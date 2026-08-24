"""
Getting a build onto disk and unpacked, verifiably.

Everything downloaded here is executed afterwards, which sets the standard: a
build with a publisher's checksum is verified against it, and a mismatch removes
the file rather than keeping it around to be run by the next thing that looks.

Archives are kept after unpacking. Re-installing a version then costs nothing,
and a machine that has been offline since Friday can still add the PHP it
already fetched on Thursday.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import net, paths
from .catalog import Build

#: Read in chunks: a PHP archive is thirty-odd megabytes and a Postgres one is
#: far larger. Holding either in memory to hash it is avoidable.
_CHUNK = 1024 * 1024


class VerificationError(RuntimeError):
    """What arrived is not what the publisher said it would be."""


@dataclass
class Acquired:
    """Where a build ended up."""

    build: Build
    archive: Path
    directory: Path
    verified: bool
    """
    False only when the publisher offered no checksum at all.

    Kept as a fact rather than dropped, so that a listing can say which runtimes
    on a machine are known-good and which merely arrived without incident.
    """


def digest(path: Path, algorithm: str = "sha256") -> str:
    """The file's hash, lowercase hex."""
    hasher = hashlib.new(algorithm)

    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)

    return hasher.hexdigest()


def download(
    build: Build,
    destination: Path | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """
    Fetch the archive, verify it, return where it landed.

    A file already present and matching the expected digest is not fetched
    again. A file already present and **not** matching is replaced: it is either
    a truncated earlier attempt or something worse, and neither is worth keeping.
    """
    destination = destination or paths.downloads()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / build.filename

    if target.exists() and build.checksum:
        if digest(target, build.algorithm) == build.checksum.lower():
            return target

        target.unlink()

    # Written beside the target and moved into place, so that an interrupted
    # download never looks like a complete one.
    partial = target.with_suffix(target.suffix + ".part")

    with net.open_url(build.url, timeout=300) as response:
        total = response.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        seen = 0

        with partial.open("wb") as handle:
            while chunk := response.read(_CHUNK):
                handle.write(chunk)
                seen += len(chunk)

                if on_progress:
                    on_progress(seen, total)

    if build.checksum:
        actual = digest(partial, build.algorithm)

        if actual != build.checksum.lower():
            partial.unlink(missing_ok=True)
            raise VerificationError(
                f"{build.filename} does not match the {build.algorithm} the "
                f"publisher listed.\n"
                f"  expected: {build.checksum.lower()}\n"
                f"  received: {actual}\n"
                f"The file has been discarded."
            )

    partial.replace(target)

    return target


def unpack(build: Build, archive: Path, into: Path | None = None) -> Path:
    """
    Extract into `runtimes/<name>/<slug>/`, flattening the archive's own wrapper.

    Publishers disagree about wrappers: the PHP archive is a bare tree of files,
    Caddy's holds them at the root too, and others nest everything one directory
    deep. Flattening a single top-level directory makes all three land the same
    way, so nothing downstream has to know which kind it was handed.
    """
    into = into or (paths.runtimes() / build.name / build.slug)

    if into.exists():
        shutil.rmtree(into)

    staging = into.with_name(into.name + ".unpacking")

    if staging.exists():
        shutil.rmtree(staging)

    staging.mkdir(parents=True)

    with zipfile.ZipFile(archive) as bundle:
        _extract_safely(bundle, staging)

    entries = list(staging.iterdir())

    if len(entries) == 1 and entries[0].is_dir():
        entries[0].replace(into)
        shutil.rmtree(staging, ignore_errors=True)
    else:
        staging.replace(into)

    return into


def _extract_safely(bundle: zipfile.ZipFile, into: Path) -> None:
    """
    Extract, refusing paths that climb out of the destination.

    `zipfile.extractall` sanitises names, but only as an implementation detail
    and only for absolute paths and `..` — an archive is untrusted input that
    this tool then runs, so the check is made here and made explicit.
    """
    root = into.resolve()

    for member in bundle.infolist():
        target = (root / member.filename).resolve()

        if not target.is_relative_to(root):
            raise VerificationError(
                f"The archive contains an entry that would be written outside "
                f"its directory: {member.filename!r}. It has not been unpacked."
            )

    bundle.extractall(into)


def install(build: Build, on_progress: Callable[[int, int | None], None] | None = None) -> Acquired:
    """Download, verify and unpack in one step."""
    archive = download(build, on_progress=on_progress)
    directory = unpack(build, archive)

    return Acquired(
        build=build,
        archive=archive,
        directory=directory,
        verified=build.checksum is not None,
    )
