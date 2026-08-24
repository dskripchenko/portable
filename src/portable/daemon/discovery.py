"""
How a client finds the daemon.

The daemon listens on an ephemeral port and writes down where it landed. Any
client — the CLI today, an IDE plugin later — reads that file and needs no
configuration at all.

The file carries a token, and the token is not ceremony. The control API starts
processes; it is reachable by anything running as this user, including an
`npm install` postinstall script. Binding to the loopback keeps other machines
out and does nothing about the local ones, so possession of the file is what
authorises a caller.

On POSIX the file is `0600`. On Windows it inherits the ACL of
`%LOCALAPPDATA%`, which is already restricted to the user — the platform does
here what `chmod` does there.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import paths, spawn

#: Bumped when the shape of the control API changes incompatibly. A client
#: reading a file it does not recognise should say so rather than guess.
PROTOCOL = 1


@dataclass(frozen=True)
class Endpoint:
    """Everything needed to talk to a running daemon."""

    port: int
    token: str
    pid: int
    protocol: int = PROTOCOL

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write(endpoint: Endpoint, path: Path | None = None) -> Path:
    path = path or paths.daemon_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Created with restrictive permissions before anything is written, rather
    # than chmod'ed afterwards: between the two there is a moment where the
    # token is readable, and that moment is exactly what an attacker waits for.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(asdict(endpoint), handle, indent=2)

    return path


def read(path: Path | None = None) -> Endpoint | None:
    """
    The running daemon's endpoint, or None when there is not one.

    A file describing a process that is no longer alive is treated as absent and
    removed. Otherwise a daemon killed rather than stopped leaves a note that
    sends every later client to a port belonging to something else.
    """
    path = path or paths.daemon_file()

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        endpoint = Endpoint(
            port=int(data["port"]),
            token=str(data["token"]),
            pid=int(data["pid"]),
            protocol=int(data.get("protocol", 0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # Truncated by a crash mid-write, or from a version that wrote something
        # else. Either way it describes nothing that can be talked to.
        path.unlink(missing_ok=True)

        return None

    if not spawn.is_running(endpoint.pid):
        path.unlink(missing_ok=True)

        return None

    return endpoint


def clear(path: Path | None = None) -> None:
    (path or paths.daemon_file()).unlink(missing_ok=True)
