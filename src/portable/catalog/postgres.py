"""
PostgreSQL, from portable builds.

There is no official binary distribution of PostgreSQL for Windows that unpacks
into a directory — the installers exist, and they install. `theseus-rs` publishes
relocatable archives built from source for every platform, which is what a tool
that must not touch the system needs.

They arrive as `.tar.gz` rather than the `.zip` everything else here uses, which
is why the unpacker handles both.
"""

from __future__ import annotations

import json

from .. import net
from . import Build, CatalogError, Offer

RELEASES_URL = "https://api.github.com/repos/theseus-rs/postgresql-binaries/releases"

#: The triple in the archive name. Windows-only for now, as the tool is.
TARGET = "x86_64-pc-windows-msvc"


def _fetch_releases() -> list[dict]:
    return json.loads(net.read_text(f"{RELEASES_URL}?per_page=30"))


def _fetch(version: str) -> dict:
    url = RELEASES_URL + ("/latest" if version == "latest" else f"/tags/{version}")

    return json.loads(net.read_text(url))


def line(version: str) -> str:
    """
    PostgreSQL's major, and here it is not a preference.

    A data directory belongs to the major that created it. 17 will not start on
    18's files and 18 will not start on 17's; moving between them means a dump
    and a restore. An update that crossed a major would leave a database that
    cannot open its own data.
    """
    return version.split(".")[0]


def available(releases: list[dict] | None = None, limit: int = 20, line: str | None = None) -> list[Offer]:
    """
    PostgreSQL builds published for this target: the newest of each major line.

    Sorted by version rather than by date, and one per major. GitHub returns
    releases in the order they were cut, and this project cuts a build for every
    supported major on the same day — so the raw order reads `18.6, 17.11, 16.15,
    15.19, 14.24, 18.4, 17.10`, with each line appearing again a few entries
    down at an older patch. Offering the same major twice, out of order, invites
    picking the older one by accident.
    """
    releases = releases if releases is not None else _fetch_releases()
    newest: dict[int, str] = {}

    for release in releases:
        version = str(release.get("tag_name") or "").lstrip("v")
        names = {str(asset.get("name")) for asset in release.get("assets", [])}

        if not version or f"postgresql-{version}-{TARGET}.tar.gz" not in names:
            continue

        major = _major(version)

        if major is not None and (major not in newest or _key(version) > _key(newest[major])):
            newest[major] = version

    return _on_line(
        [Offer(version=newest[major]) for major in sorted(newest, reverse=True)], line
    )[:limit]


def _major(version: str) -> int | None:
    head = version.split(".")[0]

    return int(head) if head.isdigit() else None


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def resolve(version: str = "latest", release: dict | None = None) -> Build:
    release = release if release is not None else _fetch(version)
    resolved = str(release.get("tag_name") or "").lstrip("v")

    if not resolved:
        raise CatalogError("The PostgreSQL release carries no tag name.")

    wanted = f"postgresql-{resolved}-{TARGET}.tar.gz"
    assets = {asset.get("name"): asset for asset in release.get("assets", [])}

    if wanted not in assets:
        offered = ", ".join(sorted(name for name in assets if TARGET in str(name)))
        raise CatalogError(
            f"PostgreSQL {resolved} has no {wanted}. For this target it offers: "
            f"{offered or 'nothing'}."
        )

    return Build(
        name="postgres",
        version=resolved,
        url=assets[wanted]["browser_download_url"],
        filename=wanted,
        # The project publishes no digests beside the archives. Recorded as
        # absent rather than glossed over — `install` says so, and a listing can
        # show which runtimes on a machine are verified and which merely arrived.
        checksum=None,
        variant=TARGET,
    )


def _on_line(offers: list[Offer], wanted: str | None) -> list[Offer]:
    """
    Narrow a listing to one release line, when one was asked for.

    Filtered here rather than by the caller so that every catalog answers the
    same question the same way — the daemon asks "what is newest on this line"
    of all six without knowing that a line means a branch for PHP, a series for
    MariaDB and a major for the rest.
    """
    if wanted is None:
        return offers

    return [offer for offer in offers if line(offer.version) == wanted]
