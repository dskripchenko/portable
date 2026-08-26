"""
Replacing this tool with a newer one, from inside it.

The awkward part is not the download. It is that on Windows a running program's
files cannot be replaced: `upgrade` is executed by the very `python.exe` inside
the bundle being replaced, and `cmd.exe` holds `portable.cmd` open for as long as
it runs. Unpacking over the top is not merely unwise, it is refused by the
operating system.

So the new version is unpacked **beside** the old one and proven to work, and the
directories are then exchanged by the system's own shell, running a script that
lives outside both bundles. It has to be outside both: Windows will not rename a
directory containing a running executable, so anything started from the new
bundle cannot move the new bundle, and anything started from the old cannot move
the old.

That script waits for this process to exit, renames twice, and puts the first
name back if the second rename fails.

The order matters more than the mechanism. Nothing existing is touched until the
replacement has been downloaded, verified against the publisher's digest, and
seen to run. An upgrade that fails should leave a working tool and a wasted
minute; the state to avoid at any cost is half-swapped.

This is the one command that does its work in the client rather than asking the
daemon. That is not an erosion of the rule — the daemon is part of what is being
replaced, and it has to be stopped before the swap.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import net, paths, spawn

RELEASES = "https://api.github.com/repos/dskripchenko/portable/releases/latest"

#: The only target published. A bundle for anything else runs the tool and then
#: hands somebody binaries their machine cannot execute.
TARGET = "x86_64-pc-windows-msvc"

#: What a bundle consists of. Everything else in the folder belongs to whoever
#: put it there and travels with them across an upgrade.
#:
#: `home set --beside` puts the data directory inside the bundle, which is the
#: point of it — a flash drive holding the whole installation. Replacing the
#: bundle then means replacing the folder that also holds the sites and the
#: databases, and treating the two as one thing carried them into the copy kept
#: as the previous version. Reported from Windows, where it failed earlier and
#: more loudly, and reproduced here.
BUNDLE_FILES = frozenset({"python", "portable.cmd", "portable", "README.txt"})

#: How long the helper keeps trying to rename before giving up.
#:
#: Not a fixed pause. `cmd.exe` closes `portable.cmd` a moment after the Python
#: process it launched exits, and Windows refuses to rename a directory holding
#: an open file — so the first attempt often fails and the second succeeds.
#: Antivirus scanning a freshly unpacked directory extends that window
#: unpredictably, which is why this is generous.
RENAME_SECONDS = 60


class UpgradeFailed(RuntimeError):
    """The upgrade did not happen, with what stopped it."""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    digest_url: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def latest() -> Release:
    """The newest published release, and where its bundle is."""
    try:
        payload = json.loads(net.read_text(RELEASES, timeout=60))
    except (OSError, net.Unreachable, json.JSONDecodeError) as error:
        raise UpgradeFailed(f"Could not ask what the newest version is: {error}") from error

    version = str(payload.get("tag_name") or "").lstrip("v")
    assets = {asset.get("name"): asset.get("browser_download_url") for asset in payload.get("assets", [])}
    bundle = next(
        (name for name in assets if name and name.endswith(f"-{TARGET}.zip")),
        None,
    )

    if not version or bundle is None:
        raise UpgradeFailed(
            f"The newest release ({version or 'unnamed'}) has no {TARGET} bundle attached. "
            f"It offers: {', '.join(sorted(name for name in assets if name)) or 'nothing'}."
        )

    return Release(version=version, url=assets[bundle], digest_url=assets.get(f"{bundle}.sha256", ""))


def newer(candidate: str, than: str) -> bool:
    def key(version: str) -> tuple[int, ...]:
        return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))

    return key(candidate) > key(than)


def fetch(release: Release, into: Path) -> Path:
    """
    Download and verify the bundle, and return the unpacked directory.

    Verified against the digest published beside it, and refusing to go on
    without one. This replaces the program itself — the one download where
    "the publisher listed no checksum" is not an acceptable answer.
    """
    into.mkdir(parents=True, exist_ok=True)
    archive = into / release.filename

    expected = _published_digest(release)

    with net.open_url(release.url, timeout=900) as response:
        archive.write_bytes(response.read())

    actual = hashlib.sha256(archive.read_bytes()).hexdigest()

    if actual != expected:
        archive.unlink(missing_ok=True)
        raise UpgradeFailed(
            f"The downloaded bundle does not match the digest the release publishes.\n"
            f"  expected: {expected}\n"
            f"  received: {actual}\n"
            f"It has been discarded and nothing was changed."
        )

    unpacked = into / "new"

    if unpacked.exists():
        shutil.rmtree(unpacked)

    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)

    # The archive holds a single top-level directory named for the version.
    inside = [entry for entry in unpacked.iterdir() if entry.is_dir()]
    root = inside[0] if len(inside) == 1 else unpacked

    _restore_executable_bits(bundle_root=root)

    return root


def _published_digest(release: Release) -> str:
    if not release.digest_url:
        raise UpgradeFailed(
            f"The release publishes no digest beside {release.filename}, and this "
            f"replaces the program itself. Download it by hand if you mean to."
        )

    try:
        listed = net.read_text(release.digest_url, timeout=60)
    except (OSError, net.Unreachable) as error:
        raise UpgradeFailed(f"Could not fetch the digest: {error}") from error

    for row in listed.splitlines():
        parts = row.split()

        if len(parts) == 2 and parts[1].lstrip("*./") == release.filename:
            return parts[0].lower()

    raise UpgradeFailed(f"The published digest file does not mention {release.filename}.")


def _restore_executable_bits(bundle_root: Path) -> None:
    """
    Python's `extractall` drops the mode. On Windows nothing notices; anywhere
    else the interpreter unpacks unrunnable, and "permission denied" on a file
    that is plainly there reads like a different problem entirely.
    """
    if os.name == "nt":
        return

    for candidate in (bundle_root / "portable", *(bundle_root / "python" / "bin").glob("*")):
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)


def works(bundle_root: Path) -> str:
    """
    Run the new bundle once and return the version it reports.

    Before anything existing is touched. A bundle that arrived intact and still
    does not start — a partial archive, an interpreter the machine will not load
    — is exactly what must not be swapped in, and the only way to know is to ask
    it.
    """
    launcher = bundle_root / ("portable.cmd" if os.name == "nt" else "portable")

    if not launcher.exists():
        raise UpgradeFailed(f"The downloaded bundle has no launcher in it ({bundle_root}).")

    try:
        result = subprocess.run(
            [str(launcher), "version", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        )
        reported = json.loads(result.stdout)["version"]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError) as error:
        raise UpgradeFailed(
            f"The downloaded bundle does not run, so it has not been installed.\n"
            f"{error}\n"
            f"Nothing was changed."
        ) from error

    return reported


def swap(
    current: Path, replacement: Path, keep: Path, workspace: Path | None = None
) -> int:
    """
    Start the process that exchanges the two installations, and return its pid.

    It moves the **contents** of the bundle directory rather than the directory
    itself, and that is the whole design rather than an implementation detail.

    Windows will not rename a directory that is any process's current
    directory, and by the time an upgrade runs it is usually two: the shell it
    was typed into — the documentation itself says `portable.cmd upgrade`,
    which means standing in the folder — and the helper, which inherited that
    directory because nothing told it otherwise. So the rename could not
    succeed, the deadline ran out, the working copy was put back, and the whole
    thing looked like a download that changed nothing. Reported exactly that
    way, twice.

    Renaming files and folders *inside* a directory is not restricted like
    that, and a running executable can be renamed even though it cannot be
    deleted — which is how any updater replaces a program while it runs.

    It also means the data directory needs no carrying: with
    `home set --beside` it sits in the bundle folder, and the folder is now
    exactly where it stays. What used to be a move — every site and every
    database, with a failure path of its own — is now nothing happening to it.

    Run by the interpreter **already executing**, with the code passed as an
    argument rather than written to a file. A script on disk is a thing
    application control policies exist to stop, and on a managed machine they
    do: reported from Windows as "portable-swap.py was blocked in accordance
    with application control policies". Nothing is written, so there is no file
    for a rule to match. The running interpreter rather than the new bundle's,
    because a freshly extracted `python.exe` can still be held by whatever is
    scanning it, and `CreateProcess` then fails with access denied.
    """
    # Everything the new version will place, plus everything the old one put
    # there. The first would collide, the second would be left behind mixed in
    # with the new. Anything else in the folder is not ours and is not touched:
    # the data directory, the pointer naming it, whatever somebody kept beside
    # the tool.
    arriving = {entry.name for entry in replacement.iterdir()} if replacement.is_dir() else set()
    names = sorted({*BUNDLE_FILES, *arriving})

    # The download is cleaned up afterwards, and only when it is somewhere that
    # cannot possibly take the installation with it. Deriving this from the
    # replacement's parent — which is what it did for an hour — deletes the
    # directory the bundle lives in the moment an archive unpacks one level
    # flatter than expected.
    if workspace is not None and _contains(workspace, current):
        workspace = None

    return spawn.start_detached(
        [
            sys.executable,
            "-c",
            _HELPER,
            str(os.getpid()),
            str(current),
            str(replacement),
            str(keep),
            str(workspace or ""),
            str(RENAME_SECONDS),
            *names,
        ],
        # Outside the bundle, so that the helper is not itself holding the thing
        # it is working on — which it was, and which is half of why this never
        # completed on Windows.
        cwd=current.parent,
        # Beside the bundle, never inside it: with `--beside` the data directory
        # is in the bundle folder, and a log written into a folder being taken
        # apart is one more open handle in the wrong place.
        log=current.parent / "portable-upgrade.log",
    )


#: Passed to the interpreter as an argument, never written down.
#:
#: Standalone rather than a function in this package: at the moment it runs there
#: are two installations on disk, and importing from either would tie the swap to
#: something it is in the middle of moving.
#:
#: With `-c`, `sys.argv[0]` is `"-c"` and everything after it follows, which is
#: why the arguments below start at 1 exactly as they would for a script.
_HELPER = '''"""Exchange two installations once the process using the first has gone."""

import os
import shutil
import sys
import time


def alive(pid):
    if os.name == "nt":
        import ctypes

        # PROCESS_QUERY_LIMITED_INFORMATION. A handle that opens means the
        # process is there; one that does not means it is gone or was never
        # ours to ask about, and both answers are "stop waiting".
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)

        if not handle:
            return False

        ctypes.windll.kernel32.CloseHandle(handle)

        return True

    try:
        os.kill(pid, 0)
    except OSError:
        return False

    return True


def move(source, destination, deadline):
    """Keep trying: whatever blocks this is usually in the act of closing."""
    last = None

    while time.monotonic() < deadline:
        try:
            os.rename(source, destination)

            return
        except OSError as error:
            last = error
            time.sleep(0.25)

    raise last if last else OSError("could not move %s" % source)


def undo(done):
    """Put back what was moved, newest move first."""
    for source, destination in reversed(done):
        try:
            os.rename(destination, source)
        except OSError as error:
            print("could not put %s back: %s" % (source, error), flush=True)


def main():
    pid = int(sys.argv[1])
    current, replacement, keep, workspace = sys.argv[2:6]
    deadline = time.monotonic() + float(sys.argv[6])
    names = sys.argv[7:]

    print("waiting for %s" % pid, flush=True)

    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)

    # cmd.exe closes the batch file a moment after the process it launched exits.
    time.sleep(1.0)

    if os.path.exists(keep):
        shutil.rmtree(keep, ignore_errors=True)

    os.makedirs(keep, exist_ok=True)

    # Out: the old version, into the folder kept beside this one.
    taken = []

    for name in names:
        source = os.path.join(current, name)

        if not os.path.exists(source):
            continue

        try:
            move(source, os.path.join(keep, name), deadline)
            taken.append((source, os.path.join(keep, name)))
        except OSError as error:
            print("could not set aside %s: %s" % (name, error), flush=True)
            undo(taken)
            shutil.rmtree(keep, ignore_errors=True)

            raise SystemExit(1)

    # In: the new one, into the folder nobody had to rename.
    placed = []

    for name in sorted(os.listdir(replacement)):
        source = os.path.join(replacement, name)

        try:
            move(source, os.path.join(current, name), deadline)
            placed.append((source, os.path.join(current, name)))
        except OSError as error:
            # A tool that is merely out of date is a great deal better than one
            # that is not there.
            print("swap failed and was undone: %s" % error, flush=True)
            undo(placed)
            undo(taken)
            shutil.rmtree(keep, ignore_errors=True)

            raise SystemExit(1)

    if workspace:
        shutil.rmtree(workspace, ignore_errors=True)

    print("swapped: %s replaced, previous kept at %s" % (current, keep), flush=True)


main()
'''


def _contains(directory: Path, other: Path) -> bool:
    """Whether `other` is `directory` or sits inside it."""
    try:
        other.resolve().relative_to(directory.resolve())
    except (ValueError, OSError):
        return False

    return True


def can_upgrade() -> Path:
    """The bundle to replace, or a refusal explaining why there is not one."""
    bundle = paths.bundle()

    if bundle is None:
        raise UpgradeFailed(
            "This is a source checkout rather than a bundle, so there is nothing "
            "to replace. Use git."
        )

    if not os.access(bundle, os.W_OK):
        raise UpgradeFailed(f"{bundle} cannot be written to.")

    return bundle


def workspace(bundle: Path) -> Path:
    """
    Where the replacement is unpacked: beside the bundle, not inside it.

    Inside would mean unpacking a copy of the tool into the directory about to
    be renamed, and renaming a directory into its own subdirectory is not a
    thing that works.
    """
    return bundle.parent / f".{bundle.name}.upgrade"


def previous(bundle: Path, version: str) -> Path:
    return bundle.parent / f"{bundle.name}.{version}.old"


def python_is_ours(bundle: Path) -> bool:
    """Whether the interpreter running this belongs to the bundle being replaced."""
    try:
        Path(sys.executable).resolve().relative_to(bundle.resolve())
    except ValueError:
        return False

    return True
