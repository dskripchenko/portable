"""
Where everything lives.

One directory holds the whole installation: runtimes, configuration, state,
logs, sockets. Deleting it removes the tool without a trace — no registry keys,
no services, no entries in PATH, nothing under the system directories.

That is not tidiness. It is the reason this tool can be installed on a machine
where you are not an administrator, which is the situation it was written for.

**Where that directory sits is not fixed.** `%LOCALAPPDATA%` is the default and
not always a usable one: AppLocker's common configurations deny execution from
under a user's profile, precisely because that is where software installed
without administrator rights lives. Everything downloaded here is an executable,
so on such a machine the default location does not merely feel wrong — nothing
will start. Hence four ways to say otherwise, in `resolved()` below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "portable"

#: The pointer file, beside the launcher. A plain line of text so that it can be
#: read, and fixed, without this tool being runnable — which is the state
#: somebody pointing it at an unusable directory has just put themselves in.
POINTER = "portable.home"

#: What the pointer holds when the data should travel with the bundle. A word
#: rather than an absolute path because that is the case an absolute path cannot
#: express: a flash drive is `E:` on one machine and `F:` on the next.
BESIDE = "beside"


def resolved() -> tuple[Path, str]:
    """
    The installation directory, and what decided on it.

    In order:

    1. `PORTABLE_HOME` — the `--home` flag sets it, and it is what the tests use.
    2. The pointer file beside the launcher, written by `portable home set`.
    3. `%LOCALAPPDATA%\\portable`, or `~/.portable`.

    The source travels with the answer because "why is my PHP over there" is a
    question this tool should be able to answer itself. Two of these three are
    invisible otherwise — an environment variable exported in another shell, a
    file somebody wrote months ago.
    """
    override = os.environ.get("PORTABLE_HOME")

    if override:
        return Path(override).expanduser().resolve(), "PORTABLE_HOME"

    pointer = pointer_file()

    if pointer is not None and pointer.is_file():
        recorded = pointer.read_text(encoding="utf-8").strip()

        if recorded == BESIDE:
            return (pointer.parent / "data").resolve(), str(pointer)

        if recorded:
            return Path(recorded).expanduser().resolve(), str(pointer)

    return default_root(), "the default"


def root() -> Path:
    return resolved()[0]


def default_root() -> Path:
    if os.name == "nt":
        # LOCALAPPDATA rather than APPDATA: this is machine-local state that has
        # no business travelling with a roaming profile — the runtimes alone run
        # to hundreds of megabytes.
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / APP_NAME

    return Path.home() / f".{APP_NAME}"


def bundle() -> Path | None:
    """
    The directory the launcher sits in, or None when this is not a bundle.

    Found by walking up from the running interpreter until a launcher appears
    beside it: `python/python.exe` on Windows and `python/bin/python3` elsewhere,
    so it is never far. Deliberately not derived from this file's own location —
    the package is inside `site-packages`, several levels down a path whose
    shape includes the interpreter's minor version.

    A source checkout has no launcher, and gets None. There is nothing to write
    a pointer beside, and `PORTABLE_HOME` is the answer there.
    """
    start = Path(sys.executable).resolve().parent

    for candidate in (start, *list(start.parents)[:3]):
        if (candidate / "portable.cmd").is_file() or (candidate / "portable").is_file():
            return candidate

    return None


def pointer_file() -> Path | None:
    found = bundle()

    return None if found is None else found / POINTER


def set_home(target: Path | str) -> Path:
    """
    Record where everything should live, beside the launcher.

    Returns the directory that will now be used. `BESIDE` records the word
    rather than the path it resolves to today, so the bundle keeps working when
    the drive letter changes.
    """
    pointer = pointer_file()

    if pointer is None:
        raise NotABundle(
            "This is a source checkout, not a bundle, so there is no launcher to "
            "write the setting beside. Set PORTABLE_HOME instead."
        )

    if target == BESIDE:
        pointer.write_text(f"{BESIDE}\n", encoding="utf-8")

        return (pointer.parent / "data").resolve()

    chosen = Path(target).expanduser().resolve()
    check_usable(chosen)
    pointer.write_text(f"{chosen}\n", encoding="utf-8")

    return chosen


def clear_home() -> None:
    pointer = pointer_file()

    if pointer is not None:
        pointer.unlink(missing_ok=True)


def check_usable(target: Path) -> None:
    """
    Fail now rather than at the first install.

    Creating the directory is the check: a path on a drive that is not mounted,
    inside a folder policy forbids, or occupied by a file all fail here, in a
    command whose whole subject is that path — instead of surfacing later,
    halfway through a download, as an error about somewhere the person has since
    forgotten they configured.
    """
    if target.exists() and not target.is_dir():
        raise UnusableHome(f"{target} exists and is a file, not a directory.")

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".portable-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise UnusableHome(f"{target} cannot be written to: {error}") from error


class NotABundle(Exception):
    """Raised when there is no launcher to record a setting beside."""


class UnusableHome(Exception):
    """Raised when a chosen directory cannot hold the installation."""


def runtimes() -> Path:
    """Unpacked runtimes, one directory per version: `php/8.4.24-nts-x64`."""
    return root() / "runtimes"


def downloads() -> Path:
    """Archives as they arrived, kept so a re-install costs nothing."""
    return root() / "downloads"


def sites() -> Path:
    """Generated per-site configuration."""
    return root() / "sites"


def logs() -> Path:
    return root() / "logs"


def run() -> Path:
    """Pids, the daemon's discovery file, anything that dies with the process."""
    return root() / "run"


def config_file() -> Path:
    return root() / "portable.json"


def daemon_file() -> Path:
    """
    How a client finds the daemon: port and token.

    Read by the CLI, and later by the IDE plugin. Both are clients of the same
    API — see `daemon/server.py` for why the CLI does no work of its own.
    """
    return run() / "daemon.json"


def tail(path: Path, lines: int = 25) -> str:
    """
    The end of a log, formatted for a failure message.

    Shown rather than pointed at. "See the log file" asks a person to go and
    read a traceback the program has already read — and on a CI runner, or any
    machine somebody else is holding, nobody goes.
    """
    if not path.exists():
        return f"Nothing was written to {path}."

    text = path.read_text(encoding="utf-8", errors="replace").strip()

    if not text:
        return f"{path} is empty — the process died before it could say anything."

    return f"Last of {path}:\n" + "\n".join(text.splitlines()[-lines:])


def ensure_layout() -> None:
    """Creates the directories. Safe to call repeatedly."""
    for path in (root(), runtimes(), downloads(), sites(), logs(), run()):
        path.mkdir(parents=True, exist_ok=True)
