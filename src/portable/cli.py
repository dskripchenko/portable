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

from . import catalog, paths, spawn
from .daemon import discovery
from .daemon.client import CallFailed, Client, NotRunning


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    # Before anything reads a path. `--home` works by setting the variable the
    # rest of the tool already consults, which is why it needs no plumbing of
    # its own and why the daemon, started with this environment, agrees with the
    # client that started it.
    if getattr(args, "home", None):
        os.environ["PORTABLE_HOME"] = str(Path(args.home).expanduser().resolve())

    if not args.command:
        parser.print_help()

        return 2

    try:
        return args.run(args)
    except NotRunning as error:
        return _fail(args, "not-running", str(error))
    except CallFailed as error:
        return _fail(args, error.key, error.message)
    except (paths.NotABundle, paths.UnusableHome) as error:
        return _fail(args, "unusable-home", str(error))


def _parser() -> argparse.ArgumentParser:
    # A parent parser rather than one flag on the top level, so that `--home`
    # is accepted on either side of the command name. Somebody typing it after
    # the verb is not making a mistake, and argparse would otherwise reject it
    # in a way that reads like the flag does not exist.
    #
    # SUPPRESS matters: without it the subparser's own `None` default overwrites
    # a value given before the command, silently.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--home",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Use this directory for this one command. See `portable home`.",
    )

    parser = argparse.ArgumentParser(
        prog="portable",
        parents=[common],
        description="A development environment that installs beside the system, not into it.",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text, parents=[common])
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.set_defaults(run=run)

        return sub

    add("up", "Start the daemon.", _up)
    add("down", "Stop the daemon and everything it supervises.", _down)
    add("status", "What is running.", _status)

    install = add("install", "Download a runtime — php, caddy.", _install)
    # From the catalog rather than typed out again. The two lists were separate
    # once and drifted: this one offered four runtimes the daemon then refused.
    install.add_argument("runtime", choices=catalog.names())
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

    available = add("available", "What each publisher currently offers.", _available)
    available.add_argument("runtime", choices=catalog.names())
    available.add_argument(
        "branch",
        nargs="?",
        help="For PHP: a branch (8.3), to include superseded patches from the archive.",
    )

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

    env = add("env", "Print the shell settings that reach the installed runtimes.", _env)
    env.add_argument(
        "--shell",
        choices=("powershell", "cmd", "posix"),
        help="Defaults to what this platform most likely uses.",
    )

    run = subparsers.add_parser(
        "run",
        help="Run a command with the installed runtimes on PATH.",
        # Otherwise `portable run npm --version` is read as a flag of ours.
        prefix_chars="\x00",
    )
    run.add_argument("argv", nargs=argparse.REMAINDER)
    run.set_defaults(run=_run, json=False)

    service = subparsers.add_parser("service", help="Databases run by this installation.")
    service_commands = service.add_subparsers(dest="service_command")

    def add_service(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = service_commands.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.set_defaults(run=run)

        return sub

    service_add = add_service("add", "Start a database.", _service_add)
    service_add.add_argument("kind", choices=("postgres", "mariadb", "redis"))
    service_add.add_argument("--name", help="Defaults to the kind. A second instance needs one.")
    service_add.add_argument("--version")
    service_add.add_argument("--port", type=int, help="Defaults to the conventional one, if free.")

    service_remove = add_service("remove", "Stop a database. Its data is kept.", _service_remove)
    service_remove.add_argument("name")

    add_service("list", "Databases and how to reach them.", _service_list)

    service.set_defaults(run=_service_help, service_command=None, json=False)

    ext = subparsers.add_parser("ext", parents=[common], help="PHP extensions.")
    ext.add_argument("--json", action="store_true", help="Machine-readable output.")
    ext.add_argument("--php", help="Which installed PHP. Defaults to the newest.")
    ext_commands = ext.add_subparsers(dest="ext_command")

    def add_ext(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = ext_commands.add_parser(name, help=help_text, parents=[common])
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.add_argument("--php", help="Which installed PHP. Defaults to the newest.")
        sub.set_defaults(run=run)

        return sub

    ext_install = add_ext(
        "install", "Download one PHP does not ship — xdebug, redis, imagick.", _ext_install
    )
    ext_install.add_argument("name")
    ext_install.add_argument("version", nargs="?", default="latest")

    ext_enable = add_ext("enable", "Load an extension the build ships.", _ext_enable)
    ext_enable.add_argument("name")

    ext_disable = add_ext("disable", "Stop loading one.", _ext_disable)
    ext_disable.add_argument("name")

    add_ext("list", "Extensions this PHP ships, and which are loaded.", _ext_list)

    ext.set_defaults(run=_ext_list, ext_command=None)

    home = subparsers.add_parser(
        "home",
        parents=[common],
        help="Where runtimes, data and logs are kept.",
    )
    # On the group itself, not only on its subcommands. `portable home` is the
    # form that gets typed, and the form a plugin will call — leaving `--json`
    # off it would make the one command about configuration the one command that
    # cannot be read by a program.
    home.add_argument("--json", action="store_true", help="Machine-readable output.")
    home_commands = home.add_subparsers(dest="home_command")

    def add_home(name: str, help_text: str, run) -> argparse.ArgumentParser:
        sub = home_commands.add_parser(name, help=help_text, parents=[common])
        sub.add_argument("--json", action="store_true", help="Machine-readable output.")
        sub.set_defaults(run=run)

        return sub

    home_set = add_home("set", "Keep everything somewhere else, from now on.", _home_set)
    home_set.add_argument(
        "path",
        nargs="?",
        help="A directory. Created if it does not exist.",
    )
    home_set.add_argument(
        "--beside",
        action="store_true",
        help="Keep it next to the launcher, so the whole installation travels with it.",
    )

    add_home("clear", "Go back to the default location.", _home_clear)

    home.set_defaults(run=_home_show, home_command=None)

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

    endpoint = _await_daemon(pid)

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
        f"{status.get('sites', 0)} site(s), {status.get('services', 0)} database(s)",
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

    # Pinned rather than left to be resolved again. The daemon would otherwise
    # re-derive the location from its own environment and its own
    # `sys.executable`, and any disagreement — a pointer file edited between the
    # two, a `--home` that only the client saw — produces a daemon writing its
    # discovery file where no client will look for it.
    environment["PORTABLE_HOME"] = str(paths.root())

    return environment


def _ext_list(args) -> int:
    php = getattr(args, "php", None)
    result = Client().call("GET", f"/v1/php/extensions{f'?php={php}' if php else ''}")

    if args.json:
        return _emit(args, result, "")

    lines = []

    for entry in result["extensions"]:
        if not entry["shipped"]:
            # Loaded by the ini, absent from the build. PHP warns at startup, to
            # a log, and runs without it — so this line is the only place the
            # cause is visible before the symptom.
            state = "MISSING — the ini loads it, this build has no such file"
        else:
            state = "on" if entry["enabled"] else ""

        lines.append(f"  {entry['name']:<22} {state}".rstrip())

    return _emit(
        args,
        result,
        "\n".join([f"PHP {result['php']}  {result['ini']}", "", *lines]),
    )


def _ext_install(args) -> int:
    result = Client().call(
        "POST",
        "/v1/php/extensions/install",
        {"name": args.name, "version": args.version, "php": getattr(args, "php", None)},
        timeout=300,
    )

    unverified = "" if result["verified"] else "\n(PECL publishes no checksum for it.)"
    restarted = " Workers restarted." if result["restarted"] else ""

    return _emit(
        args,
        result,
        f"{result['name']} {result['version']} installed for PHP {result['php']} "
        f"and switched on.{restarted}{unverified}",
    )


def _ext_enable(args) -> int:
    return _ext_change(args, enabling=True)


def _ext_disable(args) -> int:
    return _ext_change(args, enabling=False)


def _ext_change(args, enabling: bool) -> int:
    result = Client().call(
        "POST",
        f"/v1/php/extensions/{'enable' if enabling else 'disable'}",
        {"name": args.name, "php": getattr(args, "php", None)},
    )

    if not result["changed"]:
        return _emit(
            args, result, f"{result['name']} was already {'on' if enabling else 'off'}."
        )

    note = " Workers restarted." if result["restarted"] else ""

    return _emit(
        args,
        result,
        f"{result['name']} is now {'on' if enabling else 'off'} for PHP {result['php']}.{note}",
    )


def _available(args) -> int:
    query = f"?name={args.runtime}" + (f"&branch={args.branch}" if args.branch else "")
    result = Client().call("GET", f"/v1/runtimes/available{query}")

    if args.json:
        return _emit(args, result, "")

    lines = []

    for entry in result["versions"]:
        marks = [part for part in (entry["note"], "installed" if entry["installed"] else "") if part]
        lines.append(f"  {entry['version']:<12} {'  '.join(marks)}".rstrip())

    if not lines:
        return _fail(args, "nothing-offered", f"The publisher lists no {args.runtime} builds.")

    return _emit(
        args,
        result,
        "\n".join([*lines, "", f"portable install {args.runtime} <version>"]),
    )


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


def _env(args) -> int:
    """
    Print what a shell needs to reach the runtimes.

    Printed for evaluation rather than applied, because a process cannot change
    its parent's environment — the reason every tool of this shape ends in
    `eval $(...)` or its equivalent.
    """
    result = Client().call("GET", "/v1/environment")

    if args.json:
        return _emit(args, result, "")

    shell = args.shell or ("powershell" if os.name == "nt" else "posix")
    directories = [entry for entry in result["path"] if entry]
    separator = ";" if os.name == "nt" else ":"

    if not directories:
        print("# Nothing installed — nothing to add to PATH.")

        return 0

    joined = separator.join(directories)

    if shell == "powershell":
        lines = [f'$env:PATH = "{joined}{separator}$env:PATH"']
        lines += [f'$env:{name} = "{value}"' for name, value in result["vars"].items()]
    elif shell == "cmd":
        lines = [f"set PATH={joined};%PATH%"]
        lines += [f"set {name}={value}" for name, value in result["vars"].items()]
    else:
        lines = [f'export PATH="{joined}{separator}$PATH"']
        lines += [f'export {name}="{value}"' for name, value in result["vars"].items()]

    print("\n".join(lines))

    return 0


def _run(args) -> int:
    """
    Run a command with the runtimes reachable, without changing anything.

    `portable run npm install` is the whole point of installing Node through a
    tool that refuses to touch PATH.
    """
    argv = [argument for argument in args.argv if argument]

    if not argv:
        print("Usage: portable run <command> [arguments]", file=sys.stderr)

        return 2

    import shutil
    import subprocess

    result = Client().call("GET", "/v1/environment")
    ours = [entry for entry in result["path"] if entry]
    separator = ";" if os.name == "nt" else ":"

    environment = dict(os.environ)

    # Empty entries dropped rather than joined blindly. On POSIX an empty
    # element in PATH means the *current directory* — so a machine with nothing
    # installed produced `PATH=":$PATH"` and would run whatever happened to sit
    # in the directory the command was typed in.
    environment["PATH"] = separator.join(
        [*ours, *[entry for entry in environment.get("PATH", "").split(separator) if entry]]
    )
    environment.update(result["vars"])

    executable = shutil.which(argv[0], path=environment["PATH"])

    if executable is None:
        installed = ", ".join(f"{entry['name']} {entry['version']}" for entry in result["runtimes"])

        print(
            f"{argv[0]!r} was not found, here or on your PATH. "
            f"Installed: {installed or 'nothing'}.",
            file=sys.stderr,
        )

        return 127

    # Falling back to the machine's own tools is deliberate: `portable run
    # composer install` should work, and composer is not something this
    # installs. Taking a *runtime* from the machine while saying nothing is not
    # — this command exists to make it certain which one runs, and silence is
    # how somebody spends an hour on a version difference that was never theirs.
    if _names_a_runtime(argv[0]) and not _is_ours(executable, ours):
        print(
            f"note: {argv[0]} came from {executable}, which portable does not "
            f"manage. `portable install {_runtime_behind(argv[0])}` would change that.",
            file=sys.stderr,
        )

    return subprocess.call([executable, *argv[1:]], env=environment)


#: Commands that *are* runtimes, and the runtime each belongs to. Taking one of
#: these from elsewhere is worth mentioning; anything else — composer, a
#: project's own script — is expected to come from the machine.
RUNTIME_COMMANDS = {
    "node": "node",
    "npm": "node",
    "npx": "node",
    "php": "php",
    "psql": "postgres",
    "redis-cli": "redis",
    "mariadb": "mariadb",
    "mysql": "mariadb",
}


def _names_a_runtime(command: str) -> bool:
    return Path(command).stem.lower() in RUNTIME_COMMANDS


def _runtime_behind(command: str) -> str:
    return RUNTIME_COMMANDS.get(Path(command).stem.lower(), command)


def _is_ours(executable: str, directories: list[str]) -> bool:
    resolved = Path(executable).resolve()

    return any(resolved.is_relative_to(Path(directory).resolve()) for directory in directories)


def _service_add(args) -> int:
    result = Client().call(
        "POST",
        "/v1/services/add",
        {
            "kind": args.kind,
            "name": args.name or args.kind,
            "version": args.version,
            "port": args.port,
        },
        # First start initialises the data directory, which is not quick.
        timeout=600,
    )

    return _emit(
        args,
        result,
        f"{result['kind']} on 127.0.0.1:{result['port']} as {result['user']}\n"
        f"data: {result['data']}",
    )


def _service_remove(args) -> int:
    result = Client().call("POST", "/v1/services/remove", {"name": args.name}, timeout=60)

    return _emit(
        args,
        result,
        f"Stopped {result['removed']}. Its data is still at {result['data_kept']}.",
    )


def _service_list(args) -> int:
    result = Client().call("GET", "/v1/services")

    if args.json:
        return _emit(args, result, "")

    if not result["services"]:
        print("No databases. Add one with `portable service add postgres`.")

        return 0

    for service in result["services"]:
        state = f"127.0.0.1:{service['port']}" if service["running"] else "stopped"

        print(f"  {service['name']:<12} {service['kind']:<10} {state:<20} user {service['user']}")

    return 0


def _service_help(args) -> int:
    print("Usage: portable service {add,remove,list}")

    return 2


def _site_help(args) -> int:
    print("Usage: portable site {add,remove,list}")

    return 2


def _home_show(args) -> int:
    """
    Where everything lives, and what decided that.

    The source is reported, not just the path. Two of the three ways it can be
    set are invisible from the outside — a variable exported in some other
    shell, a file written months ago — and "why is it installing over there" is
    otherwise a question with no way to answer it.
    """
    home, source = paths.resolved()

    # `--home` works by setting the variable, so that is what `resolved()` sees
    # and reports. Repeating it back to somebody who typed the flag would send
    # them looking for an export that does not exist.
    if getattr(args, "home", None):
        source = "--home"

    is_bundle = paths.bundle() is not None

    payload = {
        "home": str(home),
        "source": source,
        "exists": home.exists(),
        "settable": is_bundle,
        "default": str(paths.default_root()),
    }

    if args.json:
        return _emit(args, payload, "")

    print(f"{home}")
    print(f"  set by: {source}")

    if not home.exists():
        print("  nothing there yet — it is created on the first install")
    else:
        print(f"  holding: {_describe_contents(home)}")

    if is_bundle:
        print("\nTo move it: portable home set <path>, or --beside to keep it next to")
        print("the launcher so the whole installation travels on one drive.")
    else:
        print("\nThis is a source checkout, so there is no launcher to record a setting")
        print("beside. Set PORTABLE_HOME to move it.")

    return 0


def _home_set(args) -> int:
    if args.beside == bool(args.path):
        return _fail(
            args,
            "no-path",
            "Give a directory, or --beside to keep everything next to the launcher.",
        )

    # A running daemon holds the old location: it has already opened its logs
    # there, its runtimes are there, and its discovery file — the only way any
    # client finds it — is there too. Moving the setting out from under it would
    # leave a daemon nothing can reach and ports nothing can free.
    if discovery.read() is not None:
        return _fail(
            args,
            "still-running",
            "Stop the daemon first: portable down. It is using the current location, "
            "and would be unreachable the moment this changes.",
        )

    previous, _ = paths.resolved()
    home = paths.set_home(paths.BESIDE if args.beside else args.path)

    payload = {"home": str(home), "previous": str(previous)}
    lines = [f"Everything will now be kept in {home}."]

    # Nothing is moved. Copying hundreds of megabytes of runtimes on the way
    # past would be a surprising thing for a settings command to do, and if it
    # failed halfway it would do so across two locations. But leaving the old
    # one unmentioned is how somebody re-downloads PHP without understanding
    # why, and never reclaims the disk.
    if previous != home and previous.exists() and any(previous.iterdir()):
        payload["leftBehind"] = str(previous)
        lines.append(
            f"\n{previous} still holds {_describe_contents(previous)}.\n"
            "Nothing was moved. Either copy that directory across, or re-install\n"
            "what you need and delete it."
        )

    return _emit(args, payload, "\n".join(lines))


def _home_clear(args) -> int:
    if discovery.read() is not None:
        return _fail(
            args,
            "still-running",
            "Stop the daemon first: portable down.",
        )

    paths.clear_home()
    home, source = paths.resolved()

    return _emit(
        args,
        {"home": str(home), "source": source},
        f"Back to {home}."
        + (
            "\nNote that PORTABLE_HOME is set in this shell and still wins."
            if source == "PORTABLE_HOME"
            else ""
        ),
    )


def _describe_contents(home: Path) -> str:
    """A one-line inventory, so a location can be recognised without opening it."""
    parts = []

    for name, label in (("runtimes", "runtime"), ("data", "database")):
        directory = home / name

        if directory.is_dir():
            count = len([child for child in directory.iterdir() if child.is_dir()])

            if count:
                parts.append(f"{count} {label}{'s' if count != 1 else ''}")

    size = sum(path.stat().st_size for path in home.rglob("*") if path.is_file())

    if size:
        parts.append(f"{size // 1048576} MB")

    return ", ".join(parts) if parts else "nothing yet"


def _await_daemon(pid: int | None = None, timeout: float = 60.0) -> discovery.Endpoint | None:
    """
    Wait for the daemon to answer, distinguishing slow from dead.

    Two different situations were previously one number. A daemon that has died
    is answerable immediately — there is nothing left to wait for — while one
    that is merely slow deserves considerably longer than it used to get: a cold
    Windows machine can spend fifteen seconds starting an interpreter and
    importing, before any of this tool's own code runs. That is not a fault, and
    a timeout tuned for a warm developer laptop turns it into one. It showed up
    as CI failing once in four runs on `windows-latest`, which is the same cold
    start a person gets on the first `portable up` after a reboot.

    So: patient while the process is alive, immediate once it is not.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if pid is not None and not spawn.is_running(pid):
            return None

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
