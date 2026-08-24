"""
PHP for Windows, from the index php.net publishes itself.

Windows is the platform where PHP is easy to obtain: official builds, a
machine-readable index, checksums included. On macOS and Linux none of that
exists — which is one of the reasons this tool starts with Windows.

The build we want is always **non-thread-safe**. PHP's FastCGI SAPI runs one
request per process, so thread safety buys nothing and costs perhaps a fifth of
the performance. The thread-safe builds exist for Apache with a threaded MPM,
which is not how anything here runs.
"""

from __future__ import annotations

import json
import re

from .. import net
from . import Build, CatalogError, Offer

INDEX_URL = "https://downloads.php.net/~windows/releases/releases.json"
DOWNLOAD_BASE = "https://downloads.php.net/~windows/releases"

#: The compiler each branch is built with. PHP for Windows pins one per branch,
#: and picking the wrong one is not a slow path but an unloadable extension:
#: a `php_redis.dll` built for vs16 will not load into a vs17 binary.
#:
#: Read from the index rather than hardcoded — the branch that introduces vs18
#: should not require a release of this tool.
_ARCH = "x64"


def _fetch_index(url: str = INDEX_URL) -> dict:
    return json.loads(net.read_text(url))


def branches(index: dict | None = None) -> list[str]:
    """Every branch the index offers, newest first: `['8.5', '8.4', ...]`."""
    index = index if index is not None else _fetch_index()

    return sorted(
        (key for key in index if _looks_like_branch(key)),
        key=_version_key,
        reverse=True,
    )


ARCHIVE_URL = "https://windows.php.net/downloads/releases/archives/"

#: What an archived Windows build is called.
#:
#: The compiler token appears in both cases — `vc15` and `VC15` are both in the
#: listing, for different releases of the same branch — so matching is
#: case-insensitive while the filename is used exactly as published. Getting
#: that backwards produces a URL that 404s for half the versions on offer.
_ARCHIVED = re.compile(
    r"php-(?P<version>\d+\.\d+\.\d+)-nts-Win32-(?P<compiler>[A-Za-z]+\d+)-" + _ARCH + r"\.zip",
    re.IGNORECASE,
)


def archived(listing: str | None = None) -> dict[str, str]:
    """
    Every superseded release still downloadable, as version -> filename.

    php.net's index lists the current release of each branch and nothing else;
    everything it supersedes moves here and stays. That is the difference
    between "PHP 8.3" and "the PHP 8.3.20 this project was pinned to eighteen
    months ago", and only the second one is ever asked for by name.

    Non-thread-safe x64 only, matching what `resolve` installs from the index —
    the archive also carries thread-safe builds, x86, sources and debug packs,
    and offering those would be offering things this tool cannot run.
    """
    listing = listing if listing is not None else net.read_text(ARCHIVE_URL)

    return {
        match.group("version"): match.group(0)
        for match in _ARCHIVED.finditer(listing)
    }


def available(
    index: dict | None = None,
    branch: str | None = None,
    archive: str | None = None,
) -> list[Offer]:
    """
    What can be installed, newest first.

    Without a branch: the current release of each, which is all php.net's index
    holds — it lists one per branch and moves everything it supersedes to the
    archive.

    With a branch: that branch's current release **and** every superseded patch
    of it still downloadable. Which is the question actually asked — not "what
    PHP versions exist" but "can I still get the 8.3.20 this project is pinned
    to". Only per branch, because the archive holds three hundred-odd builds
    reaching back to 5.2 and listing them all answers nobody's question.
    """
    index = index if index is not None else _fetch_index()
    current = [
        Offer(version=str(index[name].get("version") or name), note=f"branch {name}")
        for name in branches(index)
    ]

    if branch is None:
        return current

    live = [offer for offer in current if offer.version.startswith(f"{branch}.")]
    superseded = [
        Offer(version=version, note="archived, no checksum published")
        for version in archived(archive)
        if version.rsplit(".", 1)[0] == branch
        and version not in {offer.version for offer in live}
    ]

    return live + sorted(superseded, key=lambda offer: _version_key(offer.version), reverse=True)


