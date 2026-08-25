"""
A full-screen view of what the supervisor is doing, with somewhere to type.

Everything here is already available from the command line — `status`, `site
list`, `service list`, `logs -f` — and the reason to have it in one screen is
that those answers are usually wanted together. Which PHP worker died is a
question about the log; whether it came back is a question about the process
table; and reading them in turn means alternating between two commands while the
thing you are watching moves.

The command line at the bottom is the other half of that. A screen you can only
watch sends you back to another window to act on what it showed, and then the
screen is behind that window. What you type and what it answers go into the same
stream as the services' own output, in the order it happened — which is what
makes "I added a site and then this appeared in the log" a thing you can read
rather than reconstruct.

It is a client of the daemon like every other, which is the whole point of the
API having been built first: nothing was added to the daemon for this.

Two habits are kept from the rest of the tool. Nothing is invented when the
daemon is not running — the screen says so and keeps offering to look again,
because a dashboard that empties itself is indistinguishable from one that has
broken. And the logs are read from their files rather than through the API, for
the same reason `portable logs` does: the daemon does not read them either, so
going through it would add a hop and a dependency on the daemon being alive.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, ClassVar

from . import VERSION, logs, paths
from .daemon.client import CallFailed, Client, NotRunning

#: How often the tables are refreshed.
#:
#: A second is fast enough to feel live for things that change on the scale of a
#: process restarting, and slow enough that the daemon is not answering a
#: question every frame for a screen nobody is looking at.
REFRESH = 1.0

#: Commands that cannot be run from inside the screen, and why.
#:
#: Each of these would either wait for an answer nobody can give here, or take
#: the ground out from under the screen itself. Refusing by name and saying so
#: is better than a window that stops responding for a reason nobody can see.
REFUSED = {
    "dash": "you are in it",
    "shell": "this is one",
    "upgrade": "it stops the daemon and replaces the folder this is running from",
}

#: Lines kept in the log pane.
#:
#: Enough to scroll back through a start-up, not enough to hold a session's
#: worth of Caddy JSON in memory for as long as the window is open.
SCROLLBACK = 2000


class Unavailable(RuntimeError):
    """The dashboard cannot run here, with the reason."""


def available() -> bool:
    """Whether the libraries the screen is drawn with are present."""
    try:
        import textual  # noqa: F401
    except ImportError:
        return False

    return True


@dataclass
class Snapshot:
    """Everything the screen shows, as of one moment."""

    running: bool = False
    version: str = ""
    home: str = ""
    port: int | None = None
    https_port: int | None = None
    router_error: str | None = None
    processes: list[dict] = field(default_factory=list)
    sites: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    error: str = ""

    @property
    def summary(self) -> str:
        """The one line at the top, which is the answer to "is it working"."""
        if not self.running:
            return f"portable {VERSION} — daemon not running — {self.home}"

        where = f"http://…:{self.port}" if self.port else "nothing served"

        if self.https_port:
            where += f"  https://…:{self.https_port}"

        return (
            f"portable {VERSION} — {where} — "
            f"{len(self.sites)} site{'s' if len(self.sites) != 1 else ''}, "
            f"{len(self.services)} database{'s' if len(self.services) != 1 else ''}, "
            f"{len(self.processes)} process{'es' if len(self.processes) != 1 else ''}"
        )


def look() -> Snapshot:
    """
    Ask the daemon everything the screen needs, in one go.

    Failure is a state rather than an exception. The daemon being down is the
    ordinary case at least once per session — it is how every session starts —
    and a screen that raised would have to be restarted for the thing it is
    watching to come back.
    """
    home, _ = paths.resolved()
    snapshot = Snapshot(home=str(home))

    try:
        client = Client()
        status = client.status()
    except (NotRunning, CallFailed) as error:
        snapshot.error = str(error)

        return snapshot

    snapshot.running = True
    snapshot.version = status.get("version", "")
    snapshot.port = status.get("port")
    snapshot.https_port = status.get("https_port")
    snapshot.router_error = status.get("router_error")
    snapshot.processes = status.get("processes", [])

    with contextlib.suppress(NotRunning, CallFailed):
        snapshot.sites = client.call("GET", "/v1/sites").get("sites", [])

    with contextlib.suppress(NotRunning, CallFailed):
        snapshot.services = client.call("GET", "/v1/services").get("services", [])

    return snapshot


def _suggestions() -> list[str]:
    """
    What the command line offers as you type.

    Whole commands rather than a grammar: the point is to save typing and to
    remind somebody what exists, not to be a parser. The list comes from the
    parser itself so a new command appears here without anybody remembering to
    add it.
    """
    from . import cli

    commands = cli._parser()._subparsers._group_actions[0].choices
    offered = [name for name in commands if name not in REFUSED]

    return sorted(
        [
            *offered,
            "site add ",
            "site list",
            "site remove ",
            "service add ",
            "service list",
            "install php",
            "install caddy",
            "ext list",
            "ext install ",
            "logs php",
            "status --json",
        ]
    )


def build() -> Any:
    """
    The application, built only when asked.

    Imported inside the function so that `portable dash` can explain itself on a
    machine without the libraries, rather than failing at import time and taking
    every other command with it.
    """
    if not available():
        raise Unavailable(
            "The dashboard needs `textual`, which the bundle carries and a source "
            "checkout does not.\n"
            "    pip install textual\n"
            "Everything it shows is on the command line too: `status`, `site list`, "
            "`service list`, `logs -f`."
        )

    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.suggester import SuggestFromList
    from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

    class Dashboard(App):
        CSS = """
        Screen { layout: vertical; }
        #summary { height: 1; padding: 0 1; background: $panel; color: $text; }
        #tables { height: 40%; }
        #command { dock: bottom; border: none; background: $surface; }
        #processes { width: 55%; }
        #right { width: 45%; }
        DataTable { height: 1fr; }
        RichLog { height: 1fr; border-top: solid $primary; }
        """

        # Function keys, and `priority` so nothing swallows them.
        #
        # Single letters had to go the moment there was somewhere to type: a
        # screen where `site add q...` closes the window is one nobody types in
        # twice. The obvious replacements are taken — `ctrl+c` is copy inside a
        # text field, and `ctrl+p` is textual's own command palette — which is
        # the sort of thing found by pressing the key rather than by reasoning
        # about it.
        BINDINGS: ClassVar = [
            Binding("f10", "quit", "Quit", priority=True),
            Binding("f5", "refresh", "Refresh", priority=True),
            Binding("f2", "toggle_follow", "Pause logs", priority=True),
            Binding("up", "earlier", "Previous", priority=True),
            Binding("down", "later", "Next", priority=True),
        ]

        TITLE = "portable"

        def __init__(self, follow: str | None = None) -> None:
            super().__init__()
            self._follow = follow
            self._paused = False
            self._stop = None
            self._history: list[str] = []
            self._recalled = 0

        def compose(self) -> ComposeResult:
            yield Header()
            yield Static("starting…", id="summary")

            with Horizontal(id="tables"):
                yield DataTable(id="processes")

                with Vertical(id="right"):
                    yield DataTable(id="sites")
                    yield DataTable(id="services")

            yield RichLog(id="log", highlight=False, markup=False, max_lines=SCROLLBACK)
            yield Input(
                placeholder="a command, without `portable` in front of it",
                id="command",
                suggester=SuggestFromList(_suggestions(), case_sensitive=False),
            )
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#processes", DataTable).add_columns(
                "process", "state", "pid", "restarts"
            )
            self.query_one("#sites", DataTable).add_columns("site", "url", "php")
            self.query_one("#services", DataTable).add_columns("database", "kind", "port")

            self.set_interval(REFRESH, self.action_refresh)
            self.action_refresh()
            self.watch_logs()
            self.query_one("#command", Input).focus()

        def action_refresh(self) -> None:
            self.collect()

        def action_toggle_follow(self) -> None:
            self._paused = not self._paused
            self.query_one("#log", RichLog).write(
                "-- paused --" if self._paused else "-- following --"
            )

        # ------------------------------------------------------------ commands

        def on_input_submitted(self, event) -> None:
            line = event.value.strip()
            self.query_one("#command", Input).value = ""

            if not line:
                return

            self._history.append(line)
            self._recalled = len(self._history)

            self.query_one("#log", RichLog).write(f"> {line}")

            refusal = self._refused(line)

            if refusal:
                self.query_one("#log", RichLog).write(f"  not from here: {refusal}")

                return

            self.run_worker(
                lambda: self._run(line), thread=True, group="command", exclusive=False
            )

        def _refused(self, line: str) -> str:
            import shlex

            try:
                first = (shlex.split(line) or [""])[0]
            except ValueError:
                return "the quotes do not balance"

            if first in REFUSED:
                return REFUSED[first]

            # `logs -f` never returns, and the pane below is already following.
            if first == "logs" and ("-f" in line or "--follow" in line):
                return "the pane below is already following; drop the -f"

            if first == "purge" and "--yes" not in line:
                return "it asks a question this screen cannot put to you; add --yes if you mean it"

            return ""

        def _run(self, line: str) -> None:
            """
            One typed line, through the ordinary parser and handlers.

            The same rule the shell follows: a second dispatch is a second
            implementation, and it drifts. Output is captured rather than
            printed, because stdout here belongs to the screen.
            """
            import contextlib as ctx
            import io
            import shlex

            from . import cli

            captured = io.StringIO()

            try:
                with ctx.redirect_stdout(captured), ctx.redirect_stderr(captured):
                    cli.main(shlex.split(line))
            except SystemExit:
                # argparse raises this for a bad command. In a screen it would
                # be the last thing that ever happened.
                pass
            except Exception as error:  # noqa: BLE001
                captured.write(f"{type(error).__name__}: {error}\n")

            for written in captured.getvalue().splitlines():
                self.call_from_thread(self.query_one("#log", RichLog).write, f"  {written}")

            # A command that changed something should be visible in the tables
            # before the next tick, which is a whole second of wondering.
            self.call_from_thread(self.action_refresh)

        def action_earlier(self) -> None:
            self._recall(-1)

        def action_later(self) -> None:
            self._recall(1)

        def _recall(self, step: int) -> None:
            if not self._history:
                return

            self._recalled = max(0, min(len(self._history), self._recalled + step))
            command = self.query_one("#command", Input)
            command.value = (
                self._history[self._recalled] if self._recalled < len(self._history) else ""
            )
            command.cursor_position = len(command.value)

        # ------------------------------------------------------------- workers

        def collect(self) -> None:
            self.run_worker(self._collect, thread=True, exclusive=True, group="status")

        def _collect(self) -> None:
            snapshot = look()
            self.call_from_thread(self.show, snapshot)

        def watch_logs(self) -> None:
            self.run_worker(self._watch_logs, thread=True, group="logs")

        def _watch_logs(self) -> None:
            import threading

            self._stop = threading.Event()
            sources = logs.resolve(self._follow)
            width = max((len(source.name) for source in sources), default=0)

            for source in sources:
                for line in logs.tail(source, 5):
                    self.call_from_thread(self.say, source.name, line, width)

            for name, line in logs.follow(sources, self._stop):
                if not self._paused:
                    self.call_from_thread(self.say, name, line, width)

        # ----------------------------------------------------------- rendering

        def say(self, name: str, line: str, width: int) -> None:
            self.query_one("#log", RichLog).write(logs.render(name, line, width, colour=False))

        def show(self, snapshot: Snapshot) -> None:
            self.query_one("#summary", Static).update(snapshot.summary)

            if snapshot.router_error and not snapshot.port:
                # The reason nothing is being served, in the place somebody is
                # already looking. It is otherwise only in `status`.
                self.query_one("#summary", Static).update(
                    f"{snapshot.summary}\n{snapshot.router_error.splitlines()[0]}"
                )

            processes = self.query_one("#processes", DataTable)
            processes.clear()

            for entry in snapshot.processes:
                processes.add_row(
                    entry.get("name", ""),
                    entry.get("state", ""),
                    str(entry.get("pid") or ""),
                    str(entry.get("restarts", 0)),
                )

            sites = self.query_one("#sites", DataTable)
            sites.clear()

            for entry in snapshot.sites:
                sites.add_row(
                    entry.get("name", ""), entry.get("url", ""), entry.get("php") or "newest"
                )

            services = self.query_one("#services", DataTable)
            services.clear()

            for entry in snapshot.services:
                services.add_row(
                    entry.get("name", ""), entry.get("kind", ""), str(entry.get("port") or "")
                )

        def on_unmount(self) -> None:
            if self._stop is not None:
                self._stop.set()

    return Dashboard
