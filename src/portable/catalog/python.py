"""
Python for Windows, from the index python.org publishes for its own installer.

`index-windows.json` is what the Python install manager reads, and it is the
closest thing Python has to php.net's `releases.json`: machine-readable, kept by
the publisher, every package listed with its URL and a sha256. The packages are
complete Windows layouts — `python.exe`, the standard library, pip already in
`site-packages`, the Visual C++ runtime beside the interpreter — so an unpacked
one runs as it is, with nothing installed into the system.

The index is **paged**. The first page holds the current releases, `next` leads
to older ones, and the last page reaches back to 2.7. That is the same shape as
php.net's index and its archive, and it is read the same way: the first page
answers the common question, and the rest are fetched only when somebody names a
version the first page does not have.

The oldest page lists NuGet packages rather than zips, and without a digest.
They install, and are reported as unverified — the same treatment, for the same
reason, as an archived PHP: there is nothing published to check them against.

Only `pythoncore-X.Y-64` is offered. The index also carries 32-bit and ARM64
builds, the embeddable distribution (which ignores `site-packages` and cannot run
pip), the test suite as a package, and the free-threaded `t` builds — none of
which is what a project's `requirements.txt` was written against.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from urllib.parse import urljoin

from .. import net
from . import Build, CatalogError, Offer

INDEX_URL = "https://www.python.org/ftp/python/index-windows.json"

#: The regular CPython build for x64, and nothing else from the index.
_CORE = re.compile(r"^pythoncore-(?P<line>\d+\.\d+)-64$")

#: Every entry of one of those is this build, so it is the variant.
VARIANT = "amd64"

#: How many pages to follow before deciding the index is not ending. Three exist;
#: the bound is there so that a `next` pointing at itself is an error rather
#: than a daemon that never answers.
_MAX_PAGES = 16


def _fetch_page(url: str) -> dict:
    return json.loads(net.read_text(url))


def _pages(pages: Iterable[dict] | None = None) -> Iterator[dict]:
    """
    The index, one page at a time, fetched only as far as it is read.

    Given pages are used as they are — which is how the tests read captured
    ones without the network doing any work.
    """
    if pages is not None:
        yield from pages

        return

    url: str | None = INDEX_URL
    seen: set[str] = set()

    while url and url not in seen and len(seen) < _MAX_PAGES:
        seen.add(url)
        page = _fetch_page(url)

        yield page

        following = page.get("next")
        url = urljoin(url, following) if following else None


def _entries(page: dict) -> list[dict]:
    """The regular x64 builds on one page, as the publisher listed them."""
    return [
        entry
        for entry in page.get("versions", [])
        if _CORE.match(str(entry.get("id", ""))) and entry.get("url")
    ]


def line(version: str) -> str:
    """
    The release line an update stays inside: `3.13.17` -> `3.13`.

    A Python minor is a language version. Packages ship wheels per minor, so a
    virtual environment made by 3.13 does not run under 3.14 — an update that
    crossed one would break every project that pinned nothing.
    """
    return ".".join(version.split(".")[:2])


def is_prerelease(version: str) -> bool:
    """`3.15.0rc3`, `3.16.0a2` — anything that is not a final release."""
    return bool(re.search(r"[a-z]", version))


def available(
    pages: Iterable[dict] | None = None,
    line: str | None = None,
    limit: int = 8,
) -> list[Offer]:
    """
    What can be installed, newest first.

    Without a line: the newest release of each line on the first page — the
    lines python.org currently ships Windows builds for. A line with nothing but
    pre-releases is listed as one, so that `3.16.0a2` is never mistaken for
    something to start a project on.

    With one: every release of that line the index still has, however far back
    that means reading. Which is the question being asked — not "what Pythons
    exist" but "can I still get the 3.11.4 this project was built against".
    """
    if line is not None:
        found = _of_line(_pages(pages), line)

        return [_offer(entry) for entry in found]

    first = next(iter(_pages(pages)), {"versions": []})
    newest: dict[str, dict] = {}

    for entry in _entries(first):
        name = _line_of(entry)
        current = newest.get(name)

        if current is None or _better(entry, current):
            newest[name] = entry

    return [
        _offer(newest[name])
        for name in sorted(newest, key=_version_key, reverse=True)
    ][:limit]


def _better(entry: dict, than: dict) -> bool:
    """A final release beats any pre-release; otherwise the newer one wins."""
    mine, theirs = _version(entry), _version(than)

    if is_prerelease(mine) != is_prerelease(theirs):
        return not is_prerelease(mine)

    return _version_key(mine) > _version_key(theirs)


def _offer(entry: dict) -> Offer:
    version = _version(entry)
    notes = []

    if is_prerelease(version):
        notes.append("pre-release")

    if not entry.get("hash", {}).get("sha256"):
        notes.append("no checksum published")

    return Offer(version=version, note=", ".join(notes))


def _of_line(pages: Iterable[dict], wanted: str) -> list[dict]:
    """Every release of one line, across every page, newest first, once each."""
    found: dict[str, dict] = {}

    for page in pages:
        for entry in _entries(page):
            if _line_of(entry) == wanted:
                found.setdefault(_version(entry), entry)

    return sorted(found.values(), key=lambda entry: _version_key(_version(entry)), reverse=True)


def resolve(version: str = "latest", pages: Iterable[dict] | None = None) -> Build:
    """
    A concrete build for a requested version.

    Accepts `latest`, a line (`3.13`) or an exact version (`3.13.5`, `3.15.0rc3`).

    `latest` and a line mean the newest **final** release. A pre-release is
    installed only when named exactly: a line that has nothing else — `3.16`
    while it is in alpha — is refused with what it does have, rather than
    quietly answered with an alpha.
    """
    wanted = version.strip().lower()
    seen: list[dict] = []

    for page in _pages(pages):
        entries = _entries(page)
        seen.extend(entries)

        if wanted == "latest":
            finals = [entry for entry in entries if not is_prerelease(_version(entry))]

            if finals:
                return _build(max(finals, key=lambda entry: _version_key(_version(entry))))

            continue

        exact = [entry for entry in entries if _version(entry) == wanted]

        if exact:
            return _build(exact[0])

        if _looks_like_line(wanted):
            # The index is newest-first across pages, so the first page that has
            # a final release of this line has its newest one.
            finals = [
                entry
                for entry in entries
                if _line_of(entry) == wanted and not is_prerelease(_version(entry))
            ]

            if finals:
                return _build(max(finals, key=lambda entry: _version_key(_version(entry))))

    if not seen:
        raise CatalogError("The Python index lists no Windows x64 builds at all.")

    if wanted == "latest":
        raise CatalogError("The Python index lists no final release, only pre-releases.")

    asked = wanted if _looks_like_line(wanted) else line(wanted)
    of_line = sorted(
        {_version(entry) for entry in seen if _line_of(entry) == asked},
        key=_version_key,
        reverse=True,
    )

    if of_line:
        if _looks_like_line(wanted):
            raise CatalogError(
                f"Python {wanted} has no final release yet, only "
                f"{', '.join(of_line[:6])}. Name one exactly to install it."
            )

        raise CatalogError(
            f"Python {version} is not in python.org's index. "
            f"{asked} has: {', '.join(of_line[:12])}."
        )

    lines = sorted({_line_of(entry) for entry in seen}, key=_version_key, reverse=True)

    raise CatalogError(
        f"Python {version} is not in python.org's index. Lines on offer: {', '.join(lines)}."
    )


def _build(entry: dict) -> Build:
    url = str(entry["url"])
    filename = url.rsplit("/", 1)[-1]

    return Build(
        name="python",
        version=_version(entry),
        url=url,
        filename=filename,
        # Absent for the NuGet packages on the oldest page, and recorded as
        # absent: what is about to be unpacked is an interpreter.
        checksum=(entry.get("hash") or {}).get("sha256"),
        algorithm="sha256",
        variant=VARIANT,
    )


def _version(entry: dict) -> str:
    return str(entry.get("sort-version", "")).lower()


def _line_of(entry: dict) -> str:
    match = _CORE.match(str(entry.get("id", "")))

    return match.group("line") if match else line(_version(entry))


def _looks_like_line(version: str) -> bool:
    parts = version.split(".")

    return len(parts) == 2 and all(part.isdigit() for part in parts)


#: Pre-release stages in the order they come: alpha, beta, candidate, final.
_STAGES = {"a": 0, "b": 1, "rc": 2}


def _version_key(version: str) -> tuple[int, ...]:
    """`3.15.0rc3` -> (3, 15, 0, 2, 3); a final release sorts after its candidates."""
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?", version)

    if not match:
        return (0,)

    major, minor, patch, stage, number = match.groups()

    return (
        int(major),
        int(minor),
        int(patch or 0),
        _STAGES[stage] if stage else 3,
        int(number or 0),
    )
