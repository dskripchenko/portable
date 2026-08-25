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
import http.client
import shutil
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
    _fetch(build, partial, on_progress)

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


#: How many times a transfer that breaks off is resumed before giving up.
RESUMES = 5


def _fetch(
    build: Build,
    partial: Path,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> None:
    """
    Fill `partial` with the archive, continuing where a broken transfer stopped.

    Reconnecting is not enough on its own. These are thirty- and ninety-megabyte
    archives, and a connection that drops eight times out of ten will drop
    partway through — so starting again from nothing means never finishing,
    however many attempts are allowed. Asking for the rest is what turns a bad
    network into a slow one.

    A server that ignores `Range` answers 200 with the whole file, and then what
    is already on disk has to be thrown away: appending to it would produce an
    archive with the first bytes twice.
    """
    failures: list[str] = []

    for attempt in range(1, RESUMES + 1):
        have = partial.stat().st_size if partial.exists() else 0

        try:
            with net.open_url(build.url, timeout=300, offset=have) as response:
                resuming = response.status == 206
                total = response.headers.get("Content-Length")
                total = int(total) if total and total.isdigit() else None

                if total is not None and resuming:
                    total += have

                seen = have if resuming else 0

                with partial.open("ab" if resuming else "wb") as handle:
                    while chunk := response.read(_CHUNK):
                        handle.write(chunk)
                        seen += len(chunk)

                        if on_progress:
                            on_progress(seen, total)

            # A transfer can stop early and look like it finished: the socket
            # closes, `read` returns nothing, and the loop ends contentedly on a
            # file missing its last thirty megabytes. Nothing downstream would
            # notice for postgres, redis or an archived PHP, none of which the
            # publisher gives a checksum for — the archive would simply be
            # unpacked, short.
            if total is not None and seen < total:
                raise ShortTransfer(
                    f"the connection ended after {seen} of {total} bytes"
                )

            return
        except _INTERRUPTED as error:
            failures.append(f"{type(error).__name__}: {error}")

            if attempt == RESUMES or (partial.exists() and partial.stat().st_size == have):
                # Either out of attempts, or the last one moved nothing at all —
                # and resuming a transfer that makes no progress is a loop, not
                # a recovery.
                raise TransferFailed(
                    f"{build.filename} could not be downloaded in one piece.\n"
                    + "\n".join(f"  {failure}" for failure in dict.fromkeys(failures))
                    + f"\n{_kept(partial)}"
                ) from error


def _kept(partial: Path) -> str:
    if not partial.exists():
        return "Nothing was kept."

    return (
        f"{partial.stat().st_size // 1048576} MB is kept at {partial.name} and the "
        f"next attempt continues from there."
    )


class TransferFailed(RuntimeError):
    """The archive could not be fetched, after resuming."""


class ShortTransfer(OSError):
    """The body ended before the length the server promised."""


#: Failures that resuming can answer, which is not all of them.
#:
#: `net.Unreachable` is deliberately absent. It already means "tried five times
#: and gave up", so catching it here and going round again turned five attempts
#: into twenty-five — each waiting out its own connect timeout. Reported from a
#: Windows machine watching `install mariadb` cycle 1-to-5 four times over.
#:
#: What is left is a transfer that started and broke, which is exactly what
#: resuming is for.
_INTERRUPTED = (OSError, http.client.HTTPException)


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
    _extract(archive, staging)

    entries = list(staging.iterdir())

    if len(entries) == 1 and entries[0].is_dir():
        entries[0].replace(into)
        shutil.rmtree(staging, ignore_errors=True)
    else:
        staging.replace(into)

    return into


def _extract(archive: Path, into: Path) -> None:
    """
    Unpack a zip or a gzipped tar, refusing entries that escape.

    Both, because publishers disagree and the disagreement is not negotiable:
    PHP, Caddy and MariaDB ship `.zip`, the portable PostgreSQL builds ship
    `.tar.gz`. Guessing from the extension rather than from content, because the
    extension is what the publisher's index promised and a mismatch is itself
    worth failing on.
    """
    name = archive.name.lower()

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            _refuse_escapes(bundle.namelist(), into, archive)
            bundle.extractall(into)

        return

    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as bundle:
            names = bundle.getnames()
            _refuse_escapes(names, into, archive)

            # Links are refused outright rather than sanitised. A symlink inside
            # an archive can point anywhere once unpacked, and nothing this tool
            # downloads has a reason to contain one.
            for member in bundle.getmembers():
                if member.issym() or member.islnk():
                    raise VerificationError(
                        f"{archive.name} contains a link ({member.name!r}). "
                        f"It has not been unpacked."
                    )

            bundle.extractall(into)

        return

    raise VerificationError(
        f"{archive.name} is neither a zip nor a gzipped tar, and nothing here "
        f"knows how to unpack it."
    )


def _refuse_escapes(names: list[str], into: Path, archive: Path) -> None:
    """
    Refuse any entry that would be written outside [into].

    The standard extractors sanitise names, but as an implementation detail and
    only for the obvious cases. An archive is untrusted input that this tool then
    executes, so the check is made here and made explicit.
    """
    root = into.resolve()

    for name in names:
        target = (root / name).resolve()

        if not target.is_relative_to(root):
            raise VerificationError(
                f"{archive.name} contains an entry that would be written outside "
                f"its directory: {name!r}. It has not been unpacked."
            )


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
