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

import socket

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
