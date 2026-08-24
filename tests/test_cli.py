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

from portable import cli, paths, spawn
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


class TestDaemonEnvironment:
    def test_the_daemon_can_import_the_package_without_an_inherited_pythonpath(
        self, monkeypatch, capsys
    ):
        """
        The failure CI found and this machine could not.

        The daemon is a fresh interpreter with a fresh `sys.path`. Started from
        a source checkout it has no way to find this package unless it is told
        where it is — and `PYTHONPATH` being set locally, by hand, hid that on
        every developer machine while failing on all four CI platforms at once.
        """
        monkeypatch.delenv("PYTHONPATH", raising=False)

        assert main(["up"]) == 0, "the daemon could not start without an inherited PYTHONPATH"
        capsys.readouterr()

        try:
            assert main(["status"]) == 0
        finally:
            main(["down"])
            capsys.readouterr()


class TestRunAndEnv:
    def test_an_empty_path_entry_is_never_produced(self, daemon, capsys):
        """
        On POSIX an empty element in PATH means the *current directory*. With
        nothing installed the naive join produced `PATH=":$PATH"`, so a command
        run through this would pick up whatever happened to be in the directory
        it was typed in.
        """
        assert main(["env", "--shell", "posix"]) == 0

        output = capsys.readouterr().out

        assert 'PATH=":' not in output
        assert "Nothing installed" in output

    def test_taking_a_runtime_from_the_machine_is_said_out_loud(self, daemon, capsys):
        # Falling back is deliberate — `portable run composer` should work. Doing
        # it silently for a *runtime* is not: this command exists to make it
        # certain which one runs.
        from portable.cli import _names_a_runtime, _runtime_behind

        assert _names_a_runtime("node")
        assert _names_a_runtime("npm")
        assert _runtime_behind("npm") == "node"
        # Not a runtime: expected to come from the machine, and mentioning it
        # every time would be noise.
        assert not _names_a_runtime("composer")

    def test_run_without_a_command_explains_itself(self, daemon, capsys):
        assert main(["run"]) == 2
        assert "Usage" in capsys.readouterr().err


class TestWaitingForTheDaemon:
    """
    Slow and dead are different, and used to be one number.

    A cold Windows machine can spend fifteen seconds starting an interpreter
    before any of this tool's own code runs — not a fault, but a fixed timeout
    tuned for a warm laptop turns it into one. It surfaced as CI failing once in
    four on `windows-latest`, which is the same cold start a person gets on the
    first `portable up` after a reboot.
    """

    def test_it_gives_up_at_once_when_the_process_is_gone(self):
        # And does not sit out the full timeout. There is nothing left to wait
        # for, and a minute of silence before a failure that was knowable
        # immediately is a minute spent doubting the command.
        started = time.monotonic()

        assert cli._await_daemon(pid=-1, timeout=30) is None
        assert time.monotonic() - started < 2

    def test_it_keeps_waiting_while_the_process_is_alive(self, monkeypatch):
        alive = [True]
        monkeypatch.setattr(cli.spawn, "is_running", lambda pid: alive[0])

        answered = []

        def read():
            answered.append(1)


        monkeypatch.setattr(cli.discovery, "read", read)

        assert cli._await_daemon(pid=1234, timeout=1.0) is None
        assert len(answered) > 1, "it stopped polling while the daemon was still starting"
