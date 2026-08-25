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
import sys

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
