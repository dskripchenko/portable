"""
Extensions PHP does not ship: xdebug, redis, imagick, and the rest of PECL.

Where `extensions.py` only writes a line in a file, this downloads a DLL — and a
DLL that has to match the interpreter exactly. A PHP extension is loaded into
the running process, so four things must agree or it does not load at all:

    php_xdebug-3.5.3-8.4-nts-vs17-x64.zip
                       ^   ^   ^    ^
                       |   |   |    architecture
                       |   |   compiler
                       |   thread safety
                       PHP branch

Getting one wrong is not a slow path or a warning. `php-cgi` refuses the module
and keeps running without it, so the report arrives as a function that does not
exist. Which is why nothing here guesses: all four come from the installed
build's own recorded variant — `nts-vs17-x64` — which is the same string PECL
puts in the filename. They line up because both sides took it from the same
place, not because this tool assembled something that happens to match.

**Not xdebug.org.** Xdebug publishes Windows builds itself, and its filenames
changed shape between its own releases: 3.4.0 is
`php_xdebug-3.4.0-8.4-vs17-nts-x86_64.dll` and 3.4.1 is
`php_xdebug-3.4.1-8.4-nts-vs17-x86_64.dll` — the compiler and the thread-safety
token swapped places, and the architecture is spelled differently from
everywhere else. A parser written against either one silently finds nothing for
the other. The same builds are on PECL under the uniform name, so that is where
these come from, and this whole class of problem does not arise.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from . import acquire, net
from .catalog import Build, CatalogError

BASE = "https://downloads.php.net/~windows/pecl/releases"

#: How many versions back to look for one built against this PHP.
#:
#: An extension's newest release does not always cover the newest PHP — the
#: maintainer builds when they build. Walking back finds the newest that does,
#: and each step is a request, so it is bounded rather than open-ended.
DEPTH = 12


def versions(name: str, listing: str | None = None) -> list[str]:
    """
    Released versions, newest first, pre-releases excluded.

    `5.3.7RC1` and friends are in the listing and are not what `latest` should
    ever mean. Sorted numerically rather than as text, because otherwise 3.10
    sorts before 3.5 — which stays invisible until an extension reaches its
    tenth minor and then hands out a year-old build.
    """
    if listing is None:
        try:
            listing = net.read_text(f"{BASE}/{name}/")
        except OSError as error:
            # A misspelled name is the ordinary case here, and the raw 404 —
            # which is what this used to surface — says nothing about what was
            # asked for or where to look for the right spelling.
            raise CatalogError(
                f"PECL has nothing called {name!r} built for Windows. "
                f"The names it publishes are listed at {BASE}/.\n"
                f"Underlying error: {error}"
            ) from error

    found = set(re.findall(r'href="(\d[^"/]*)/"', listing))
    stable = [version for version in found if re.fullmatch(r"[\d.]+", version)]

    return sorted(stable, key=_key, reverse=True)


def resolve(
    name: str,
    php: str,
    variant: str,
    version: str = "latest",
    listings: dict[str, str] | None = None,
) -> Build:
    """
    The newest build of `name` made for this exact PHP.

    `php` is the branch (`8.4`) and `variant` is the build's own
    `nts-vs17-x64`. Both are read from the installed runtime rather than
    inferred here.
    """
    name = name.lower()
    available = versions(name, (listings or {}).get(""))

    if not available:
        raise CatalogError(
            f"PECL lists no released versions of {name!r} for Windows. "
            f"Check the name at {BASE}/."
        )

    wanted = available if version == "latest" else [version]
    tried = []

    for candidate in wanted[:DEPTH]:
        filename = f"php_{name}-{candidate}-{php}-{variant}.zip"
        listing = (listings or {}).get(candidate)
        listing = listing if listing is not None else _listing(name, candidate)

        if listing is None:
            tried.append(candidate)
            continue

        if f'href="{filename}"' in listing or filename in listing:
            return Build(
                name=name,
                version=candidate,
                url=f"{BASE}/{name}/{candidate}/{filename}",
                filename=filename,
                # PECL's Windows builds are published without digests. Recorded
                # as absent rather than glossed over: `install` says so, and
                # this is a DLL that will be loaded into every PHP process.
                checksum=None,
                variant=variant,
            )

        tried.append(candidate)

    raise CatalogError(
        f"No {name} build for PHP {php} {variant}.\n"
        f"Looked at {name} {', '.join(tried) or version}. The maintainer publishes "
        f"a Windows build per PHP branch when they build one, and this PHP may be "
        f"newer than the last of them.\n"
        f"Everything on offer: {BASE}/{name}/"
    )


def install(build: Build, runtime_directory: Path) -> Path:
    """
    Put the extension where this PHP will find it. Returns the installed file.

    Only the module goes in. The archive also carries a licence, a readme, an
    example ini and — for xdebug — a `.pdb` several times the size of the
    extension itself, none of which belongs in a directory PHP scans.
    """
    archive = acquire.download(build)
    ext = runtime_directory / "ext"
    ext.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as bundle:
        modules = [
            entry
            for entry in bundle.namelist()
            if entry.lower().endswith(".dll") and not entry.endswith("/")
        ]

        if not modules:
            raise CatalogError(
                f"{build.filename} contains no .dll at all. It holds: "
                f"{', '.join(bundle.namelist()[:8])}."
            )

        # The extension proper, not a library it ships beside itself: some
        # archives carry their dependencies (imagick brings the ImageMagick
        # DLLs), and the module is the one named after the extension.
        wanted = next(
            (
                entry
                for entry in modules
                if Path(entry).stem.lower() in (f"php_{build.name}", build.name)
            ),
            modules[0],
        )

        target = ext / Path(wanted).name

        with bundle.open(wanted) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)

        # Everything else in the archive that is a DLL — imagick's dependencies
        # live beside the module and PHP will not load it without them.
        for entry in modules:
            if entry == wanted:
                continue

            beside = runtime_directory / Path(entry).name

            if not beside.exists():
                with bundle.open(entry) as source, beside.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

    return target


def _listing(name: str, version: str) -> str | None:
    try:
        return net.read_text(f"{BASE}/{name}/{version}/")
    except OSError:
        # A version directory that does not answer. One missing release is not
        # a reason to stop looking at the others.
        return None


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))
