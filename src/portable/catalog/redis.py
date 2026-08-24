"""
Redis for Windows, from a third-party rebuild.

Redis does not support Windows and publishes nothing for it. What exists:

- **`redis-windows`** — current, tracking upstream within days, built through
  msys2 or cygwin, which means an emulation DLL travels with it. Used here.
- **`tporadowski/redis`** — the fork most often recommended, last released in
  February 2022 at Redis 5. Three majors behind and not moving.
- **Garnet** — Microsoft's RESP-compatible server, MIT, native. A real option
  and a different one: compatible is not identical, and a local environment that
  behaves unlike production is worth less than a slower one that does not.

So: a rebuild by people this project does not know, which is worth saying out
loud rather than burying. Two things follow. The version is pinned, so an
install is repeatable. And nothing is verified — the release carries no digests
at all — which `install` reports rather than passes over.

Anyone who would rather not: `portable install redis --from <path>` takes a
binary they chose themselves.
"""

from __future__ import annotations

import json
import re

from .. import net
from . import Build, CatalogError, Offer

RELEASES_URL = "https://api.github.com/repos/redis-windows/redis-windows/releases"

#: msys2 over cygwin: a smaller emulation layer and the one upstream's own CI
#: uses. Neither is native, and for a development machine neither needs to be.
FLAVOUR = "msys2"


def _fetch_releases() -> list[dict]:
    return json.loads(net.read_text(f"{RELEASES_URL}?per_page=30"))


def _fetch(version: str) -> dict:
    url = RELEASES_URL + ("/latest" if version == "latest" else f"/tags/{version}")

    return json.loads(net.read_text(url))


def available(releases: list[dict] | None = None, limit: int = 20) -> list[Offer]:
    """
    Redis versions this rebuild offers, newest first.

    The version reported is the upstream one, not the rebuild's tag. Somebody
    choosing between these is choosing a Redis, and the packaging is our problem
    rather than theirs.
    """
    releases = releases if releases is not None else _fetch_releases()
    offers = []

    for release in releases:
        tag = str(release.get("tag_name") or "")
        usable = any(
            name and FLAVOUR in name and name.endswith(".zip") and "with-Service" not in name
            for name in (asset.get("name") for asset in release.get("assets", []))
        )

        if tag and usable:
            offers.append(Offer(version=_upstream_version(tag)))

    return offers[:limit]


def resolve(version: str = "latest", release: dict | None = None) -> Build:
    release = release if release is not None else _fetch(version)
    resolved = str(release.get("tag_name") or "").lstrip("v")

    if not resolved:
        raise CatalogError("The Redis release carries no tag name.")

    assets = {asset.get("name"): asset for asset in release.get("assets", [])}
    wanted = next(
        (
            name
            for name in assets
            # Without the service variant: this tool installs no services, by
            # design, and the archive that bundles one differs only by carrying
            # something that would need administrator rights to use.
            if name
            and FLAVOUR in name
            and name.endswith(".zip")
            and "with-Service" not in name
        ),
        None,
    )

    if wanted is None:
        offered = ", ".join(sorted(str(name) for name in assets))
        raise CatalogError(f"Redis {resolved} has no {FLAVOUR} archive. Offered: {offered}.")

    return Build(
        name="redis",
        version=_upstream_version(resolved),
        url=assets[wanted]["browser_download_url"],
        filename=str(wanted),
        # The rebuild publishes none. Recorded as absent, and reported.
        checksum=None,
        variant=FLAVOUR,
    )


def _upstream_version(tag: str) -> str:
    """The Redis version out of the rebuild's tag, which mirrors it."""
    found = re.search(r"(\d+\.\d+\.\d+)", tag)

    return found.group(1) if found else tag
