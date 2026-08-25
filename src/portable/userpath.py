"""
Putting this installation on the PATH, for this user only.

The user's PATH lives in `HKEY_CURRENT_USER\\Environment` and needs no
administrator. The machine's lives in `HKEY_LOCAL_MACHINE` and does — so only
the first is ever touched here, and there is no option to touch the second.

This is the one thing in the whole tool that writes outside its own directory,
which is why it is a command somebody runs rather than something an install
does. `portable path remove` puts it back exactly as it was.

Three ways this operation is routinely got wrong, each of which has cost
somebody their PATH:

- **Reading `os.environ["PATH"]` and writing it back.** That variable is the
  machine's PATH and the user's already joined together, so writing it into the
  user's copies every system entry into it. They then persist after being
  removed from the system, and the two disagree forever. The registry value is
  read directly instead.
- **Writing `REG_SZ` over a `REG_EXPAND_SZ`.** A user PATH very often contains
  `%USERPROFILE%\\...`, and the expanding type is what makes that a path rather
  than a literal percent sign. Rewriting it as a plain string freezes it, and
  the breakage appears later, somewhere else, when the profile moves. The
  existing type is read and preserved.
- **Not telling anybody.** A registry write alone reaches no running program:
  Explorer, and therefore every shell started from it afterwards, only re-reads
  the environment when it is told the setting changed. Without the broadcast the
  entry appears to have done nothing until the next sign-in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Where the current user's environment lives. Not `HKEY_LOCAL_MACHINE`, which
#: is the machine's and needs administrator rights.
KEY = "Environment"

WINDOWS = os.name == "nt"


class PathError(RuntimeError):
    """The PATH could not be read or written, with the reason."""


@dataclass(frozen=True)
class State:
    entries: list[str]
    """The user's PATH, as stored — not merged with the machine's."""

    expandable: bool
    """Whether it is stored as `REG_EXPAND_SZ` and may contain `%VARIABLES%`."""

    def has(self, directory: Path) -> bool:
        return _index(self.entries, directory) is not None


def read() -> State:
    """The user's own PATH, exactly as the registry holds it."""
    if not WINDOWS:
        raise PathError(
            "Only Windows keeps a per-user PATH in one place. Elsewhere it is set "
            "by your shell's profile; `portable env` prints what to add."
        )

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, kind = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        # A user who has never had one. Perfectly ordinary on a fresh account.
        return State(entries=[], expandable=False)
    except OSError as error:
        raise PathError(f"Could not read your PATH: {error}") from error

    return State(
        entries=[part for part in str(value).split(os.pathsep) if part.strip()],
        expandable=kind == winreg.REG_EXPAND_SZ,
    )


def add(directory: Path) -> bool:
    """Put a directory on the user's PATH. Returns whether anything changed."""
    state = read()
    directory = directory.resolve()

    if state.has(directory):
        return False

    _write(State(entries=[*state.entries, str(directory)], expandable=state.expandable))

    return True


def remove(directory: Path) -> bool:
    """Take it off again. Returns whether anything changed."""
    state = read()
    directory = directory.resolve()
    position = _index(state.entries, directory)

    if position is None:
        return False

    remaining = [*state.entries[:position], *state.entries[position + 1 :]]
    _write(State(entries=remaining, expandable=state.expandable))

    return True


def _write(state: State) -> None:
    import winreg

    kind = winreg.REG_EXPAND_SZ if state.expandable else winreg.REG_SZ

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Path", 0, kind, os.pathsep.join(state.entries))
    except OSError as error:
        raise PathError(f"Could not write your PATH: {error}") from error

    announce()


def announce() -> bool:
    """
    Tell Windows the environment changed. Returns whether anybody was listening.

    Without this the registry holds the new value and nothing knows: Explorer
    reads the environment once and hands a copy to everything it starts, so the
    change would first appear at the next sign-in — long enough after the
    command for the two to seem unrelated.
    """
    if not WINDOWS:
        return False

    import ctypes

    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002

    result = ctypes.c_ulong()
    sent = ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        ctypes.c_wchar_p(KEY),
        SMTO_ABORTIFHUNG,
        # Five seconds. A window that has stopped answering should not hold up a
        # command that has already done its work.
        5000,
        ctypes.byref(result),
    )

    return bool(sent)


def _index(entries: list[str], directory: Path) -> int | None:
    """
    Where a directory sits in a PATH, comparing the way Windows does.

    Case-insensitively on Windows, ignoring a trailing separator, and treating
    the two separators as one — `C:\\portable`, `c:\\portable\\` and `C:/portable`
    are the same place. Treating them as different is how a directory ends up on
    the PATH twice, once from an installer and once by hand, and how `remove`
    then leaves one of them behind.
    """
    wanted = _normal(str(directory))

    for position, entry in enumerate(entries):
        if _normal(entry) == wanted:
            return position

    return None


def _normal(entry: str) -> str:
    # Separators folded together always. Windows accepts both, and an entry
    # written one way should match a directory written the other; on POSIX a
    # backslash is an ordinary character in a name, and one appearing in a PATH
    # entry is not something worth preserving a distinction over.
    stripped = entry.strip().strip('"').replace("\\", "/").rstrip("/")

    # `%USERPROFILE%\bin` is compared as written rather than expanded. Expanding
    # it would be guessing at what it means on some other day, and the point of
    # the expanding type is that the answer is not fixed.
    return stripped.lower() if WINDOWS else stripped
