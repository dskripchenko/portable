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
import os
import re

from .. import net
from . import Build, CatalogError, Offer

API = "https://downloads.mariadb.org/rest-api/mariadb"

#: Where every release stays, and the way in when the API host cannot be reached.
#:
#: `downloads.mariadb.org` is unreachable from some networks — reported from
#: Windows in Russia as `WinError 10060`, a connect timeout, which no amount of
#: retrying fixes. `archive.mariadb.org` is a different host serving the same
#: releases as a plain directory listing, with `sha256sums.txt` beside each one,
#: so the fallback is verified rather than merely available.
ARCHIVE = "https://archive.mariadb.org"

#: An override for either, so a machine with a mirror of its own can say so.
ARCHIVE_VARIABLE = "PORTABLE_MARIADB_ARCHIVE"


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


def line(version: str) -> str:
    """
    MariaDB's series, `11.4`, which is how the project itself is organised.

    Same reasoning as PostgreSQL: the data directory belongs to the series, and
    crossing one is an upgrade with a procedure, not a download.
    """
    return ".".join(version.split(".")[:2])


def available(index: dict | None = None, line: str | None = None) -> list[Offer]:
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

    return _on_line(
        [
            Offer(version=release_id, note="" if status == "Stable" else status.lower())
            for release_id, status in sorted(entries, key=lambda pair: _key(pair[0]), reverse=True)
        ],
        line,
    )


def archive_base() -> str:
    return os.environ.get(ARCHIVE_VARIABLE) or ARCHIVE


def resolve(version: str = "latest", release: dict | None = None) -> Build:
    """
    A concrete MariaDB build for Windows.

    `version` is a series (`11.4`) or `latest`. An exact patch version is
    resolved through its series, since that is how the API is organised.

    When the API host cannot be reached at all, the archive answers instead.
    That is a different host rather than a retry, because a connect timeout is
    not a transient failure — it is this network not having a route, and asking
    again five times only takes longer to say so.
    """
    if release is None:
        try:
            wanted = series()[0] if version == "latest" else _series_of(version)
            release = _fetch(wanted)
        except net.Unreachable:
            return _from_archive(version)

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


def _from_archive(version: str, listing: str | None = None) -> Build:
    """
    A build from `archive.mariadb.org`, which is a directory listing.

    Every release ever made is here, so `latest` means the newest of them rather
    than the newest the API calls current — the two agree in practice and the
    archive is the only one that can be read when the other host is silent.
    """
    base = archive_base()
    listing = listing if listing is not None else net.read_text(f"{base}/")
    versions = sorted(set(_VERSIONS.findall(listing)), key=_key)

    if not versions:
        raise CatalogError(f"{base} lists no MariaDB releases at all.")

    if version == "latest":
        resolved = _newest_maintained(versions)
    else:
        wanted = _series_of(version)
        matching = [candidate for candidate in versions if _series_of(candidate) == wanted]

        if not matching:
            # Maintained series first, not simply the newest ten. The archive
            # keeps every preview and release candidate ever cut, and those
            # crowd out exactly the series somebody typing a wrong number is
            # looking for — the long-lived ones people actually run.
            counts: dict[str, int] = {}

            for found in versions:
                counts[_series_of(found)] = counts.get(_series_of(found), 0) + 1

            established = sorted(
                (name for name, count in counts.items() if count >= MAINTAINED),
                key=_key,
                reverse=True,
            )
            rest = sorted(
                (name for name, count in counts.items() if count < MAINTAINED),
                key=_key,
                reverse=True,
            )

            raise CatalogError(
                f"{base} lists no MariaDB {version}.\n"
                f"Maintained series: {', '.join(established) or 'none'}.\n"
                f"Also there, shorter-lived: {', '.join(rest[:8])}."
            )

        resolved = matching[-1]
    filename = f"mariadb-{resolved}-winx64.zip"
    directory = f"{base}/mariadb-{resolved}/winx64-packages"

    return Build(
        name="mariadb",
        version=resolved,
        url=f"{directory}/{filename}",
        filename=filename,
        checksum=_archive_checksum(directory, filename),
        algorithm="sha256",
        variant="winx64",
    )


#: Patch releases a series needs before `latest` will choose it from the archive.
#:
#: The API marks each series Stable, RC or Preview and the archive does not, so
#: something has to stand in for that mark. Maintenance history does: a series
#: only reaches its fifth patch after about a year of being looked after, which
#: is what "stable enough to be the default" means in practice. A preview with
#: one release and a release candidate with two are excluded by it today, and
#: would be excluded by it in a year.
#:
#: It errs towards an older series than the API would name, which is the right
#: direction for something chosen without being asked.
MAINTAINED = 5

_VERSIONS = re.compile(r"mariadb-(\d+\.\d+\.\d+)/")


def _newest_maintained(versions: list[str]) -> str:
    """The newest release of the newest series that has been maintained."""
    counts: dict[str, list[str]] = {}

    for candidate in versions:
        counts.setdefault(_series_of(candidate), []).append(candidate)

    established = [
        series_name
        for series_name, found in counts.items()
        if len(found) >= MAINTAINED
    ]

    if not established:
        return versions[-1]

    newest = max(established, key=_key)

    return max(counts[newest], key=_key)


def _archive_checksum(directory: str, filename: str) -> str | None:
    """
    The digest from `sha256sums.txt` beside the archive.

    Its entries are written `./mariadb-11.8.9-winx64.zip`, with the leading
    `./` — matching on the bare name finds nothing and quietly downgrades a
    verified install to an unverified one.
    """
    try:
        sums = net.read_text(f"{directory}/sha256sums.txt")
    except (OSError, net.Unreachable):
        return None

    for row in sums.splitlines():
        parts = row.split()

        if len(parts) == 2 and parts[1].lstrip("./") == filename:
            return parts[0].lower()

    return None


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
