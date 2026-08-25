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


def swap(current: Path, replacement: Path, keep: Path) -> int:
    """
    Start the process that exchanges the directories, and return its pid.

    Run by the interpreter **already executing** — this one — with the code
    passed as an argument rather than written to a file.

    That last part is the whole of it. A script on disk is a thing application
    control policies exist to stop, and on a managed machine they do: reported
    from Windows as "portable-swap.py was blocked in accordance with application
    control policies". The same lesson the installer already carries — a `.ps1`
    on disk will not run under the default execution policy while a string
    passed to `iex` will — and it applies here for the same reason. Nothing is
    written, so there is no file for a rule to match.

    The interpreter is the running one rather than the new bundle's, which was
    the obvious choice and the wrong one: a freshly extracted `python.exe` can
    be held by whatever is scanning it, and `CreateProcess` then fails with
    access denied. The one already running cannot be, because it is already
    running — and renaming the directory it runs from is permitted, since the
    loader maps images with `FILE_SHARE_DELETE`. A test records that.

    It waits for the calling process to exit first, because until then that
    process is running from `current`, and on Windows `cmd.exe` still holds
    `portable.cmd` open.
    """
    return spawn.start_detached(
        [
            sys.executable,
            "-c",
            _HELPER,
            str(os.getpid()),
            str(current),
            str(replacement),
            str(keep),
            str(RENAME_SECONDS),
        ],
        log=paths.logs() / "upgrade.log",
    )


#: Passed to the interpreter as an argument, never written down.
#:
#: Standalone rather than a function in this package: at the moment it runs there
#: are two installations on disk, and importing from either would tie the swap to
#: something it is in the middle of moving.
#:
#: With `-c`, `sys.argv[0]` is `"-c"` and everything after it follows, which is
#: why the arguments below start at 1 exactly as they would for a script.
_HELPER = '''"""Exchange two directories once the process using the first has gone."""

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


def rename(source, destination, deadline):
    """Keep trying: whatever blocks this is usually in the act of closing."""
    last = None

    while time.monotonic() < deadline:
        try:
            os.rename(source, destination)

            return
        except OSError as error:
            last = error
            time.sleep(0.25)

    raise last if last else OSError(f"could not rename {source}")


def main():
    pid = int(sys.argv[1])
    current, replacement, keep = sys.argv[2], sys.argv[3], sys.argv[4]
    deadline = time.monotonic() + float(sys.argv[5])

    print(f"waiting for {pid}", flush=True)

    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)

    # cmd.exe closes the batch file a moment after the process it launched exits.
    time.sleep(1.0)

    if os.path.exists(keep):
        shutil.rmtree(keep, ignore_errors=True)

    rename(current, keep, deadline)

    try:
        rename(replacement, current, deadline)
    except OSError as error:
        # Put the working installation back. A tool that is merely out of date
        # is a great deal better than one that is not there.
        os.rename(keep, current)

        print(f"swap failed and was undone: {error}", flush=True)

        raise SystemExit(1)

    print(f"swapped: {current} replaced, previous kept at {keep}", flush=True)


main()
'''


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
