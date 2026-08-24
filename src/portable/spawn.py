"""
Starting a process that outlives whatever started it.

The daemon is launched from a terminal — `portable up` — and has to keep running
when that terminal closes. Later the launcher might be an IDE's built-in
terminal or a plugin, but the requirement does not change with the launcher: a
child must not die because its parent went away.

On Windows that takes three separate precautions, because there are three
separate mechanisms that would otherwise kill it:

1. **The console.** A process launched from a console is attached to it, and
   Windows sends `CTRL_CLOSE_EVENT` to everything attached when the window
   closes. `DETACHED_PROCESS` means having no console at all.
2. **The process group.** Ctrl+C in a terminal is delivered to the whole group.
   `CREATE_NEW_PROCESS_GROUP` puts the daemon in its own.
3. **Job objects.** A parent inside a job with `KILL_ON_JOB_CLOSE` takes its
   children with it. Terminals rarely do this; some IDE run configurations do.
   `CREATE_BREAKAWAY_FROM_JOB` escapes — when the job permits it, which is why
   the flag is requested and its refusal tolerated rather than treated as fatal.

On POSIX one call does the same work: `start_new_session` detaches from the
controlling terminal and the process group together.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

WINDOWS = os.name == "nt"

# Defined here rather than read from `subprocess` so that the values can be
# named and tested on any platform; the constants only exist on Windows.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def detached_flags(breakaway: bool = True) -> int:
    """
    The creation flags that survive a closing terminal.

    [breakaway] is separable because `CREATE_BREAKAWAY_FROM_JOB` fails outright
    when the containing job forbids it — and a job that forbids breakaway is
    better served by starting without the flag than by not starting at all.
    """
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB

    return flags


def child_flags() -> int:
    """
    Flags for the runtimes the daemon itself starts — `php-cgi.exe`, Caddy.

    These are the opposite case. They should die with the daemon: a supervisor
    that leaves orphaned `php-cgi.exe` processes behind after `portable down` is
    worse than one that never started them. So no detachment — only
    `CREATE_NO_WINDOW`, which keeps a console window from flashing up for each
    of the eight or so processes in a pool.
    """
    return CREATE_NO_WINDOW if WINDOWS else 0


def start_detached(
    argv: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log: Path | None = None,
) -> int:
    """
    Start a process that outlives this one. Returns its pid.

    Output goes to [log] when given, and to the null device otherwise. Never to
    the parent's streams: writing into a pipe nobody reads fills the buffer and
    blocks the writer, and a daemon that stops serving after its terminal closes
    looks exactly like a daemon that died.
    """
    # Same reasoning as `start_child`: the child gets its own descriptor, ours
    # is closed the moment it is no longer needed.
    stdout = open(log, "ab", buffering=0) if log else subprocess.DEVNULL  # noqa: SIM115
    close_after = log is not None

    try:
        if WINDOWS:
            process = _start_windows(argv, cwd, env, stdout)
        else:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            _reap_when_it_exits(process)

        return process.pid
    finally:
        if close_after:
            stdout.close()


def _reap_when_it_exits(process: subprocess.Popen) -> None:
    """
    Collect the child's exit status, whenever that happens.

    POSIX only, and not housekeeping. A detached child is still this process's
    child until this process ends; when it exits and nobody has waited on it, it
    becomes a zombie — and a zombie answers `kill(pid, 0)`, so `is_running`
    reports it as alive **forever**.

    Invisible from a terminal, because the shell exits straight after `up` and
    init adopts and reaps the daemon. It appears the moment the launcher is
    long-lived: a test, or anything that starts the daemon and keeps running.
    Found exactly that way — `down` waited ten seconds for a process that had
    already exited.

    One daemon thread that spends its life blocked in `wait()` is a cheaper
    answer than a double fork, and does not fork a process that may have threads.
    """
    threading.Thread(
        target=_swallow_exit,
        args=(process,),
        name=f"portable-reap-{process.pid}",
        daemon=True,
    ).start()


def _swallow_exit(process: subprocess.Popen) -> None:
    try:
        process.wait()
    except Exception:  # noqa: BLE001, S110
        # Already reaped elsewhere, or the interpreter tore the process object
        # down on the way out. There is nothing left to collect and nothing a
        # caller could do about it — this thread's only job was to keep the
        # kernel from holding a zombie entry, and by now it is not.
        pass


def _start_windows(argv, cwd, env, stdout):
    """Try to break away from the job; fall back when the job forbids it."""
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=detached_flags(breakaway=True),
        )
    except OSError:
        # ERROR_ACCESS_DENIED from a job that does not permit breakaway. The
        # daemon still detaches from the console and the process group, which
        # covers the case that actually happens — a terminal being closed.
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=detached_flags(breakaway=False),
        )


def start_child(
    argv: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log: Path | None = None,
) -> subprocess.Popen:
    """
    Start a process the daemon supervises and owns.

    The handle is returned rather than a pid: the supervisor waits on it, and on
    Windows a pid can be reused by the operating system once the process exits.
    Polling a pid would eventually report some unrelated process as "still
    running".
    """
    # Closed as soon as the child is running. `Popen` duplicates the descriptor
    # into the child, so the parent's copy is dead weight — and a pool member
    # recycling once a minute would otherwise leak one handle per restart until
    # the daemon ran out of them, days later, far from the cause.
    stdout = open(log, "ab", buffering=0) if log else subprocess.DEVNULL  # noqa: SIM115

    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=child_flags(),
        )
    finally:
        if log:
            stdout.close()


def is_running(pid: int) -> bool:
    """
    Whether a pid belongs to a live process.

    Deliberately weak, and used only for the daemon's own record: pids are
    reused, so a true answer means "something with this number is running", not
    "the thing we started is running". Anything the supervisor owns is tracked
    by handle instead.
    """
    if pid <= 0:
        return False

    if WINDOWS:
        return _is_running_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, belongs to someone else.
        return True

    return True


def _is_running_windows(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

    if not handle:
        return False

    try:
        code = ctypes.c_ulong()

        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False

        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def python_executable() -> str:
    """
    The interpreter to re-launch ourselves with.

    `sys.executable` is the bundled CPython once this ships as a bundle, and the
    developer's interpreter before that. Either way it is the one already
    running, which is the only one guaranteed to be able to import this package.
    """
    return sys.executable
