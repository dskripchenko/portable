"""
Choosing where the installation lives.

`%LOCALAPPDATA%` is the default and not always a usable one. AppLocker's common
configurations deny execution from under a user's profile — that is exactly
where software installed without administrator rights ends up, which is why the
rule exists — and everything this tool downloads is an executable. On such a
machine the default is not merely unwelcome: nothing starts.

So the location is settable, and the tests below are mostly about the ways that
can go quietly wrong rather than about the setting itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from portable import cli, paths


def bundle(tmp_path: Path, interpreter: str = "python/bin/python3") -> Path:
    """A directory shaped like an unpacked bundle, with the launcher in it."""
    root = tmp_path / "bundle"
    executable = root / interpreter
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    (root / "portable").write_text("", encoding="utf-8")

    return root


class TestResolution:
    def test_the_environment_wins_over_a_recorded_setting(self, tmp_path, monkeypatch):
        # `--home` works by setting this variable, so this is also what makes a
        # one-off override actually override.
        monkeypatch.setattr("sys.executable", str(bundle(tmp_path) / "python/bin/python3"))
        paths.set_home(tmp_path / "recorded")
        monkeypatch.setenv("PORTABLE_HOME", str(tmp_path / "asked-for"))

        home, source = paths.resolved()

        assert home == (tmp_path / "asked-for").resolve()
        assert source == "PORTABLE_HOME"

    def test_a_recorded_setting_wins_over_the_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PORTABLE_HOME", raising=False)
        monkeypatch.setattr("sys.executable", str(bundle(tmp_path) / "python/bin/python3"))
        paths.set_home(tmp_path / "elsewhere")

        assert paths.root() == (tmp_path / "elsewhere").resolve()

    def test_it_reports_what_decided(self, tmp_path, monkeypatch):
        # Two of the three ways this can be set are invisible from outside: a
        # variable exported in another shell, a file written months ago. "Why is
        # it installing over there" needs an answer the tool can give.
        monkeypatch.delenv("PORTABLE_HOME", raising=False)
        root = bundle(tmp_path)
        monkeypatch.setattr("sys.executable", str(root / "python/bin/python3"))
        paths.set_home(tmp_path / "elsewhere")

        assert paths.resolved()[1] == str(root / paths.POINTER)

    def test_beside_stays_a_word_and_is_not_frozen_into_a_path(self, tmp_path, monkeypatch):
        """
        The case an absolute path cannot express.

        A flash drive is `E:` on one machine and `F:` on the next. Recording
        where it happened to be mounted when the setting was made produces a
        bundle that works on exactly one computer — which is the opposite of
        what somebody choosing this option asked for.
        """
        monkeypatch.delenv("PORTABLE_HOME", raising=False)
        root = bundle(tmp_path)
        monkeypatch.setattr("sys.executable", str(root / "python/bin/python3"))

        paths.set_home(paths.BESIDE)

        assert (root / paths.POINTER).read_text(encoding="utf-8").strip() == paths.BESIDE
        assert paths.root() == (root / "data").resolve()

        # Now the drive is mounted somewhere else, with the bundle still on it.
        moved = tmp_path / "another-letter"
        root.rename(moved)
        monkeypatch.setattr("sys.executable", str(moved / "python/bin/python3"))

        assert paths.root() == (moved / "data").resolve()


class TestFindingTheBundle:
    def test_it_finds_the_launcher_from_the_windows_layout(self, tmp_path, monkeypatch):
        root = tmp_path / "bundle"
        (root / "python").mkdir(parents=True)
        (root / "python" / "python.exe").write_text("", encoding="utf-8")
        (root / "portable.cmd").write_text("", encoding="utf-8")
        monkeypatch.setattr("sys.executable", str(root / "python" / "python.exe"))

        assert paths.bundle() == root

    def test_a_source_checkout_has_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.executable", str(tmp_path / "usr" / "bin" / "python3"))

        assert paths.bundle() is None

    def test_setting_it_without_a_bundle_says_what_to_do_instead(self, tmp_path, monkeypatch):
        # There is nowhere to record it, and "it did not work" would leave the
        # person with no next move.
        monkeypatch.setattr("sys.executable", str(tmp_path / "usr" / "bin" / "python3"))

        with pytest.raises(paths.NotABundle) as excinfo:
            paths.set_home(tmp_path / "anywhere")

        assert "PORTABLE_HOME" in str(excinfo.value)


class TestRefusingBadChoices:
    def test_a_path_that_cannot_be_written_to_fails_now_not_later(self, tmp_path, monkeypatch):
        """
        The whole point of checking at all.

        An unmounted drive, a folder policy forbids, a name already taken by a
        file — each of those surfaces halfway through a download otherwise, as
        an error about a directory the person configured weeks ago and has since
        forgotten about.
        """
        monkeypatch.setattr("sys.executable", str(bundle(tmp_path) / "python/bin/python3"))
        occupied = tmp_path / "a-file"
        occupied.write_text("", encoding="utf-8")

        with pytest.raises(paths.UnusableHome):
            paths.set_home(occupied)

    def test_a_refused_path_is_not_recorded(self, tmp_path, monkeypatch):
        # Otherwise the failure leaves the tool pointed at the bad location and
        # every later command fails, including the one that would fix it.
        monkeypatch.delenv("PORTABLE_HOME", raising=False)
        root = bundle(tmp_path)
        monkeypatch.setattr("sys.executable", str(root / "python/bin/python3"))
        occupied = tmp_path / "a-file"
        occupied.write_text("", encoding="utf-8")

        with pytest.raises(paths.UnusableHome):
            paths.set_home(occupied)

        assert not (root / paths.POINTER).exists()
        assert paths.root() == paths.default_root()


class TestTheCommand:
    def run(self, argv, capsys) -> dict:
        assert cli.main(argv) in (0, 1)

        return json.loads(capsys.readouterr().out)

    def test_the_flag_is_taken_on_either_side_of_the_command(self, tmp_path, capsys):
        # Somebody typing it after the verb is not making a mistake.
        first = self.run(["--home", str(tmp_path / "x"), "home", "--json"], capsys)
        second = self.run(["home", "--json", "--home", str(tmp_path / "x")], capsys)

        assert first["home"] == second["home"] == str((tmp_path / "x").resolve())

    def test_moving_it_while_the_daemon_runs_is_refused(self, tmp_path, monkeypatch, capsys):
        """
        The trap worth a test of its own.

        A running daemon has its logs open in the old location, its runtimes
        there, and — the part that bites — its discovery file there. That file
        is the only way any client finds it. Change the setting out from under
        it and the daemon is still running, still holding ports 80 and 5432, and
        no longer reachable by anything, including `portable down`.
        """
        monkeypatch.setattr("sys.executable", str(bundle(tmp_path) / "python/bin/python3"))
        monkeypatch.setattr(
            "portable.cli.discovery.read",
            lambda: type("E", (), {"pid": 1, "port": 2, "token": "t"})(),
        )

        result = self.run(["home", "set", str(tmp_path / "new"), "--json"], capsys)

        assert result["errorKey"] == "still-running"
        assert "portable down" in result["message"]

    def test_it_says_what_was_left_behind(self, tmp_path, monkeypatch, capsys):
        # Nothing is moved — copying hundreds of megabytes is a surprising thing
        # for a settings command to do, and a half-done copy is worse. But
        # saying nothing is how somebody re-downloads PHP without understanding
        # why, and never reclaims the disk.
        monkeypatch.delenv("PORTABLE_HOME", raising=False)
        root = bundle(tmp_path)
        monkeypatch.setattr("sys.executable", str(root / "python/bin/python3"))

        old = tmp_path / "old"
        (old / "runtimes" / "php-8.4").mkdir(parents=True)
        (old / "runtimes" / "php-8.4" / "php.exe").write_text("x" * 2048, encoding="utf-8")
        paths.set_home(old)

        result = self.run(["home", "set", str(tmp_path / "new"), "--json"], capsys)

        assert result["leftBehind"] == str(old.resolve())

    def test_it_admits_when_the_environment_overrules_the_reset(self, tmp_path, monkeypatch, capsys):
        # `home clear` deletes the pointer and the location does not change,
        # because a variable is set in this shell. Reporting success without
        # saying so is how the next half hour gets spent.
        root = bundle(tmp_path)
        monkeypatch.setattr("sys.executable", str(root / "python/bin/python3"))
        monkeypatch.setenv("PORTABLE_HOME", str(tmp_path / "from-the-shell"))

        assert cli.main(["home", "clear"]) == 0

        assert "PORTABLE_HOME" in capsys.readouterr().out
