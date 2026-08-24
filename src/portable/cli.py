"""
The command line.

Deliberately thin. Every command here finds the daemon, makes one call and
prints the answer; none of them does any work of its own. That rule is the whole
reason an IDE plugin will be cheap to write later — it becomes the second client
of an API that already does everything — and it is worth defending against the
recurring temptation to "just do this bit directly" because a round trip seems
excessive for something small.

`--json` is on every command, not a chosen few. A client that has to parse
sentences written for people is a client that breaks when a sentence is reworded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import paths, spawn
from .daemon import discovery
from .daemon.client import CallFailed, Client, NotRunning


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()

        return 2

    try:
        return args.run(args)
    except NotRunning as error:
        return _fail(args, "not-running", str(error))
    except CallFailed as error:
        return _fail(args, error.key, error.message)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portable",
        description="A development environment that installs beside the system, not into it.",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.set_defaults(run=run)

        return sub

    add("up", "Start the daemon.", _up)
    add("down", "Stop the daemon and everything it supervises.", _down)
    add("status", "What is running.", _status)

    return parser


def _up(args) -> int:
    """
    Start the daemon, detached, and wait for it to answer.

    Waiting matters: without it `portable up` returns before the daemon is
    listening, and the very next command fails with "no daemon is running" for
    a reason that looks like a bug rather than a race.
    """
    existing = discovery.read()

    if existing is not None:
        return _emit(args, {"already": True, "pid": existing.pid, "port": existing.port},
                     f"Already running (pid {existing.pid}).")

    paths.ensure_layout()

    pid = spawn.start_detached(
        [spawn.python_executable(), "-m", "portable.daemon"],
        env=_daemon_environment(),
        log=paths.logs() / "daemon.log",
    )

    endpoint = _await_daemon()

    if endpoint is None:
        # The log is shown, not pointed at. "See the log file" asks a person to
        # go and look for a traceback the tool has already read — and on a CI
        # runner, or any machine somebody else is holding, nobody goes.
        return _fail(
            args,
            "daemon-did-not-start",
            f"The daemon was started as pid {pid} and never answered.\n"
            f"{_log_tail(paths.logs() / 'daemon.log')}",
        )

    return _emit(args, {"pid": endpoint.pid, "port": endpoint.port},
                 f"Started (pid {endpoint.pid}).")


def _down(args) -> int:
    client = Client()
    endpoint = client.endpoint
    client.shutdown()

    # The reply comes before the teardown, so the caller gets an answer rather
    # than a dropped connection. Waiting here turns `down` into a promise that
    # the ports are free when it returns.
    #
    # The wait is on the **process**, not on the discovery file. The daemon
    # removes its file and then exits, so a file-based check reports success
    # while `php-cgi` and Caddy are still holding their ports — and the next
    # `portable up` fails with an address already in use, pointing nowhere near
    # the cause.
    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if not spawn.is_running(endpoint.pid):
            discovery.clear()

            return _emit(args, {"stopped": True}, "Stopped.")

        time.sleep(0.1)

    return _fail(
        args,
        "did-not-stop",
        f"The daemon (pid {endpoint.pid}) acknowledged the request and is still running.",
    )


def _status(args) -> int:
    status = Client().status()

    if args.json:
        return _emit(args, status, "")

    lines = [f"portable {status['version']}  ·  {status['home']}"]
    processes = status.get("processes", [])

    if not processes:
        lines.append("nothing supervised")
    else:
        for process in processes:
            detail = f"pid {process['pid']}" if process["pid"] else "—"
            restarts = f", {process['restarts']} restarts" if process["restarts"] else ""
            lines.append(f"  {process['name']:<20} {process['state']:<9} {detail}{restarts}")

            if process.get("failure"):
                lines.append(f"  {'':<20} {process['failure']}")

    print("\n".join(lines))

    return 0


def _log_tail(path: Path, lines: int = 20) -> str:
    """The end of a log, formatted for a failure message."""
    if not path.exists():
        return f"Nothing was written to {path}."

    text = path.read_text(encoding="utf-8", errors="replace").strip()

    if not text:
        return f"{path} is empty — the process died before it could say anything."

    tail = "\n".join(text.splitlines()[-lines:])

    return f"Last of {path}:\n{tail}"


def _daemon_environment() -> dict[str, str]:
    """
    An environment in which the daemon can import the package that started it.

    Inheriting the parent's is not enough, and the difference only shows outside
    a developer's own setup. Run from a source checkout — by pytest, or by
    `python -m portable.cli` — this package is on `sys.path` without `PYTHONPATH`
    ever being set, so the detached child, a fresh interpreter with a fresh
    `sys.path`, cannot find it at all.

    Locally that was hidden by exporting `PYTHONPATH` by hand. CI, which does
    not, failed on all four platforms at once — which is the whole reason it
    runs.
    """
    import portable

    package_root = str(Path(portable.__file__).resolve().parent.parent)
    environment = dict(os.environ)
    existing = [part for part in environment.get("PYTHONPATH", "").split(os.pathsep) if part]

    # dict.fromkeys keeps the order and drops duplicates: the location this
    # package was imported from wins, and whatever the caller had set survives
    # behind it.
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([package_root, *existing]))

    return environment


def _await_daemon(timeout: float = 15.0) -> discovery.Endpoint | None:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        endpoint = discovery.read()

        if endpoint is not None:
            try:
                Client(endpoint=endpoint).ping()

                return endpoint
            except (NotRunning, CallFailed):
                # Written the file, not yet listening. Normal for a moment.
                pass

        time.sleep(0.1)

    return None


def _emit(args, payload: dict, human: str) -> int:
    if args.json:
        print(json.dumps(payload))
    elif human:
        print(human)

    return 0


def _fail(args, key: str, message: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"errorKey": key, "message": message}))
    else:
        print(message, file=sys.stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
