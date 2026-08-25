"""
Putting the installation on the current user's PATH.

The user's PATH is in `HKEY_CURRENT_USER` and needs no administrator; the
machine's is in `HKEY_LOCAL_MACHINE` and does. Only the first is ever touched,
and there is no option for the second.

Most of what follows guards against ways this operation is routinely got wrong,
each of which has cost somebody their PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from portable import userpath


class TestComparingEntries:
    def test_the_separators_are_the_same_separator(self):
        """
        `C:\\portable` and `C:/portable` are the same directory.

        Windows accepts both, and an entry written one way should match a
        directory written the other. Treating them as different is how a
        directory ends up on the PATH twice.
        """
        entries = ["C:\\Windows", "C:\\portable\\"]

        assert userpath._index(entries, Path("C:/portable")) == 1

    @pytest.mark.skipif(os.name != "nt", reason="paths are case-insensitive on Windows only")
    def test_case_does_not_hide_it(self):
        assert userpath._index(["c:\\PORTABLE"], Path("C:/portable")) == 0

    def test_quotes_and_spaces_do_not_hide_it(self):
        entries = ['  "C:\\portable"  ']

        assert userpath._index(entries, Path("C:\\portable")) == 0

    def test_something_that_is_not_there_is_not_found(self):
        assert userpath._index(["C:\\Windows"], Path("C:/portable")) is None

    def test_a_variable_is_compared_as_written(self):
        # Expanding `%USERPROFILE%\bin` would be guessing what it means on some
        # other day, and the whole point of the expanding type is that the
        # answer is not fixed.
        assert userpath._index(["%USERPROFILE%\\bin"], Path("C:/Users/x/bin")) is None


class TestWhatIsReadAndWritten:
    def test_the_state_knows_whether_a_directory_is_on_it(self):
        state = userpath.State(entries=["C:\\Windows", "C:\\portable"], expandable=False)

        assert state.has(Path("C:\\portable"))
        assert not state.has(Path("C:\\elsewhere"))

    def test_off_windows_it_says_so_rather_than_guessing(self):
        # There is no single per-user PATH elsewhere; it is set by a shell
        # profile, and editing somebody's dotfiles is not this tool's business.
        if os.name == "nt":
            pytest.skip("this is the Windows behaviour")

        with pytest.raises(userpath.PathError) as excinfo:
            userpath.read()

        assert "portable env" in str(excinfo.value)


@pytest.mark.skipif(os.name != "nt", reason="the user PATH is a Windows registry value")
class TestAgainstTheRealRegistry:
    """
    Written against `HKEY_CURRENT_USER`, which is this account's own and needs
    no elevation. Each test restores what it found.
    """

    @pytest.fixture(autouse=True)
    def restore(self):
        before = userpath.read()

        yield

        userpath._write(before)

    def test_adding_and_removing_leaves_it_as_it_was(self, tmp_path):
        before = userpath.read()

        assert userpath.add(tmp_path) is True
        assert userpath.read().has(tmp_path)

        assert userpath.remove(tmp_path) is True
        assert userpath.read().entries == before.entries

    def test_adding_twice_changes_nothing_the_second_time(self, tmp_path):
        userpath.add(tmp_path)

        assert userpath.add(tmp_path) is False
        assert [e for e in userpath.read().entries if str(tmp_path).lower() in e.lower()] != []
        assert len([e for e in userpath.read().entries if str(tmp_path).lower() == e.lower()]) == 1

    def test_the_expanding_type_survives(self, tmp_path):
        """
        A user PATH very often contains `%USERPROFILE%\\...`.

        `REG_EXPAND_SZ` is what makes that a path rather than a literal percent
        sign. Rewriting the value as a plain string freezes it, and the breakage
        turns up later and somewhere else — when the profile moves, or on
        another machine entirely.
        """
        import winreg

        userpath._write(userpath.State(entries=["%USERPROFILE%\\bin"], expandable=True))
        userpath.add(tmp_path)

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, userpath.KEY) as key:
            _, kind = winreg.QueryValueEx(key, "Path")

        assert kind == winreg.REG_EXPAND_SZ
        assert "%USERPROFILE%\\bin" in userpath.read().entries

    def test_the_machines_path_is_not_copied_into_the_users(self):
        """
        The mistake that has ruined the most PATHs.

        `os.environ["PATH"]` is the machine's and the user's already joined
        together. Writing that back into the user's copies every system entry
        into it, where they persist after being removed from the system — and
        the two disagree from then on.
        """
        combined = os.environ["PATH"].split(os.pathsep)
        stored = userpath.read().entries

        assert len(stored) < len(combined), "the user PATH looks like the combined one"
        assert "C:\\Windows\\system32" not in [entry.rstrip("\\") for entry in stored]
