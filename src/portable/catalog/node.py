"""
Node, from nodejs.org.

Straightforward, unusually: an official index listing every release with its
files, official archives, and a `SHASUMS256.txt` beside each version. Nothing
here has to be worked around.

The one decision worth stating is the default. `latest` here means the newest
**LTS**, not the newest release — a development environment that installs an
odd-numbered Node by default is one that produces bug reports about a runtime
the project never intended to support. The actual newest is still reachable by
naming it.
"""

from __future__ import annotations

import json
import re

from .. import net
from . import Build, CatalogError, Offer

INDEX_URL = "https://nodejs.org/dist/index.json"
DIST = "https://nodejs.org/dist"

#: Windows x64, the only target while the tool is Windows-only.
SUFFIX = "win-x64"


def _fetch_index() -> list[dict]:
    return json.loads(net.read_text(INDEX_URL))


def available(index: list[dict] | None = None, limit: int = 8) -> list[Offer]:
    """
    Node releases carrying a Windows build, newest first.

    One entry per major line, not the newest N releases. Node ships every couple
    of weeks, so a plain newest-first list is a screenful of one current major
    and the LTS lines — the ones most people actually want — fall off the end
    with their labels unread. Which would leave this listing failing at the one
    job it has: keeping somebody from picking an odd-numbered major by accident.

    Even by major it is long — the index reaches back to Node 4, released in
    2015 — so the default shows the newest eight lines, which covers every LTS
    anybody still starts a project on. The rest are reachable by asking.
    """
    index = index if index is not None else _fetch_index()
    newest: dict[int, dict] = {}

    for entry in index:
        if f"{SUFFIX}-zip" not in entry.get("files", []):
            continue

        version = entry["version"].lstrip("v")
        major = _major(version)

        if major is None:
            continue

        if major not in newest or _key(version) > _key(newest[major]["version"].lstrip("v")):
            newest[major] = entry

    return [
        Offer(
            version=newest[major]["version"].lstrip("v"),
            note=f"LTS {newest[major]['lts']}" if newest[major].get("lts") else "",
        )
        for major in sorted(newest, reverse=True)
    ][:limit]


def _major(version: str) -> int | None:
    head = version.split(".")[0]

    return int(head) if head.isdigit() else None


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def resolve(version: str = "lts", index: list[dict] | None = None) -> Build:
    """
    A concrete Node build.

    Accepts `lts` (the default), `latest`, a major (`24`) or an exact version.
    """
    index = index if index is not None else _fetch_index()
    releases = [entry for entry in index if f"{SUFFIX}-zip" in entry.get("files", [])]

    if not releases:
        raise CatalogError("The Node index lists no Windows builds at all.")

    if version in ("lts", "latest"):
        # The index is newest-first, so the first match is the newest.
        candidates = [entry for entry in releases if entry.get("lts")] if version == "lts" else releases
        entry = next(iter(candidates), None)

        if entry is None:
            raise CatalogError("The Node index lists no LTS release.")
    else:
        wanted = version.lstrip("v")
        entry = next(
            (
                candidate
                for candidate in releases
                if candidate["version"].lstrip("v") == wanted
                or candidate["version"].lstrip("v").startswith(f"{wanted}.")
            ),
            None,
        )

        if entry is None:
            recent = ", ".join(candidate["version"] for candidate in releases[:5])
            raise CatalogError(f"Node {version} is not in the index. Recent: {recent}.")

    resolved = entry["version"].lstrip("v")
    filename = f"node-v{resolved}-{SUFFIX}.zip"

    return Build(
        name="node",
        version=resolved,
        url=f"{DIST}/v{resolved}/{filename}",
        filename=filename,
        # Fetched separately: the digests live in one file per version, and
        # pulling it during resolution would cost a request every caller pays
        # for and most do not use.
        checksum=None,
        algorithm="sha256",
        variant=SUFFIX,
    )


def checksum_url(version: str) -> str:
    return f"{DIST}/v{version}/SHASUMS256.txt"


def checksum_for(filename: str, checksums: str) -> tuple[str, str] | None:
    """
    The digest for one file out of the published `SHASUMS256.txt`.

    Returns the algorithm alongside it, matching `caddy.checksum_for`. Two
    publishers with two different return shapes means the caller grows a branch
    per publisher, and the next one to join adds a third.
    """
    for line in checksums.splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)$", line.strip())

        if match and match.group(2) == filename:
            return match.group(1).lower(), "sha256"

    return None