def resolve(
    version: str = "latest",
    index: dict | None = None,
    archive: str | None = None,
) -> Build:
    """
    A concrete build for a requested version.

    Accepts a branch (`8.4`), an exact version (`8.4.24`) or `latest`.

    The index holds one patch release per branch. A version it does not have is
    looked for in the archive php.net keeps everything else in — so `8.3.20`
    installs, eighteen months after being superseded, which is the version a
    project pinned to it actually needs.

    What never happens is a silent substitution. Somebody naming `8.4.1` has a
    reason, and handing back `8.4.24` without saying so is the worst of the
    available answers.
    """
    index = index if index is not None else _fetch_index()
    available = branches(index)

    if not available:
        raise CatalogError("The PHP index lists no branches at all.")

    if version == "latest":
        branch = available[0]
    elif version in index and _looks_like_branch(version):
        branch = version
    else:
        # An exact version: find the branch whose current release matches.
        branch = next(
            (
                name
                for name in available
                if str(index[name].get("version", "")) == version
            ),
            "",
        )

        if not branch:
            return _from_archive(version, available, index, archive)

    entry = index[branch]
    resolved = str(entry.get("version") or "")
    variant = _variant_of(entry, branch)
    archive = entry[variant]["zip"]

    return Build(
        name="php",
        version=resolved,
        url=f"{DOWNLOAD_BASE}/{archive['path']}",
        filename=archive["path"],
        checksum=archive.get("sha256"),
        algorithm="sha256",
        variant=variant,
    )


def _from_archive(
    version: str, branches: list[str], index: dict, archive: str | None = None
) -> Build:
    """
    A superseded release, from the archive php.net keeps them in.

    Reached only when the index does not have it, so the common case costs
    nothing: the archive listing is a megabyte of HTML and is fetched when
    somebody names a version that has been replaced.

    These come **unverified**, and that is not an oversight to be tidied away
    later. php.net publishes digests for current releases and none for archived
    Windows builds: `archives/sha1sum.txt` covers twenty-six files from the 5.2
    era, and the sha256 list covers only what is current. The Build says so, and
    `install` reports it.
    """
    listing = archived(archive)
    filename = listing.get(version)

    if filename is None:
        current = ", ".join(f"{name} ({index[name].get('version')})" for name in branches)
        near = sorted(
            (
                candidate
                for candidate in listing
                if candidate.rsplit(".", 1)[0] == version.rsplit(".", 1)[0]
            ),
            key=_version_key,
            reverse=True,
        )
        raise CatalogError(
            f"PHP {version} is neither a current release nor in the archive.\n"
            f"Current: {current}.\n"
            + (f"Archived in {version.rsplit('.', 1)[0]}: {', '.join(near[:12])}." if near else "")
        )

    compiler = _ARCHIVED.match(filename).group("compiler").lower()

    return Build(
        name="php",
        version=version,
        url=f"{ARCHIVE_URL}{filename}",
        filename=filename,
        # php.net publishes no digest for archived Windows builds. Recorded as
        # absent rather than glossed over: this is an interpreter that is about
        # to run everything on the machine.
        checksum=None,
        # Lowercased so that it matches what PECL puts in its filenames, and so
        # that `vc15` and `VC15` — both of which are in the listing — are one
        # thing rather than two.
        variant=f"nts-{compiler}-{_ARCH}",
    )


def _variant_of(entry: dict, branch: str) -> str:
    """
    The non-thread-safe x64 flavour of whatever compiler this branch uses.

    The compiler is discovered rather than assumed: branches move from vs16 to
    vs17 and will move again, and a hardcoded list would make that a release of
    this tool instead of a fact about the index.
    """
    candidates = sorted(
        (key for key in entry if key.startswith("nts-") and key.endswith(f"-{_ARCH}")),
        reverse=True,
    )

    if not candidates:
        offered = ", ".join(sorted(key for key in entry if "-" in key))
        raise CatalogError(
            f"PHP {branch} offers no non-thread-safe {_ARCH} build. Offered: {offered}."
        )

    return candidates[0]


def _looks_like_branch(key: str) -> bool:
    """`8.4` yes; `source`, `test_pack` no."""
    parts = key.split(".")

    return len(parts) == 2 and all(part.isdigit() for part in parts)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
