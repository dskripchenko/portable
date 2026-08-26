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
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from . import VERSION, logs, mascot, paths
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

#: How often the "still going" indicator moves.
#:
#: Five times a second, which is fast enough to read as motion rather than as a
#: clock. The tables refresh once a second and that is plenty for them; this is
#: answering a different question — not "what is true" but "is anything still
#: happening" — and a still picture answers it wrongly.
TICK = 0.2

#: The frames it cycles through. Plain characters, because a terminal that
#: cannot draw braille would show the indicator as boxes and make the screen
#: look broken at exactly the moment it is meant to reassure.
FRAMES = "|/-\\"

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
    def masthead(self) -> list[str]:
        """
        The top block: the same facts, one per line.

        Four lines because the face beside it is four rows tall, and three of
        them would otherwise be empty — a picture costing three rows of a screen
        where rows are the scarce thing. Split across lines the facts also read
        better than they did crammed into one: the version, where it is served,
        what is there, and where it all lives.
        """
        if not self.running:
            # The third line is what to do about it. An empty row here is the
            # only place in this block that says nothing, and it says it at the
            # moment somebody most needs telling.
            return [
                f"portable {VERSION}",
                "daemon not running",
                "portable up   starts it",
                self.home,
            ]

        where = f"http :{self.port}" if self.port else "nothing served"

        if self.https_port:
            where += f"   https :{self.https_port}"

        return [
                f"portable {VERSION}",
                where,
                (
                    f"{len(self.sites)} site{'s' if len(self.sites) != 1 else ''}, "
                    f"{len(self.services)} database"
                    f"{'s' if len(self.services) != 1 else ''}, "
                    f"{len(self.processes)} process"
                    f"{'es' if len(self.processes) != 1 else ''}"
                ),
                self.home,
        ]

    @property
    def summary(self) -> str:
        """The one line at the top, which is the answer to "is it working"."""
        if not self.running:
            return f"portable {VERSION} — daemon not running — {self.home}"

        # `http :80` rather than `http://…:80`. The ellipsis stood in for a
        # hostname that is different for every site, and the sites table below
        # gives each of them in full — so it was punctuation pretending to be
        # information.
        where = f"http :{self.port}" if self.port else "nothing served"

        if self.https_port:
            where += f"   https :{self.https_port}"

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


