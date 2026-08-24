"""
The command line, end to end.

These start a real daemon in a real detached process and talk to it, because the
questions worth asking here cannot be answered any other way: does `up` return
only once something is actually listening, does `down` leave the ports free, and
does every command speak JSON when asked.

The last one is not cosmetic. An IDE plugin will read this output, and a client
that has to parse sentences written for people breaks the day a sentence is
reworded.
"""

from __future__ import annotations

import json
import time

import pytest

from portable import paths, spawn
from portable.cli import main
from portable.daemon import discovery


@pytest.fixture
def daemon(capsys):
    """A running daemon, stopped afterwards however the test ends."""
    assert main(["up"]) == 0
    capsys.readouterr()

    yield discovery.read()

    endpoint = discovery.read()

    if endpoint is not None:
        main(["down"])
        capsys.readouterr()


class TestWithoutADaemon:
    def test_status_says_so_rather_than_crashing(self, capsys):
        assert main(["status"]) == 1
        assert "portable up" in capsys.readouterr().err

    def test_the_refusal_has_a_key_in_json_mode(self, capsys):
        assert main(["status", "--json"]) == 1
        assert json.loads(capsys.readouterr().out)["errorKey"] == "not-running"

    def test_no_command_prints_help_and_does_not_pretend_to_succeed(self, capsys):
        assert main([]) == 2
        assert "usage" in capsys.readouterr().out.lower()


class TestUp:
    def test_it_returns_only_once_the_daemon_answers(self, daemon):
        # Returning early makes the very next command fail with "no daemon is
        # running" — a race that reads exactly like a bug.
        assert daemon is not None
        assert spawn.is_running(daemon.pid)

    def test_the_daemon_outlives_the_process_that_started_it(self, daemon):
        # The whole point. `up` has returned, this test's own call stack is
        # unwound, and the daemon is still there.
        assert spawn.is_running(daemon.pid)
        assert discovery.read() is not None

    def test_starting_twice_does_not_start_a_second_daemon(self, daemon, capsys):
        assert main(["up", "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)

        assert payload["already"] is True
        assert payload["pid"] == daemon.pid

    def test_it_creates_the_layout(self, daemon):
        for directory in (paths.runtimes(), paths.logs(), paths.run()):
            assert directory.exists()


class TestStatus:
    def test_it_reports_the_home_it_is_using(self, daemon, capsys):
        assert main(["status"]) == 0
        assert str(paths.root()) in capsys.readouterr().out

    def test_json_mode_is_machine_readable(self, daemon, capsys):
        assert main(["status", "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)

        assert "version" in payload
        assert isinstance(payload["processes"], list)


class TestDown:
    def test_it_stops_the_daemon_and_clears_its_note(self, capsys):
        assert main(["up"]) == 0
        capsys.readouterr()
        endpoint = discovery.read()

        assert main(["down"]) == 0

        # `down` returning is a promise that the ports are free, not that the
        # request was accepted.
        assert discovery.read() is None
        assert not paths.daemon_file().exists()

        deadline = time.monotonic() + 5

        while spawn.is_running(endpoint.pid) and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not spawn.is_running(endpoint.pid)

    def test_down_without_a_daemon_is_a_refusal_not_a_crash(self, capsys):
        assert main(["down", "--json"]) == 1
        assert json.loads(capsys.readouterr().out)["errorKey"] == "not-running"
