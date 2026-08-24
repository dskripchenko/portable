"""
Where everything lives.

One directory holds the whole installation: runtimes, configuration, state,
logs, sockets. Deleting it removes the tool without a trace — no registry keys,
no services, no entries in PATH, nothing under the system directories.

That is not tidiness. It is the reason this tool can be installed on a machine
where you are not an administrator, which is the situation it was written for.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "portable"


def root() -> Path:
    """
    The installation directory.

    `PORTABLE_HOME` overrides it, which is what the tests use and what makes a
    genuinely portable install possible: point it at a flash drive and the whole
    thing travels.
    """
    override = os.environ.get("PORTABLE_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        # LOCALAPPDATA rather than APPDATA: this is machine-local state that has
        # no business travelling with a roaming profile — the runtimes alone run
        # to hundreds of megabytes.
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / APP_NAME

    return Path.home() / f".{APP_NAME}"


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


def ensure_layout() -> None:
    """Creates the directories. Safe to call repeatedly."""
    for path in (root(), runtimes(), downloads(), sites(), logs(), run()):
        path.mkdir(parents=True, exist_ok=True)
