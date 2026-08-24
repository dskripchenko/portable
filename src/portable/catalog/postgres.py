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
from . import Build, CatalogError

RELEASES_URL = "https://api.github.com/repos/theseus-rs/postgresql-binaries/releases"

#: The triple in the archive name. Windows-only for now, as the tool is.
TARGET = "x86_64-pc-windows-msvc"


def _fetch(version: str) -> dict:
    url = RELEASES_URL + ("/latest" if version == "latest" else f"/tags/{version}")

    return json.loads(net.read_text(url))


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
