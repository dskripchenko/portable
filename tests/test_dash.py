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

from portable import dash

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

            await pilot.press("r")
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("q")

    async def test_pausing_says_so_rather_than_going_quiet(self):
        # A log pane that stops moving is indistinguishable from one whose
        # source has stopped, which is the thing being watched for.
        application = dash.build()
        app = application()

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()

            assert app._paused is True

            await pilot.press("q")


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
