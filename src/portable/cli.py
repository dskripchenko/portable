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

    install = add("install", "Download a runtime — php, caddy.", _install)
    install.add_argument("runtime", choices=("php", "caddy"))
    install.add_argument(
        "version",
        nargs="?",
        default="latest",
        help="A branch (8.4), an exact version, or latest.",
    )
    install.add_argument(
        "--from",
        dest="existing",
        metavar="PATH",
        help="Adopt a runtime already on this machine instead of downloading one.",
    )

    runtimes = add("runtimes", "What is installed.", _runtimes)
    runtimes.set_defaults(run=_runtimes)

    site = subparsers.add_parser("site", help="Sites served by this installation.")
    site_commands = site.add_subparsers(dest="site_command")

    def add_site(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = site_commands.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.set_defaults(run=run)

        return sub

    add_it = add_site("add", "Serve a directory at <name>.localhost.", _site_add)
    add_it.add_argument("name")
    add_it.add_argument(
        "root",
        nargs="?",
        default=".",
        help="The directory to serve. Defaults to the current one.",
    )
    add_it.add_argument("--php", help="Pin a PHP version. Defaults to the newest installed.")

    remove = add_site("remove", "Stop serving a site.", _site_remove)
    remove.add_argument("name")

    add_site("list", "Sites and their addresses.", _site_list)

    site.set_defaults(run=_site_help, site_command=None, json=False)

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
        # Whether it is still there decides what to look at next: a process that
        # exited has a reason in its log, one that is running and silent is
        # stuck somewhere else entirely.
        alive = "still running" if spawn.is_running(pid) else "no longer running"

        # The log is shown, not pointed at. "See the log file" asks a person to
        # go and look for a traceback the tool has already read — and on a CI
        # runner, or any machine somebody else is holding, nobody goes.
        return _fail(
            args,
            "daemon-did-not-start",
            f"The daemon was started as pid {pid} ({alive}) and never answered.\n"
            f"{paths.tail(paths.logs() / 'daemon.log')}",
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

    served = f"  ·  port {status['port']}" if status.get("port") else ""
    lines = [
        f"portable {status['version']}  ·  {status['home']}{served}",
        f"{status.get('sites', 0)} site(s)",
    ]
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


def _install(args) -> int:
    """
    Ask the daemon to fetch a runtime.

    The daemon does the work rather than the CLI, even though the CLI could
    manage a download perfectly well — the rule is that the API can do
    everything, and every exception to it becomes a gap in the IDE plugin later.
    """
    result = Client().call(
        "POST",
        "/v1/runtimes/install",
        {
            "name": args.runtime,
            "version": args.version,
            "from": getattr(args, "existing", None),
        },
        # A PHP archive is thirty-odd megabytes and Postgres is far larger; the
        # default timeout is sized for questions, not for transfers.
        timeout=600,
    )

    if result.get("managed") is False:
        return _emit(
            args,
            result,
            f"Adopted {result['name']} {result['version']} at {result['directory']}.\n"
            f"It will be used but never modified or removed.",
        )

    note = "" if result.get("verified") else "  (the publisher listed no checksum)"

    return _emit(args, result, f"Installed {result['name']} {result['version']}.{note}")


def _runtimes(args) -> int:
    result = Client().call("GET", "/v1/runtimes")

    if args.json:
        return _emit(args, result, "")

    entries = result["runtimes"]

    if not entries:
        print("Nothing installed. Try `portable install php`.")

        return 0

    for entry in entries:
        origin = "" if entry["managed"] else "  (found on this machine, not managed)"
        print(f"  {entry['name']:<8} {entry['version']:<12} {entry['directory']}{origin}")

    return 0


def _site_add(args) -> int:
    root = Path(args.root).expanduser().resolve()
    result = Client().call(
        "POST",
        "/v1/sites/add",
        {"name": args.name, "root": str(root), "php": args.php},
        # Adding the first site starts a pool and the router.
        timeout=120,
    )

    return _emit(args, result, f"{result['url']}  ->  {result['root']}")


def _site_remove(args) -> int:
    result = Client().call("POST", "/v1/sites/remove", {"name": args.name}, timeout=60)

    return _emit(args, result, f"Removed {result['removed']}.")


def _site_list(args) -> int:
    result = Client().call("GET", "/v1/sites")

    if args.json:
        return _emit(args, result, "")

    if not result["sites"]:
        print("No sites. Add one with `portable site add <name>`.")

        return 0

    for site in result["sites"]:
        pinned = f"  php {site['php']}" if site["php"] else ""
        print(f"  {site['url']:<40} {site['root']}{pinned}")

    return 0


def _site_help(args) -> int:
    print("Usage: portable site {add,remove,list}")

    return 2


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
