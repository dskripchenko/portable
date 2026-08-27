"""
Finding free ports, and admitting that "free" has a shelf life.

A pool of eight `php-cgi` processes needs eight ports, and they are picked here
rather than configured: asking someone to nominate eight numbers that nothing
else on their machine wants is asking them to be wrong occasionally.

The check is a real bind, not a scan of what is listening. Those differ: a
socket in TIME_WAIT is not listening and cannot be bound either, and a port
reserved by Windows' dynamic range is invisible to both `netstat` and any
listing but will refuse a bind.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess

#: Where pool members are placed. High enough to sit above anything with a
#: registered claim, and outside Windows' default dynamic client range
#: (49152-65535) so an outgoing connection cannot take a port from underneath a
#: pool member that is about to restart.
POOL_RANGE = range(9000, 9500)

#: Where the router's admin endpoint goes. A separate range so a pool cannot
#: take it, and — like the pool — **not** the operating system's ephemeral range.
#:
#: `ephemeral()` looks like the obvious choice and is the wrong one for anything
#: that will be bound later: it binds, reads the number and lets go, and on a
#: busy machine the port can be handed to an outgoing connection before the
#: process that was going to use it gets there. That is exactly what happened
#: here, on CI, after this file had already explained why the pool avoids that
#: range.
ADMIN_RANGE = range(9500, 9600)


class NoFreePort(RuntimeError):
    """Every candidate was taken."""


def is_free(port: int, host: str = "127.0.0.1") -> bool:
    """
    Whether a port can be bound right now.

    Deliberately without `SO_REUSEADDR`. With it, a bind can succeed against a
    port another process is still using in some states, which would report
    "free" for something that will collide the moment a pool member starts.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))

            return True
        except OSError:
            return False


def find(count: int, taken: set[int] | None = None, candidates: range = POOL_RANGE) -> list[int]:
    """
    [count] ports nothing is using, in ascending order.

    [taken] is for ports this tool has already promised to something that has
    not started yet. Without it, allocating two pools in quick succession hands
    the same numbers to both — every one of them is genuinely free at the moment
    it is asked about.
    """
    taken = taken or set()
    found: list[int] = []

    for port in candidates:
        if len(found) == count:
            break

        if port in taken or not is_free(port):
            continue

        found.append(port)

    if len(found) < count:
        raise NoFreePort(
            f"Only {len(found)} of the {count} ports needed were free in "
            f"{candidates.start}-{candidates.stop - 1}. Something is using an "
            f"unusual number of them, or a previous run left processes behind."
        )

    return found


def ephemeral() -> int:
    """
    One port from the operating system's own free range.

    Only safe where the socket is bound **by the caller, immediately** — the
    daemon's own control API asks the OS for port 0 and keeps the socket. Do not
    use it to pick a number for something else to bind later: between the two
    the port can be given away. Use `find()` for that.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))

        return probe.getsockname()[1]


def holder(port: int) -> str | None:
    """
    What is listening on a port, named, or `None` when it cannot be told.

    "Something else has it" is the least useful true sentence a tool can say
    about a port. The answer is nearly always a program somebody recognises —
    another local stack, IIS, a container — and the machine can look it up in
    the time it takes to write `netstat` in a message and ask a person to.

    Everything here is best-effort by design. It appears inside failure
    messages, and a message that fails to be produced is worse than one missing
    a detail, so every path out of here that is not an answer is `None`.
    """
    try:
        pid = _listener_pid(port)

        if pid is None:
            return None

        name = _process_name(pid)

        return f"{name} (pid {pid})" if name else f"pid {pid}"
    except Exception:  # noqa: BLE001
        return None


def _listener_pid(port: int) -> int | None:
    if os.name == "nt":
        # `-a` for listening sockets, `-n` numeric so no name lookup delays
        # this, `-o` for the owning pid. Parsed rather than asked of an API
        # because the alternative on Windows is a considerable amount of ctypes
        # for a line in an error message.
        listing = _ran(["netstat", "-ano", "-p", "tcp"])

        if not listing:
            return None

        for line in listing.splitlines():
            parts = line.split()

            if len(parts) >= 5 and parts[3].upper() == "LISTENING" and _is_port(parts[1], port):
                return int(parts[4])

        return None

    listing = _ran(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])

    if not listing:
        return None

    found = re.search(r"^p(\d+)", listing, re.MULTILINE)

    return int(found.group(1)) if found else None


def _process_name(pid: int) -> str | None:
    if os.name == "nt":
        listing = _ran(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])

        if not listing or "No tasks" in listing:
            return None

        return listing.strip().split(",")[0].strip('"') or None

    listing = _ran(["ps", "-p", str(pid), "-o", "comm="])

    return listing.strip().rsplit("/", 1)[-1] if listing else None


def _is_port(address: str, port: int) -> bool:
    """Whether a `netstat` local address is this port — `0.0.0.0:443`, `[::]:443`."""
    return address.rsplit(":", 1)[-1] == str(port)


def _ran(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return result.stdout if result.returncode == 0 else None
