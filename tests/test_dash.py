"""
The full-screen view.

A client of the daemon like every other command — nothing was added to the
daemon for it, which is what building the API first was for.

The tests that matter most here are not about the layout. They are about the
screen behaving when the thing it watches is absent, and about the four
libraries it is drawn with being the only four it needs.
"""

from __future__ import annotations

import builtins
import inspect
import sys
import time

import pytest

from portable import VERSION, dash

textual = pytest.importorskip("textual", reason="the dashboard's libraries are vendored")


class TestWhatItShows:
    def test_a_daemon_that_is_not_running_is_a_state_and_not_a_failure(self):
        """
        The ordinary case at least once per session — it is how every session
        starts. A screen that raised would have to be restarted for the thing it
        is watching to come back.
        """
        snapshot = dash.look()

        assert snapshot.running is False
        assert snapshot.error
        assert "daemon not running" in snapshot.summary

    def test_the_summary_answers_is_it_working(self):
        snapshot = dash.Snapshot(
            running=True,
            port=8080,
            https_port=8443,
            sites=[{"name": "demo"}],
            services=[{"name": "pg"}],
            processes=[{"name": "php-1"}, {"name": "caddy"}],
        )

        assert "8080" in snapshot.summary
        assert "8443" in snapshot.summary
        assert "1 site" in snapshot.summary
        assert "2 processes" in snapshot.summary

    def test_it_counts_in_the_singular_when_there_is_one(self):
        # Small, and the sort of thing that reads as carelessness everywhere it
        # is got wrong.
        one = dash.Snapshot(running=True, port=80, sites=[{}], services=[{}], processes=[{}])

        assert "1 site," in one.summary
        assert "1 database," in one.summary
        assert "1 process " in one.summary + " "


