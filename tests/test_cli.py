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

from portable import VERSION, cli, paths, spawn
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


class TestFindingOutWhatItDoes:
    """
    The two commands somebody runs before any other.

    Both have to work with no daemon, no runtimes and nothing configured —
    which is exactly the state of a machine where something has already gone
    wrong and the first question is what version this even is.
    """

    def test_version_works_with_nothing_running(self, capsys):
        assert cli.main(["version", "--json"]) == 0

        reported = json.loads(capsys.readouterr().out)

        assert reported["version"] == VERSION
        assert reported["daemon"] is None
        assert reported["home"] == str(paths.root())

    def test_the_client_and_the_daemon_take_the_version_from_one_place(self):
        # So that a mismatch after an upgrade is a real mismatch and not two
        # constants drifting. The new command talking to the old daemon explains
        # a great deal, and only if both numbers mean the same thing.
        from portable.daemon import server

        assert server.VERSION is VERSION

    def test_help_names_every_command(self, capsys):
        assert cli.main(["help"]) == 0

        printed = capsys.readouterr().out
        commands = cli._parser()._subparsers._group_actions[0].choices

        missing = [name for name in commands if f"portable {name}" not in printed]

        assert not missing, f"the overview does not mention: {', '.join(missing)}"

    def test_install_offers_what_the_catalog_has(self, capsys):
        # It said "php, caddy" for a while after four more were added.
        from portable import catalog

        cli.main(["help"])
        printed = capsys.readouterr().out

        for name in catalog.names():
            assert name in printed, f"{name} is installable and unmentioned"


class TestTheShell:
    """
    A loop that reads a command, runs it, and reads another.

    Every line goes through the same parser and the same handlers as the command
    line. A shell with its own dispatch is a second implementation that drifts —
    one where a flag was added in one place and not the other, and the
    difference is found by somebody who thought they were using the same tool.
    """

    def feed(self, monkeypatch, *lines: str) -> None:
        typed = iter(lines)

        def read(_prompt=""):
            try:
                return next(typed)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", read)

    def test_a_typo_does_not_end_the_session(self, monkeypatch, capsys):
        """
        argparse raises `SystemExit` for an unknown command.

        Uncaught, that takes the whole shell down over a misspelling — losing
        whatever else was about to be typed, which is the one thing a shell
        exists to keep.
        """
        self.feed(monkeypatch, "nosuchcommand", "version --json")

        assert cli.main(["shell"]) == 0

        printed = capsys.readouterr()

        assert "invalid choice" in printed.err
        assert VERSION in printed.out, "it stopped before reaching the next line"

    def test_an_unhandled_failure_does_not_either(self, monkeypatch, capsys):
        # A shell that dies on an unforeseen error loses everything typed before
        # it, and the error is usually one command's problem.
        monkeypatch.setattr(
            cli, "_version", lambda args: (_ for _ in ()).throw(RuntimeError("unexpected"))
        )
        self.feed(monkeypatch, "version", "help")

        assert cli.main(["shell"]) == 0

        printed = capsys.readouterr()

        assert "RuntimeError: unexpected" in printed.err
        assert "getting started" in printed.out, "it stopped at the failure"

    def test_unbalanced_quotes_are_explained_rather_than_raised(self, monkeypatch, capsys):
        self.feed(monkeypatch, 'site add demo "C:\\unclosed', "version --json")

        assert cli.main(["shell"]) == 0
        assert "quotes" in capsys.readouterr().err

    def test_exit_leaves_without_running_what_follows(self, monkeypatch, capsys):
        self.feed(monkeypatch, "exit", "home --json")

        assert cli.main(["shell"]) == 0
        assert "home" not in capsys.readouterr().out.split("Leaving does not")[-1]

    def test_ctrl_c_abandons_the_line_and_not_the_shell(self, monkeypatch, capsys):
        # Which is what it does in every other shell. Ending the session on it
        # would be a surprise with a cost.
        answers = iter([KeyboardInterrupt(), "version --json", EOFError()])

        def read(_prompt=""):
            answer = next(answers)

            if isinstance(answer, BaseException):
                raise answer

            return answer

        monkeypatch.setattr("builtins.input", read)

        assert cli.main(["shell"]) == 0
        assert VERSION in capsys.readouterr().out

    def test_it_runs_the_real_handlers(self, monkeypatch, capsys):
        # Not a lookalike. This is the whole reason there is no second dispatch.
        self.feed(monkeypatch, "home --json")

        assert cli.main(["shell"]) == 0

        # The prompt is printed by `input`, which is replaced here, so the
        # output is the banner followed by whatever the command produced.
        reported = capsys.readouterr().out.strip().splitlines()[-1]

        assert json.loads(reported)["home"] == str(paths.root())


class TestRunningFromAnywhere:
    """
    On PATH, `portable` is run from wherever somebody happens to be.

    Two things have to hold: it must find itself without help, and it must not
    take the caller's directory hostage.
    """

    def test_the_daemon_is_started_in_its_own_directory(self, monkeypatch, tmp_path):
        started: dict = {}

        def remember(argv, cwd=None, env=None, log=None):
            started["cwd"] = cwd

            return 4321

        monkeypatch.setattr(cli.spawn, "start_detached", remember)
        monkeypatch.setattr(cli, "_await_daemon", lambda pid=None, timeout=60: None)

        cli.main(["up"])

        assert started["cwd"] == paths.root()

    def test_a_relative_site_root_still_follows_the_caller(self, monkeypatch, tmp_path):
        # Which is the other half of it. `site add demo .` has to mean the
        # directory the person is standing in, not the one the tool lives in.
        sent: dict = {}

        class Recording:
            def __init__(self, *args, **kwargs):
                pass

            def call(self, method, route, payload=None, **kwargs):
                sent.update(payload or {})

                return {"name": "demo", "hostname": "demo.localhost",
                        "url": "http://demo.localhost", "root": payload["root"],
                        "https": None, "detected": False}

        monkeypatch.setattr(cli, "Client", Recording)
        monkeypatch.chdir(tmp_path)

        cli.main(["site", "add", "demo", "."])

        assert sent["root"] == str(tmp_path.resolve())
