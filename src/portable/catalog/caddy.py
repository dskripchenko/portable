"""
Caddy, from the project's own releases.

Caddy rather than nginx, and the reason is nginx's own documentation: its
Windows build "uses only the `select()` and `poll()` connection processing
methods, so high performance and scalability should not be expected", and is
"considered to be a beta version". That has been the text for over a decade.

What Caddy adds beyond being a maintained Windows binary:

- an **admin API** on the loopback, so adding a site is one HTTP call rather
  than rewriting a file and hoping the reload takes;
- a **local certificate authority**, which is the whole of the HTTPS problem for
  a tool that may not touch the machine's trust store;
- `php_fastcgi` with a list of upstreams, which is exactly the shape of a pool
  of `php-cgi.exe` processes.

Checksums come from the `checksums.txt` published beside the archives — and
they are **sha512**, not sha256. Assuming otherwise produces a verification
that never matches and a failure that reads like a corrupted download.
"""

from __future__ import annotations

import json
import re

from .. import net
from . import Build, CatalogError, Offer

RELEASES_URL = "https://api.github.com/repos/caddyserver/caddy/releases"

#: Caddy names its Windows archive `caddy_<version>_windows_<arch>.zip`.
_ARCH = "amd64"


def _fetch_releases() -> list[dict]:
    return json.loads(net.read_text(f"{RELEASES_URL}?per_page=30"))


def _fetch_release(version: str) -> dict:
    url = RELEASES_URL + ("/latest" if version == "latest" else f"/tags/v{version.lstrip('v')}")

    return json.loads(net.read_text(url))


def line(version: str) -> str:
    """Caddy's major. Within it, releases are compatible by policy."""
    return version.split(".")[0]


def available(releases: list[dict] | None = None, limit: int = 20, line: str | None = None) -> list[Offer]:
    """
    Caddy releases that actually carry a Windows archive, newest first.

    The filter is not defensive tidying. GitHub's release list includes tags cut
    for other reasons, and a version offered here that has no archive behind it
    is an install that fails after the choice has been made.
    """
    releases = releases if releases is not None else _fetch_releases()
    offers = []

    for release in releases:
        version = str(release.get("tag_name") or "").lstrip("v")
        names = {asset.get("name") for asset in release.get("assets", [])}

        if version and f"caddy_{version}_windows_{_ARCH}.zip" in names:
            offers.append(
                Offer(version=version, note="pre-release" if release.get("prerelease") else "")
            )

    return _on_line(offers, line)[:limit]


def resolve(version: str = "latest", release: dict | None = None) -> Build:
    """
    A concrete Caddy build for Windows.

    `release` is injectable so resolution can be tested without the network and
    without GitHub's rate limit deciding whether the suite passes.
    """
    release = release if release is not None else _fetch_release(version)

    tag = str(release.get("tag_name") or "")
    resolved = tag.lstrip("v")

    if not resolved:
        raise CatalogError("The Caddy release carries no tag name.")

    wanted = f"caddy_{resolved}_windows_{_ARCH}.zip"
    assets = {asset.get("name"): asset for asset in release.get("assets", [])}

    if wanted not in assets:
        offered = ", ".join(sorted(name for name in assets if str(name).endswith(".zip")))
        raise CatalogError(f"Caddy {resolved} has no {wanted}. Archives offered: {offered}.")

    return Build(
        name="caddy",
        version=resolved,
        url=assets[wanted]["browser_download_url"],
        filename=wanted,
        # Filled in separately: the digest lives in a second file, and
        # fetching it during resolution would cost every caller a request for a
        # value most of them never use.
        checksum=None,
        algorithm="sha512",
        variant=f"windows-{_ARCH}",
    )


def checksum_url(version: str) -> str:
    return (
        f"https://github.com/caddyserver/caddy/releases/download/"
        f"v{version}/caddy_{version}_checksums.txt"
    )


#: Digest length in hex characters -> the algorithm that produces it. Caddy
#: publishes sha512; this table exists so that a publisher switching, or a
#: second publisher joining, is a data change rather than a code change.
_BY_LENGTH = {64: "sha256", 128: "sha512"}


def checksum_for(filename: str, checksums: str) -> tuple[str, str] | None:
    """
    One file's digest out of a `shasum`-style listing, with its algorithm.

    The algorithm is inferred from the digest's length rather than assumed:
    Caddy's file looks exactly like a sha256 listing and is not one.

    Returns None when the file is not listed, rather than raising — that means
    "this cannot be verified", and what to do about it is the caller's decision,
    not this function's.
    """
    for line in checksums.splitlines():
        match = re.match(r"^([0-9a-fA-F]{40,128})\s+\*?(\S+)$", line.strip())

        if not match or match.group(2) != filename:
            continue

        digest = match.group(1).lower()
        algorithm = _BY_LENGTH.get(len(digest))

        if algorithm is None:
            # sha1 is 40 characters and matched the pattern. Refusing beats
            # verifying against something already broken in practice.
            raise CatalogError(
                f"{filename} is listed with a {len(digest) * 4}-bit digest, "
                f"which is not one this tool will verify against."
            )

        return digest, algorithm

    return None


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
