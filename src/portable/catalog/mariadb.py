"""
MariaDB, from the project's own REST API.

Two details of that API decide the code below.

**Downloads are advertised over `http`.** The index itself comes over `https`,
and the digest comes with it, so an archive fetched over plain HTTP is still
verifiable — but there is no reason to accept the plaintext transfer when the
same path answers over TLS. The URL is upgraded, and the digest remains the
thing that actually decides.

That matters more than usual here: the `https` URL redirects to a community
mirror. The bytes come from a third party, and the checksum is what makes that
acceptable.

**The digest is under `sha256sum`,** not `sha256`. Reading the wrong key yields
None, which reads as "the publisher offers no checksum" — a silent downgrade
from verified to unverified.
"""

from __future__ import annotations

import json

from .. import net
from . import Build, CatalogError, Offer

API = "https://downloads.mariadb.org/rest-api/mariadb"


def _fetch(series: str) -> dict:
    return json.loads(net.read_text(f"{API}/{series}/"))


def series(index: dict | None = None) -> list[str]:
    """Stable release series, newest first."""
    index = index if index is not None else json.loads(net.read_text(f"{API}/"))

    stable = [
        entry["release_id"]
        for entry in index.get("major_releases", [])
        if entry.get("release_status") == "Stable"
    ]

    return sorted(stable, key=_key, reverse=True)


def available(index: dict | None = None) -> list[Offer]:
    """
    MariaDB series, newest first — not individual patch releases.

    That is how the API is organised and how the project talks about itself, so
    a listing of patch versions would be this tool inventing a shape the
    publisher does not use.

    Unstable series are listed and marked rather than hidden. They install
    perfectly well, somebody occasionally wants one, and a listing that silently
    omits what is plainly on the download page reads as out of date.
    """
    index = index if index is not None else json.loads(net.read_text(f"{API}/"))
    entries = [
        (str(entry.get("release_id")), str(entry.get("release_status") or ""))
        for entry in index.get("major_releases", [])
        if entry.get("release_id")
    ]

    return [
        Offer(version=release_id, note="" if status == "Stable" else status.lower())
        for release_id, status in sorted(entries, key=lambda pair: _key(pair[0]), reverse=True)
    ]


def resolve(version: str = "latest", release: dict | None = None) -> Build:
    """
    A concrete MariaDB build for Windows.

    `version` is a series (`11.4`) or `latest`. An exact patch version is
    resolved through its series, since that is how the API is organised.
    """
    if release is None:
        wanted = series()[0] if version == "latest" else _series_of(version)
        release = _fetch(wanted)

    entry = next(iter(release.get("releases", {}).values()), None)

    if entry is None:
        raise CatalogError(f"MariaDB {version} has no releases in the index.")

    resolved = str(entry.get("release_id") or "")
    archive = next(
        (
            candidate
            for candidate in entry.get("files", [])
            if str(candidate.get("file_name", "")).endswith("winx64.zip")
            and "debug" not in str(candidate.get("file_name", ""))
        ),
        None,
    )

    if archive is None:
        offered = ", ".join(sorted(str(f.get("file_name")) for f in entry.get("files", [])))
        raise CatalogError(f"MariaDB {resolved} offers no Windows zip. Files: {offered}.")

    return Build(
        name="mariadb",
        version=resolved,
        url=_https(str(archive["file_download_url"])),
        filename=str(archive["file_name"]),
        checksum=(archive.get("checksum") or {}).get("sha256sum"),
        algorithm="sha256",
        variant="winx64",
    )


def _https(url: str) -> str:
    """The same URL over TLS. See the module docstring for why it is not already."""
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def _series_of(version: str) -> str:
    parts = version.split(".")

    if len(parts) < 2:
        raise CatalogError(f"{version!r} is not a MariaDB version or series.")

    return ".".join(parts[:2])


def _key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)