class TestRunningIt:
    async def test_it_starts_stops_and_draws_its_panes(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            assert len(app.query("DataTable")) == 3, "processes, sites and services"
            assert app.query_one("#command"), "there is nowhere to type"

            await pilot.press("f5")
            await pilot.pause()

    async def test_the_letters_belong_to_the_command_line(self):
        """
        `q` types a q.

        The single-letter bindings had to go the moment there was somewhere to
        type: a screen where `site add q...` closes the window is a screen
        nobody types in twice.
        """
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q", "u", "i", "t")
            await pilot.pause()

            assert app.query_one("#command").value == "quit"
            assert app.is_running, "typing q closed the screen"

    async def test_pausing_says_so_rather_than_going_quiet(self):
        # A log pane that stops moving is indistinguishable from one whose
        # source has stopped, which is the thing being watched for.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f2")
            await pilot.pause()

            assert app._paused is True


class TestTypingIntoIt:
    """
    The half that makes it more than a screen to watch.

    Every line goes through the same parser and the same handlers as the command
    line — the rule the shell already follows, and for the same reason: a second
    dispatch is a second implementation, and it drifts.
    """

    async def submit(self, pilot, app, line: str) -> None:
        app.query_one("#command").value = line
        await pilot.press("enter")
        await pilot.pause()

        # The command runs in a thread so the screen stays alive through a
        # download; wait for it rather than for a fixed moment.
        for _ in range(80):
            if not app.workers._workers:
                break

            await pilot.pause(0.05)

    def written(self, app) -> str:
        return "\n".join(str(line) for line in app.query_one("#log").lines)

    async def test_a_command_runs_and_its_answer_appears(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await self.submit(pilot, app, "version --json")

            written = self.written(app)

            assert "> version --json" in written, "what was typed is not shown"
            assert VERSION in written, "the answer is not shown"

    async def test_a_typo_does_not_take_the_screen_with_it(self):
        # argparse raises SystemExit for an unknown command. In a screen that
        # would be the last thing that ever happened.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await self.submit(pilot, app, "nosuchcommand")

            assert app.is_running
            assert "invalid choice" in self.written(app)

    async def test_the_commands_that_cannot_work_here_are_refused_with_a_reason(self):
        """
        Each would either wait for an answer nobody can give, or take the ground
        out from under the screen.

        A window that stops responding for a reason nobody can see is worse than
        one that declines.
        """
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            for line, expected in (
                ("dash", "you are in it"),
                ("shell", "this is one"),
                ("upgrade", "replaces the folder"),
                ("logs -f", "already following"),
                ("purge", "add --yes"),
            ):
                await self.submit(pilot, app, line)

                assert expected in self.written(app), f"{line} was not refused clearly"

    async def test_history_comes_back_with_the_arrows(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await self.submit(pilot, app, "version --json")
            await self.submit(pilot, app, "home --json")

            await pilot.press("up")
            await pilot.pause()

            assert app.query_one("#command").value == "home --json"

            await pilot.press("up")
            await pilot.pause()

            assert app.query_one("#command").value == "version --json"

    async def test_what_it_offers_comes_from_the_parser(self):
        # So a command added tomorrow appears here without anybody remembering
        # to add it, and one that cannot run here is not offered.
        offered = dash._suggestions()

        assert "status" in offered
        assert "site add " in offered
        assert "dash" not in offered
        assert "upgrade" not in offered


class TestTheLibrariesItNeeds:
    """
    `textual` declares six dependencies this tool never reaches for, and the
    bundle carries four packages rather than ten — leaving out four and a half
    megabytes of syntax lexers for a screen that highlights nothing.

    That is a measurement, and measurements go stale. This is the guard: the
    imports are blocked and the dashboard is run anyway, so the day something
    does reach for one it is a failing test rather than a bundle that crashes on
    somebody else's machine.
    """

    OMITTED = ("pygments", "markdown_it", "mdit_py_plugins", "linkify_it", "mdurl", "uc_micro_py")

    @pytest.fixture
    def without_them(self, monkeypatch):
        real = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name.split(".")[0] in self.OMITTED:
                raise ImportError(f"{name} is deliberately not vendored")

            return real(name, *args, **kwargs)

        for name in self.OMITTED:
            for loaded in [key for key in sys.modules if key.split(".")[0] == name]:
                monkeypatch.delitem(sys.modules, loaded, raising=False)

        monkeypatch.setattr(builtins, "__import__", refuse)

    async def test_the_dashboard_runs_without_the_ones_left_out(self, without_them):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("q")

    def test_the_bundle_pins_exactly_what_is_needed(self):
        # The list in `bundle.py` and the reason for it should not drift apart
        # silently.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "bundle", "scripts/bundle.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["bundle"] = module
        spec.loader.exec_module(module)

        assert set(module.VENDORED) == {"textual", "rich", "platformdirs", "typing_extensions"}
        assert all(version[0].isdigit() for version in module.VENDORED.values()), (
            "a version must be exact — a bundle gets a checksum, and a range cannot"
        )


class TestSayingItIsBusy:
    """
    Reported after using it: commands ran, and nothing said they were running.

    A download that takes a minute was indistinguishable from a screen that had
    hung — the tables kept refreshing, the log kept flowing, and nothing said
    anybody was waiting on anything.
    """

    async def test_output_arrives_as_it_is_written_not_at_the_end(self):
        """
        Collecting it and showing it afterwards was the first version, and it is
        exactly what made a working command look frozen.
        """
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            # From a thread, which is where a command actually runs — the
            # screen's own loop cannot call into itself that way.
            import asyncio

            stream = dash._Streaming(app)
            await asyncio.to_thread(stream.write, "first line\n")
            await pilot.pause()

            written = "\n".join(str(line) for line in app.query_one("#log").lines)

            assert "first line" in written, "nothing arrived before the command ended"

    async def test_a_partial_line_is_not_lost(self):
        # A command that finishes without a trailing newline still said
        # something, and dropping it would lose the last thing it said.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            import asyncio

            stream = dash._Streaming(app)
            await asyncio.to_thread(stream.write, "no newline at the end")
            await asyncio.to_thread(stream.flush)
            await pilot.pause()

            assert "no newline" in "\n".join(str(l) for l in app.query_one("#log").lines)

    async def test_it_is_not_a_terminal(self):
        # Which decides whether output is coloured. It is a widget, and the
        # escape codes would be printed rather than obeyed.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            assert dash._Streaming(app).isatty() is False

    async def test_the_bar_says_what_is_running_and_the_prompt_stays_a_prompt(self):
        # It was said in both places, which took away the one line telling
        # somebody what the field is for and said the same thing twice.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.busy("install php")
            app.tick()

            assert "install php" in str(app.query_one("#busy").content)
            assert "install php" not in app.query_one("#command").placeholder
            assert "command" in app.query_one("#command").placeholder


class TestLeavingWhileSomethingRuns:
    """
    Reported: quitting during an `install` that was waiting out an unreachable
    host left the screen gone and the process alive — a blinking cursor in a
    terminal that looks wedged.

    A command runs in a thread, and a thread inside a network call does not
    notice being asked to stop.
    """

    async def test_the_first_press_says_what_is_still_running(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            app.busy("install mariadb")

            await pilot.press("f10")
            await pilot.pause()

            written = "\n".join(str(line) for line in app.query_one("#log").lines)

            assert "install mariadb is still running" in written
            assert app.is_running, "it left without saying anything"

    async def test_the_second_press_leaves_regardless(self):
        # The daemon owns everything that matters, and an abandoned download
        # costs the part that was fetched — which is kept for the next attempt.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            app.busy("install mariadb")

            await pilot.press("f10")
            await pilot.pause()
            await pilot.press("f10")
            await pilot.pause()

            assert not app.is_running

    async def test_nothing_running_leaves_at_once(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f10")
            await pilot.pause()

            assert not app.is_running


class TestShowingItIsStillAlive:
    """
    Reported: `install mariadb` sat for five minutes and eventually timed out,
    and there was no way to tell it apart from a hang.

    A label that says "running" and never changes is a still picture, and a
    still picture cannot answer "is anything still happening" — which is the
    whole question during a thirty-second connect timeout, when nothing is
    printed at all.
    """

    async def test_the_indicator_moves_and_counts(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.busy("install mariadb")
            app.tick()
            first = str(app.query_one("#busy").content)

            app.tick()
            second = str(app.query_one("#busy").content)

            assert first != second, "it is a still picture"
            assert "install mariadb" in first, "it does not say what is running"
            assert "s" in first, "it does not say how long"

    async def test_it_is_hidden_when_nothing_is_running(self):
        # A bar that is always there stops meaning anything.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#busy").display is False

            app.busy("install php")

            assert app.query_one("#busy").display is True

            app.busy(None)

            assert app.query_one("#busy").display is False

    async def test_it_says_how_to_get_out(self):
        # Found by somebody quitting during exactly this and being left with a
        # blinking cursor.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            app.busy("install mariadb")
            app.tick()

            assert "F10" in str(app.query_one("#busy").content)

    async def test_the_frames_are_drawable_anywhere(self):
        # A terminal that cannot draw braille would show the indicator as boxes
        # and make the screen look broken at the moment it is meant to reassure.
        assert all(character.isascii() for character in dash.FRAMES)


class TestShowingWhatIsWrong:
    """
    The screen exists to make trouble visible.

    Looked at rather than reasoned about: the dashboard renders to an image
    headlessly, and a populated one showed a stopped Redis with five restarts
    drawn exactly like the seven processes that were fine.
    """

    async def test_a_process_that_is_not_running_is_not_drawn_like_one_that_is(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.show(
                dash.Snapshot(
                    running=True,
                    port=80,
                    processes=[
                        {"name": "caddy", "state": "running", "pid": 1, "restarts": 0},
                        {"name": "redis", "state": "stopped", "pid": None, "restarts": 5},
                    ],
                )
            )

            rows = list(app.query_one("#processes").get_column_at(1))
            healthy, stopped = rows[0], rows[1]

            assert str(healthy.style or "") == "", "a running process is being shouted about"
            assert "red" in str(stopped.style), "a stopped one looks like a running one"

    async def test_a_process_that_keeps_restarting_is_marked(self):
        # A worker retiring itself every few hundred requests is routine; one
        # that keeps dying is not, and as a bare number the two are identical.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.show(
                dash.Snapshot(
                    running=True,
                    port=80,
                    processes=[
                        {"name": "a", "state": "running", "pid": 1, "restarts": 1},
                        {"name": "b", "state": "running", "pid": 2, "restarts": 9},
                    ],
                )
            )

            counts = list(app.query_one("#processes").get_column_at(3))

            assert str(counts[0].style or "") == ""
            assert "yellow" in str(counts[1].style)

    async def test_a_failure_in_the_log_is_not_the_same_grey_as_everything_else(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.say("caddy", '{"level":"error","msg":"address already in use"}', 0)
            await pilot.pause()

            written = app.query_one("#log").lines[-1]

            assert "red" in str(written), "a failure reads like `config is unchanged`"

    async def test_the_empty_tables_say_why_they_are_empty(self):
        # Column headings with nothing under them look broken. Saying why costs
        # one row and answers the question somebody is about to ask.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=False))

            assert app.query_one("#processes").row_count == 1
            assert app.query_one("#sites").row_count == 1
            assert app.query_one("#services").row_count == 1


class TestACommandOutlivingTheScreen:
    """
    A command runs in its own thread. That thread outlives neither the screen's
    arrival nor its departure, and it touches the screen three times: to raise
    the busy bar, to write a duration, to lower it again.

    One of four CI machines caught this as `NoMatches` on `#busy`. In a real
    session the cost is worse than a failed test: the lookup happened in the
    worker thread, before the line that lowers the bar, so a command finishing
    at the wrong moment would leave the bar spinning over nothing.
    """

    async def test_the_bar_survives_the_screen_going_away(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

        # The screen is gone; a command finishing now must not raise.
        app.busy("install mariadb")
        app.tick()
        app.busy(None)

    async def test_a_note_survives_it_too(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

        app.note("  (31s)")

    async def test_a_refresh_landing_after_f10_is_not_a_crash(self):
        # The status worker runs on a thread and finishes when it finishes. One
        # in flight when the screen goes arrives with nowhere to put itself,
        # which one of four CI machines caught as NoMatches on #summary.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            snapshot = dash.look()

        app.show(snapshot)

    async def test_nothing_looks_up_a_widget_from_a_thread(self):
        # Including the writer that forwards a command's output, which did.
        source = " ".join(inspect.getsource(dash).split())

        assert "call_from_thread( self._app.query_one" not in source
        assert "call_from_thread(self._app.query_one" not in source

    async def test_the_log_is_not_queried_from_the_worker_thread(self):
        # The DOM belongs to the message loop. Reaching into it from the thread
        # running a command is what turned a missing widget into a dead thread
        # rather than a missed line.
        # Collapsed, because the call that started this was split across two
        # lines and a literal search for it found nothing — a guard that passes
        # against the code it was written to catch guards nothing.
        source = " ".join(inspect.getsource(dash.build).split())

        assert "call_from_thread( self.query_one" not in source
        assert "call_from_thread(self.query_one" not in source


class TestOpeningADatabaseClient:
    """
    The tables were a display. A cursor sitting in one that does nothing when
    you press Enter is a thing people press twice and then stop trusting.
    """

    async def test_enter_on_a_database_opens_its_client(self, monkeypatch):
        application = dash.build()
        app = application()
        opened = []

        async with app.run_test() as pilot:
            await pilot.pause()

            monkeypatch.setattr(app, "open_client", opened.append)
            table = app.query_one("#services")
            table.clear()
            table.add_row("cache", "redis", "6379")

            table.action_select_cursor()
            await pilot.pause()

        assert opened == ["cache"], "selecting a database did nothing"

    async def test_the_empty_row_is_not_a_database(self, monkeypatch):
        # "none yet | type: service add" is a sentence, not something to connect
        # to.
        application = dash.build()
        app = application()
        opened = []

        async with app.run_test() as pilot:
            await pilot.pause()

            monkeypatch.setattr(app, "open_client", opened.append)
            table = app.query_one("#services")
            table.action_select_cursor()
            await pilot.pause()

        assert opened == []

    async def test_selecting_a_process_does_not_open_anything(self, monkeypatch):
        application = dash.build()
        app = application()
        opened = []

        async with app.run_test() as pilot:
            await pilot.pause()

            monkeypatch.setattr(app, "open_client", opened.append)
            table = app.query_one("#processes")
            table.clear()
            table.add_row("php-8.4.24-1", "running", "0", "")

            table.action_select_cursor()
            await pilot.pause()

        assert opened == []

    async def test_typing_the_command_is_refused_with_the_way_that_works(self):
        # An interactive client with its output captured is a prompt nobody can
        # see and a session nobody can leave.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            refusal = app._refused("service cli cache")

        assert "enter" in refusal.lower(), refusal

    async def test_other_service_commands_are_not_refused(self):
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app._refused("service list") == ""
            assert app._refused("service add redis") == ""


class TestTheFaceInTheCorner:
    """
    It answers the same question the summary does — is anything wrong — and
    answers it from further away: a shape changing is visible across a room, a
    line of text is not.
    """

    def test_every_face_fits_the_frame(self):
        from portable import mascot

        for state in mascot.FACES:
            rows = mascot.face(state)

            assert len(rows) == mascot.HEIGHT, state
            assert {len(row) for row in rows} == {mascot.WIDTH}, (
                f"{state} bends the box: {rows}"
            )

    def test_nothing_is_beyond_ascii(self):
        # The busy indicator is `|/-\` for this reason: a terminal without the
        # font shows squares, and a mascot of squares makes the screen look
        # broken at the moment it is meant to reassure.
        from portable import mascot

        for state in mascot.FACES:
            for row in mascot.face(state):
                assert row.isascii(), f"{state} is not ASCII: {row!r}"

        assert mascot.SPIN.isascii()

    def test_the_mouth_spins_while_something_runs(self):
        from portable import mascot

        mouths = {mascot.face("working", frame)[2] for frame in range(len(mascot.SPIN))}

        assert len(mouths) == len(mascot.SPIN), "the working face is a still picture"

    def test_an_unknown_state_is_a_face_and_not_an_exception(self):
        # Decoration on a screen whose job is reporting other things. One that
        # can take the dashboard down with it would be a poor trade.
        from portable import mascot

        assert mascot.face("elated") == mascot.face("ready")

    def test_what_each_state_answers(self):
        from portable import mascot

        assert mascot.state_for(running=False, failing=False, busy=True) == "stopped"
        assert mascot.state_for(running=True, failing=False, busy=True) == "working"
        assert mascot.state_for(running=True, failing=False, busy=False) == "ready"

    def test_a_command_in_flight_outranks_a_failure_that_is_already_known(self):
        # While something runs, whether it is alive is the live question; the
        # failure is not going anywhere and comes back the moment it ends.
        from portable import mascot

        assert mascot.state_for(running=True, failing=True, busy=True) == "working"
        assert mascot.state_for(running=True, failing=True, busy=False) == "error"

    def test_silence_is_waiting_and_not_working(self):
        # Most of an install is spent waiting on somebody else's host, and the
        # spinner turns the same either way — which is the moment a thirty
        # second connect timeout looks like a hang.
        from portable import mascot

        assert mascot.state_for(running=True, failing=False, busy=True, silent_for=1) == "working"
        assert (
            mascot.state_for(running=True, failing=False, busy=True, silent_for=mascot.SILENCE)
            == "waiting"
        )

    def test_a_finished_command_is_shown_and_then_let_go(self):
        from portable import mascot

        settled = {"running": True, "failing": False, "busy": False}

        assert mascot.state_for(**settled, finished_ago=0.5) == "done"
        assert mascot.state_for(**settled, finished_ago=mascot.LINGER + 1) != "done"

    def test_a_failure_does_not_cheer_up_on_a_timer(self):
        # A face that returns to pleased by itself reports the passage of time
        # rather than the state of anything.
        from portable import mascot

        assert mascot.state_for(running=True, failing=True, busy=False, idle_for=600) == "error"

    def test_a_supervisor_that_left_by_itself_is_news(self):
        from portable import mascot

        assert (
            mascot.state_for(running=False, failing=False, busy=False, vanished_ago=0.5)
            == "surprised"
        )
        assert (
            mascot.state_for(
                running=False, failing=False, busy=False, vanished_ago=mascot.LINGER + 1
            )
            == "stopped"
        )

    def test_it_blinks_now_and_then_rather_than_staring(self):
        from portable import mascot

        settled = {"running": True, "failing": False, "busy": False}
        blinks = [
            tick / 10
            for tick in range(int(mascot.BLINK_EVERY * 10), int(mascot.BLINK_EVERY * 40))
            if mascot.state_for(**settled, idle_for=tick / 10) == "blink"
        ]

        assert blinks, "the idle face never blinks"
        assert len(blinks) < mascot.BLINK_EVERY * 3, "it blinks more than it looks"

    def test_every_face_the_decider_can_return_is_drawn(self):
        # The half of the set that nothing reached is what this exists to stop
        # happening again.
        from portable import mascot

        reachable = {
            mascot.state_for(
                running=running,
                failing=failing,
                busy=busy,
                silent_for=silence,
                finished_ago=finished,
                vanished_ago=vanished,
                idle_for=idle,
            )
            for running in (True, False)
            for failing in (True, False)
            for busy in (True, False)
            for silence in (0, 60)
            for finished in (None, 0.1, 60)
            for vanished in (None, 0.1, 60)
            for idle in (0.0, mascot.BLINK_EVERY + 0.1)
        }

        assert reachable <= set(mascot.FACES)
        assert reachable == set(mascot.FACES) - {"wink"}, (
            f"drawn and never shown: {set(mascot.FACES) - reachable - {'wink'}}"
        )

    async def test_it_shows_the_state_the_screen_is_in(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=False, home="C:/p"))
            app.tick()
            assert "zZ" in str(app.query_one("#mascot").content)

            app.show(
                dash.Snapshot(
                    running=True,
                    port=80,
                    home="C:/p",
                    processes=[{"name": "caddy", "state": "stopped", "restarts": 9}],
                )
            )
            app.tick()
            assert "x x" in str(app.query_one("#mascot").content), "a dead process is not a face"

    async def test_it_gives_the_rows_back_on_a_short_terminal(self):
        # Rows are the scarce thing here. A picture that costs a table row is a
        # bad trade, so below twenty-four rows it goes.
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 20)) as pilot:
            await pilot.pause()

            assert app.query_one("#mascot").display is False
            assert app.query_one("#detail").display is False

    async def test_the_masthead_says_four_things(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            lines = dash.Snapshot(running=True, port=80, home="C:/p/data").masthead

            assert len(lines) == mascot_height(), "the block and the face are different heights"
            assert all(lines), "an empty line in the one block that is all facts"


def mascot_height() -> int:
    from portable import mascot

    return mascot.HEIGHT


class TestTheLogoAndTheFaceAreOneFigure:
    """
    The mark is not a picture of the mascot, it is the mascot — drawn with
    lines because the ears are diagonals and a diagonal transcribed into a grid
    of blocks stops being one.

    Nothing here can compare geometry to characters. What it can do is keep the
    two from drifting apart quietly: if the frame in `mascot.py` changes, these
    fail and say to look at the drawing too.
    """

    def files(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent

        return root / "docs" / "mark.svg", root / "docs" / "logo.svg"

    def test_both_are_there_and_parse(self):
        from xml.dom.minidom import parse

        for path in self.files():
            assert path.exists(), f"{path} is referenced by every README"
            parse(str(path))

    def test_they_carry_the_name_for_a_reader_who_cannot_see_them(self):
        for path in self.files():
            text = path.read_text(encoding="utf-8")

            assert 'aria-label="portable"' in text
            assert "<title>portable</title>" in text

    def test_nothing_is_fetched_from_anywhere(self):
        # The same rule the rest of the tool follows. A logo that phones home
        # from a README is a tracking pixel with a face on it.
        for path in self.files():
            text = path.read_text(encoding="utf-8")

            # The namespace is the one URL allowed: it names the format, and
            # nothing is fetched from it.
            without_namespace = text.replace("http://www.w3.org/2000/svg", "")

            assert "://" not in without_namespace, "the drawing reaches somewhere"
            assert "<image" not in text and "@import" not in text

    def test_the_drawing_is_pointed_at_the_characters(self):
        # So that changing the frame leads somebody to the other half of it.
        mark, _ = self.files()

        assert "mascot.py" in mark.read_text(encoding="utf-8")

    def test_the_frame_it_was_drawn_from_is_the_one_still_in_use(self):
        from portable import mascot

        assert mascot.TOP == "|\\---/|", (
            "the frame changed; docs/mark.svg draws the old one and needs redrawing"
        )
        assert mascot.BOTTOM == "'-----'"


class TestWhatTheCommandLineTellsTheFace:
    """
    Typing something that fails used to leave the corner pleased.

    The tables cannot show it — a refused `install` changes nothing in them —
    and the log scrolls. The exit code every command already returns was being
    thrown away.
    """

    async def test_a_command_that_fails_turns_the_face(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.settled(failed=True)
            app.tick()

            assert "x x" in str(app.query_one("#mascot").content)

    async def test_a_command_that_works_is_pleased_and_then_settles(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.settled(failed=False)
            app.tick()

            assert "^ ^" in str(app.query_one("#mascot").content)

            # Long enough ago that it is history rather than news.
            app._finished = time.monotonic() - 60
            app.tick()

            assert "^ ^" not in str(app.query_one("#mascot").content)

    async def test_a_supervisor_that_goes_away_by_itself_is_noticed(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.show(dash.Snapshot(running=False, home="C:/p"))
            app.tick()

            assert "O O" in str(app.query_one("#mascot").content), (
                "the daemon vanished and the corner did not react"
            )

    async def test_one_that_was_never_up_is_merely_asleep(self):
        # Opening the dashboard with nothing running is not news.
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=False, home="C:/p"))
            app.tick()

            assert "zZ" in str(app.query_one("#mascot").content)

    async def test_silence_while_a_command_runs_becomes_waiting(self):
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.busy("install mariadb")
            app.tick()

            assert "  >" in str(app.query_one("#mascot").content)

            # Nothing has been printed for a while: it is waiting on a host,
            # not working.
            app._spoke = time.monotonic() - 60
            app.tick()

            assert ".." in str(app.query_one("#mascot").content)

            app.said_something()
            app.tick()

            assert ".." not in str(app.query_one("#mascot").content)


class TestBlinkingAndBeingSurprisedForRealReasons:
    """
    The blink was tested by calling the decider. That proves the rule and not
    the loop — and the loop is the part that has to draw it every twenty-five
    seconds without being asked.
    """

    async def test_the_screen_blinks_by_itself(self, monkeypatch):
        import asyncio

        from portable import mascot

        # The clock, sped up. Not the state pushed by hand: the point is that
        # nothing pushes it.
        monkeypatch.setattr(mascot, "BLINK_EVERY", 1.0)
        monkeypatch.setattr(mascot, "BLINK_FOR", 0.3)
        monkeypatch.setattr(
            dash,
            "look",
            lambda: dash.Snapshot(
                running=True,
                port=80,
                home="C:/p",
                processes=[{"name": "caddy", "state": "running", "restarts": 0, "pid": 1}],
            ),
        )

        application = dash.build()
        app = application()
        seen = set()

        async with app.run_test(size=(90, 32)) as pilot:
            await pilot.pause()
            started = time.monotonic()

            while time.monotonic() - started < 2.5:
                await asyncio.sleep(0.05)
                seen.add(str(app.query_one("#mascot").content).splitlines()[1])

        assert "| - - |" in seen, f"it never blinked: {seen}"
        assert "| o o |" in seen, "it blinked and stayed shut"

    async def test_down_typed_here_is_not_a_surprise(self):
        """
        Reported by looking: the face acted shocked at what it had just been
        told to do.

        `down` ends, the busy flag clears, and only then does the next refresh
        notice the daemon has gone — so the guard that asked "is a command
        running" was always false by the time it mattered.
        """
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.busy("down")
            app.settled(failed=False)
            app.busy(None)
            app.show(dash.Snapshot(running=False, home="C:/p"))
            app.tick()

            assert "zZ" in str(app.query_one("#mascot").content), (
                "it was asked to stop and the corner is astonished"
            )

    async def test_one_that_dies_long_after_a_command_still_is(self):
        # The face exists for exactly this: nobody asked, and it went.
        application = dash.build()
        app = application()

        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            app.show(dash.Snapshot(running=True, port=80, home="C:/p"))
            app.settled(failed=False)
            app._ran_at = time.monotonic() - dash.ASKED_FOR - 1
            app.show(dash.Snapshot(running=False, home="C:/p"))
            app.tick()

            assert "O O" in str(app.query_one("#mascot").content)
