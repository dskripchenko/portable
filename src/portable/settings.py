"""
The few choices that outlive a command.

Almost nothing belongs here. Where runtimes live is decided by `paths`, what is
served by `sites`, what runs by `services` — each of those is a list of things
with its own file, and settings are what is left: single values, machine-wide,
that somebody set once.

Today that is the port. It is here rather than a field on `Stack` because a
`Stack` is rebuilt every time the daemon starts, and a preference that does not
survive a restart is not a preference.
"""

from __future__ import annotations

import json

from . import paths, ports

#: Tried in order when nothing has been chosen.
#:
#: 80 first because `demo.localhost` reads better than `demo.localhost:8080`,
#: and because on Windows — one of the few places it is friendlier than Unix —
#: an ordinary user may bind it. 8080 after it, being the convention for exactly
#: this situation.
DEFAULT_PORTS = (80, 8080)


class InvalidSetting(ValueError):
    """A value that cannot be stored, with the reason."""


def read() -> dict:
    path = paths.config_file()

    if not path.exists():
        return {}

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A settings file nobody can parse should not stop the tool from
        # running: every value in it has a default, and refusing to start over a
        # stray comma would make this the most fragile part of the program
        # rather than the least important one.
        return {}

    return loaded if isinstance(loaded, dict) else {}


def write(values: dict) -> None:
    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")


def candidate_ports() -> tuple[int, ...]:
    """
    The ports the router should try, in order.

    A chosen port is the only candidate. Falling back to 8080 after somebody
    asked for 8888 would put the site on an address they did not choose and did
    not ask about — and the whole reason for choosing is that the defaults were
    not usable.
    """
    chosen = read().get("port")

    return (int(chosen),) if chosen else DEFAULT_PORTS


def set_port(port: int | None) -> tuple[int, ...]:
    """Choose a port, or `None` to go back to trying 80 and then 8080."""
    values = read()

    if port is None:
        values.pop("port", None)
        write(values)

        return DEFAULT_PORTS

    check_port(port)
    values["port"] = int(port)
    write(values)

    return (int(port),)


def check_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise InvalidSetting(f"{port} is not a port number.")

    # The pool and the router's admin endpoint are handed out from these, and a
    # site on one of them would take a number from under a worker that is about
    # to ask for it — intermittently, and only under load, which is the worst
    # way for this to be discovered.
    if port in ports.POOL_RANGE:
        raise InvalidSetting(
            f"{port} is inside {ports.POOL_RANGE.start}-{ports.POOL_RANGE.stop - 1}, "
            f"which this tool hands out to PHP workers."
        )

    if port in ports.ADMIN_RANGE:
        raise InvalidSetting(
            f"{port} is inside {ports.ADMIN_RANGE.start}-{ports.ADMIN_RANGE.stop - 1}, "
            f"which this tool uses for the router's admin endpoint."
        )

    if port >= 49152:
        # Windows hands these out for outgoing connections, so one can be taken
        # between being chosen and being bound — and then again on the next
        # start, differently.
        raise InvalidSetting(
            f"{port} is in the range Windows uses for outgoing connections "
            f"(49152-65535), so it can be taken while the router is starting. "
            f"Pick something below 49152."
        )