class _Streaming:
    """
    Somewhere for a command's output to go, a line at a time.

    Collecting it and showing it at the end was the first version, and a
    download then looked exactly like a freeze: the command was working, saying
    so, and none of it arrived until it had finished. Anything that prints as it
    goes now appears as it goes.

    Stands in for `sys.stdout` while a command runs, so it answers what code
    reasonably asks of one — `isatty` in particular, which decides whether
    output is coloured. False, because this is a widget and the escape codes
    would be printed rather than obeyed.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text

        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._say(line)

        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._say(self._pending)
            self._pending = ""

    def isatty(self) -> bool:
        return False

    def _say(self, line: str) -> None:
        # Through a method called on the message loop, not a lookup done here:
        # the DOM belongs to that loop, and this runs in the thread.
        self._app.call_from_thread(self._app.note, f"  {line}")


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

    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.suggester import SuggestFromList
    from textual.widgets import DataTable, Footer, Input, RichLog, Static

    class Dashboard(App):
        # Every pane is bordered and titled. Without that the screen is four
        # tables of anonymous columns and a stream, and telling where one ends
        # and the next begins is left to the reader — who has to do it every
        # time they look.
        CSS = """
        Screen { layout: vertical; background: $surface; }

        /* Four rows tall, and every one of them says something. A block of
           solid colour that size shouts down the tables below it, so the
           accent stays on the first line — the one that answers "is it up" —
           and the rest sits on the ordinary background. */
        #masthead { height: 4; }
        #titles { width: 1fr; height: 4; }

        #summary {
            height: 1;
            padding: 0 1;
            color: $primary;
            text-style: bold;
        }

        #detail {
            height: 3;
            padding: 0 1;
            color: $text-muted;
        }

        #mascot {
            width: 10;
            height: 4;
            padding: 0 2 0 1;
            color: $primary;
        }

        /* Rows are the scarce thing on this screen. On a short terminal the
           masthead gives back three of them and the face goes, because a
           picture that costs a table row is a bad trade. */
        #masthead.-tight { height: 1; }
        #titles.-tight { height: 1; }

        #tables { height: 45%; }

        #processes { width: 3fr; }
        #right { width: 2fr; }

        DataTable {
            border: round $panel;
            padding: 0 1;
            scrollbar-size: 1 1;
        }

        #processes { height: 100%; }
        #sites, #services { height: 1fr; }

        RichLog {
            border: round $panel;
            padding: 0 1;
            scrollbar-size: 1 1;
        }

        #busy {
            height: 1;
            padding: 0 1;
            background: $warning;
            color: $text 90%;
            text-style: bold;
        }

        #command {
            dock: bottom;
            border: none;
            border-top: solid $panel;
            background: $surface;
            padding: 0 1;
        }
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
            # Not shown in the footer: arrows recalling history is what arrows
            # do in every shell, and the room is better spent on the keys nobody
            # would guess.
            Binding("up", "earlier", "Previous", priority=True, show=False),
            Binding("down", "later", "Next", priority=True, show=False),
        ]

        TITLE = "portable"

        def __init__(self, follow: str | None = None) -> None:
            super().__init__()
            self._follow = follow
            self._paused = False
            self._stop = None
            self._history: list[str] = []
            self._recalled = 0
            self._busy: str | None = None
            self._mood = "stopped"
            self._since = 0.0
            self._frame = 0
            self._leaving = False

        def compose(self) -> ComposeResult:
            # No `Header`: it draws the application's name in a bar of its own,
            # and the line below already says the name along with everything
            # else worth knowing. Two of the three lines at the top used to say
            # "portable" and nothing more.
            # The summary and the face side by side. The face answers the same
            # question the summary does — is anything wrong — and answers it
            # from further away: you see a shape change across the room before
            # you read a line of text.
            with Horizontal(id="masthead"):
                with Vertical(id="titles"):
                    # Two widgets rather than one styled string: the accent
                    # belongs to the stylesheet, and a rich style string cannot
                    # name a theme colour — it fails at the moment it is drawn,
                    # which for a background worker means nothing drawn at all.
                    yield Static("starting…", id="summary")
                    yield Static("", id="detail")

                yield Static(mascot.rendered("stopped"), id="mascot")

            with Horizontal(id="tables"):
                yield DataTable(id="processes")

                with Vertical(id="right"):
                    yield DataTable(id="sites")
                    yield DataTable(id="services")

            yield RichLog(id="log", highlight=False, markup=False, max_lines=SCROLLBACK)
            # In the flow rather than docked. Two widgets both claiming the
            # bottom edge fought over it, and the one that lost was the one that
            # exists to be noticed.
            yield Static("", id="busy")
            yield Input(
                placeholder="a command, without `portable` in front of it",
                id="command",
                suggester=SuggestFromList(_suggestions(), case_sensitive=False),
            )
            yield Footer()

        def on_mount(self) -> None:
            processes = self.query_one("#processes", DataTable)
            processes.add_columns("process", "state", "pid", "restarts")
            processes.border_title = "processes"

            sites = self.query_one("#sites", DataTable)
            sites.add_columns("site", "url", "php")
            sites.border_title = "sites"

            services = self.query_one("#services", DataTable)
            services.add_columns("database", "kind", "port")
            services.border_title = "databases — enter opens a prompt"

            # Rows rather than cells: the cursor picks out a database, not the
            # port of a database, and it is the row that Enter acts on. With
            # the default the highlight is one cell wide and Enter reports a
            # cell, which is neither what it looks like nor what it means.
            services.cursor_type = "row"

            log = self.query_one("#log", RichLog)
            log.border_title = f"log — {self._follow or 'everything'}"

            # No border title on the input: with only a top border it renders
            # across the corner, and the placeholder already says what it is.

            self.query_one("#busy", Static).display = False
            self.set_interval(TICK, self.tick)
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
            from . import cli

            try:
                first = (cli.split(line) or [""])[0]
            except ValueError:
                return "the quotes do not balance"

            if first in REFUSED:
                return REFUSED[first]

            # An interactive client with its output captured is a prompt
            # nobody can see and a session nobody can leave. There is a way to
            # do this from here, and it is the row above.
            words = cli.split(line)

            if first == "service" and len(words) > 1 and words[1] == "cli":
                return "select the database in the table above and press enter"

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

            from . import cli

            started = time.monotonic()
            self.call_from_thread(self.busy, line)
            stream = _Streaming(self)

            try:
                with ctx.redirect_stdout(stream), ctx.redirect_stderr(stream):
                    cli.main(cli.split(line))
            except SystemExit:
                # argparse raises this for a bad command. In a screen it would
                # be the last thing that ever happened.
                pass
            except Exception as error:  # noqa: BLE001
                stream.write(f"{type(error).__name__}: {error}\n")
            finally:
                stream.flush()

            took = time.monotonic() - started

            # Only when it was long enough to have been worth wondering about.
            if took > 1.0:
                self.call_from_thread(self.note, f"  ({took:.0f}s)")

            self.call_from_thread(self.busy, None)

            # A command that changed something should be visible in the tables
            # before the next tick, which is a whole second of wondering.
            self.call_from_thread(self.action_refresh)

        def _find(self, selector: str, kind: type) -> Any:
            """
            A widget if the screen has one, `None` if it does not.

            Everything here can be reached from the thread running a command,
            and that thread outlives neither the screen's arrival nor its
            departure. A command finishing a moment before the screen is
            composed, or a moment after F10, used to raise `NoMatches` deep in
            the framework — which on one of four machines it duly did, and in a
            real session would have left the busy bar spinning over a command
            that had already finished.
            """
            try:
                return self.query_one(selector, kind)
            except NoMatches:
                return None

        def note(self, text: str) -> None:
            """Write a line to the log pane, from the message loop."""
            pane = self._find("#log", RichLog)

            if pane is not None:
                pane.write(text)

        def busy(self, line: str | None) -> None:
            """
            Say that something is running, and what.

            Without it a command that takes a minute is indistinguishable from
            one that has hung — the screen keeps redrawing, the tables keep
            refreshing, and nothing says anybody is waiting on anything.
            """
            self._busy = line
            self._since = time.monotonic() if line else 0.0
            self._frame = 0

            indicator = self._find("#busy", Static)

            if indicator is None:
                return

            indicator.display = line is not None

            # The bar above says what is running, loudly. Repeating it in the
            # placeholder says it twice and takes away the one line telling
            # somebody what the field is for.

            if line is None:
                indicator.update("")

        def tick(self) -> None:
            """
            Move the indicator.

            A label that says "running" and never changes is a still picture,
            and a still picture cannot tell "working" from "hung" — which is the
            whole question during a thirty-second connect timeout, when nothing
            is printed at all.
            """
            if not self._busy:
                return

            self._frame = (self._frame + 1) % len(FRAMES)
            elapsed = time.monotonic() - self._since

            indicator = self._find("#busy", Static)

            if indicator is None:
                return

            self.wear("working")
            indicator.update(
                f"{FRAMES[self._frame]}  {self._busy}   {elapsed:.0f}s   "
                f"(F10 twice to leave it running)"
            )

        def _anything_wrong(self, snapshot: Snapshot) -> bool:
            """
            Whether the face should stop looking pleased.

            The same three things the tables mark in red: a process that is not
            running, a supervisor that cannot say what it is doing, and nothing
            being served because the router would not start. Restart counts are
            deliberately not here — the tables colour those amber, and amber is
            "look at this", not "something is broken".
            """
            if snapshot.error:
                return True

            if snapshot.router_error and not snapshot.port:
                return True

            return any(entry.get("state") != "running" for entry in snapshot.processes)

        def wear(self, mood: str) -> None:
            """Put a face on, if there is anywhere to put it."""
            corner = self._find("#mascot", Static)

            if corner is not None:
                corner.update(mascot.rendered(mood, self._frame))

        def on_resize(self, event) -> None:
            self._fit(event.size.height, event.size.width)

        def _fit(self, height: int, width: int) -> None:
            """
            Give the three rows back when the terminal cannot spare them.

            Below twenty-four rows the masthead collapses to the single line it
            used to be, and the face goes with it. It is decoration on a screen
            whose job is to report what is running, and decoration loses.
            """
            tight = height < 24 or width < 60

            for selector, kind in (("#masthead", Horizontal), ("#titles", Vertical)):
                found = self._find(selector, kind)

                if found is not None:
                    found.set_class(tight, "-tight")

            for selector in ("#detail", "#mascot"):
                found = self._find(selector, Static)

                if found is not None:
                    found.display = not tight

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

        #: How a line's severity is drawn. The same reading `portable logs`
        #: does, arriving here rather than being thrown away: a screen whose
        #: whole purpose is showing what went wrong should not render a failure
        #: in the same grey as "config is unchanged".
        SEVERITY: ClassVar = {"error": "bold red", "warn": "yellow", "": ""}

        def say(self, name: str, line: str, width: int) -> None:
            label = f"{name:<{width}} | " if width else f"{name} | "
            written = Text(label, style="dim")
            written.append(line, style=self.SEVERITY[logs.severity(line)])

            pane = self._find("#log", RichLog)

            if pane is not None:
                pane.write(written)

        def show(self, snapshot: Snapshot) -> None:
            # Reached from a worker thread that outlives the screen: a refresh
            # in flight when F10 is pressed arrives after there is anywhere to
            # put it. One of four CI machines caught this as `NoMatches` on
            # `#summary`, which is a crash landing in a place with no crash to
            # report — the worker dies and the screen is already gone.
            summary = self._find("#summary", Static)

            if summary is None:
                return

            running = f"  —  running: {self._busy}" if self._busy else ""
            head, *rest = snapshot.masthead
            summary.update(head + running)

            detail = self._find("#detail", Static)

            if detail is not None:
                detail.update("\n".join(rest))
            self._mood = mascot.state_for(
                running=snapshot.running,
                failing=self._anything_wrong(snapshot),
                busy=bool(self._busy),
            )
            self.wear(self._mood)

            if snapshot.router_error and not snapshot.port:
                # The reason nothing is being served, in the place somebody is
                # already looking. It is otherwise only in `status`.
                summary.update(
                    f"{snapshot.summary}\n{snapshot.router_error.splitlines()[0]}"
                )

            processes = self.query_one("#processes", DataTable)
            processes.clear()

            # An empty table with column headings and nothing under them looks
            # broken. Saying why it is empty, and what would fill it, costs one
            # row and answers the question somebody is about to ask.
            if not snapshot.processes:
                # In the second column, not the last. A hint under "restarts"
                # reads as a value of it.
                # Short. A hint long enough to overflow its column turns the
                # table into something with a scrollbar, which reads as data
                # too wide to show rather than as a table with nothing in it.
                processes.add_row(
                    "daemon not running" if not snapshot.running else "nothing running",
                    "type: up" if not snapshot.running else "",
                    "",
                    "",
                )

            for entry in snapshot.processes:
                state = entry.get("state", "")
                restarts = entry.get("restarts", 0)

                processes.add_row(
                    entry.get("name", ""),
                    # Anything but running is the reason somebody opened this.
                    Text(state, style="" if state == "running" else "bold red"),
                    str(entry.get("pid") or ""),
                    # A worker retiring itself is routine; a handful of restarts
                    # is a worker that keeps dying, and the two look identical
                    # as a number.
                    Text(str(restarts), style="yellow" if restarts >= 3 else ""),
                )

            sites = self.query_one("#sites", DataTable)
            sites.clear()

            if not snapshot.sites:
                sites.add_row("none yet", "type: site add", "")

            for entry in snapshot.sites:
                sites.add_row(
                    entry.get("name", ""), entry.get("url", ""), entry.get("php") or "newest"
                )

            services = self.query_one("#services", DataTable)
            services.clear()

            if not snapshot.services:
                services.add_row("none yet", "type: service add", "")

            for entry in snapshot.services:
                services.add_row(
                    entry.get("name", ""), entry.get("kind", ""), str(entry.get("port") or "")
                )

        def on_data_table_row_selected(self, event) -> None:
            """
            A row in the databases table, chosen with Enter or a click.

            The tables are otherwise a display, and a display with a cursor in
            it that does nothing when you press Enter is a thing people press
            twice and then stop trusting.
            """
            if event.data_table.id != "services":
                return

            name = str(event.data_table.get_row(event.row_key)[0])

            if name == "none yet":
                return

            self.open_client(name)

        def open_client(self, name: str) -> None:
            """
            Step out of the way and hand the terminal to the client.

            `suspend()` restores the terminal to what it was, runs the thing,
            and puts the screen back — which is the only way an interactive
            program can work from inside a full-screen application. Nothing is
            emulated: it is the real client on the real terminal, and this
            screen returns when it exits.
            """
            import subprocess

            try:
                found = Client().call("POST", "/v1/services/client", {"name": name})
            except (NotRunning, CallFailed) as error:
                self.note(f"  {error}")

                return

            try:
                with self.suspend():
                    subprocess.call(found["argv"])
            except Exception as error:  # noqa: BLE001
                # Headless, or a terminal that cannot be given back. Saying so
                # beats a screen that appears to have ignored the keypress.
                self.note(f"  could not open {found['argv'][0]}: {error}")

                return

            self.note(f"  {name}: closed the client")
            self.action_refresh()

        def action_quit(self) -> None:
            """
            Leave, saying what is still going on if anything is.

            A command runs in a thread, and a thread inside a network call does
            not notice being asked to stop. The screen goes, the process stays,
            and what is left is a blinking cursor in a terminal that looks
            wedged — reported after quitting during an `install` that was
            waiting out an unreachable host.

            So it is said, once, and the second press leaves regardless. The
            daemon owns everything that matters; abandoning a download costs the
            part of it that was fetched, and even that is kept for the next
            attempt.
            """
            if self._busy and not self._leaving:
                self._leaving = True
                self.query_one("#log", RichLog).write(
                    f"  {self._busy} is still running. F10 again to leave it."
                )

                return

            self.exit()

        def on_unmount(self) -> None:
            if self._stop is not None:
                self._stop.set()

    return Dashboard
