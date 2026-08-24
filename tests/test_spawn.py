"""
Starting processes that outlive their launcher — and ones that must not.

The two cases are opposite and both matter. The daemon has to survive its
terminal closing. The runtimes the daemon starts must **not** survive the
daemon, or `portable down` leaves a `php-cgi.exe` holding a port and the next
`portable up` fails pointing nowhere near the cause.

The Windows flag arithmetic is checked on every platform, because it is the part
that cannot be observed from here — and a wrong constant would go unnoticed
until someone closed a terminal on Windows and lost their stack.
"""

from __future__ import annotations

import sys
import time

from portable import spawn


class TestFlags:
    def test_detached_flags_cover_all_three_ways_a_parent_kills_a_child(self):
        flags = spawn.detached_flags()

        # The console — CTRL_CLOSE_EVENT goes to everything attached to it.
        assert flags & spawn.DETACHED_PROCESS
        # The process group — where Ctrl+C is delivered.
        assert flags & spawn.CREATE_NEW_PROCESS_GROUP
        # The job object — some IDE run configurations kill on close.
        assert flags & spawn.CREATE_BREAKAWAY_FROM_JOB

    def test_breakaway_can_be_dropped_without_losing_the_rest(self):
        # A job that forbids breakaway fails the call outright. Starting without
        # the flag beats not starting: the case that actually happens is a
        # terminal closing, and the other two flags cover it.
        flags = spawn.detached_flags(breakaway=False)

        assert not flags & spawn.CREATE_BREAKAWAY_FROM_JOB
        assert flags & spawn.DETACHED_PROCESS
        assert flags & spawn.CREATE_NEW_PROCESS_GROUP

    def test_supervised_children_are_not_detached(self):
        # The opposite requirement, and getting it backwards is how orphans
        # happen: these have to die with the daemon.
        flags = spawn.child_flags()

        assert not flags & spawn.DETACHED_PROCESS
        assert not flags & spawn.CREATE_BREAKAWAY_FROM_JOB

        if spawn.WINDOWS:
            # No console window flashing up for each member of a pool.
            assert flags & spawn.CREATE_NO_WINDOW


class TestLiveness:
    def test_a_running_process_is_reported_running(self):
        process = spawn.start_child([sys.executable, "-c", "import time; time.sleep(30)"])

        try:
            assert spawn.is_running(process.pid)
        finally:
            process.kill()
            process.wait(timeout=5)

    def test_an_exited_process_is_not(self):
        process = spawn.start_child([sys.executable, "-c", "pass"])
        process.wait(timeout=5)

        # A brief grace period: on Windows the handle can outlive the exit.
        deadline = time.monotonic() + 2

        while spawn.is_running(process.pid) and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not spawn.is_running(process.pid)

    def test_an_impossible_pid_is_not_running(self):
        assert not spawn.is_running(0)
        assert not spawn.is_running(-1)


class TestDetachedStart:
    def test_it_returns_a_pid_that_is_alive(self, tmp_path):
        pid = spawn.start_detached(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            log=tmp_path / "d.log",
        )

        assert pid > 0
        assert spawn.is_running(pid)

    def test_output_goes_to_the_log_not_to_our_streams(self, tmp_path):
        # Writing into a pipe nobody reads fills the buffer and blocks the
        # writer — a daemon that stops serving after its terminal closes looks
        # exactly like a daemon that died.
        log = tmp_path / "out.log"
        spawn.start_detached([sys.executable, "-c", "print('hello from detached')"], log=log)

        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            if log.exists() and "hello from detached" in log.read_text():
                return

            time.sleep(0.05)

        raise AssertionError(f"nothing reached the log: {log.read_text() if log.exists() else '(absent)'}")


class TestNoZombies:
    def test_a_detached_child_that_exits_stops_being_reported_as_running(self):
        """
        The bug this guards against was invisible from a terminal.

        A detached child is still this process's child. When it exits and nobody
        waits on it, POSIX keeps a zombie entry — and a zombie answers
        `kill(pid, 0)`, so `is_running` says "alive" forever. From a shell it
        never showed: the shell exits straight after launching, init adopts the
        daemon and reaps it. It appeared the moment the launcher outlived the
        child, which is what a test does, and `down` sat waiting ten seconds for
        a process that had already gone.
        """
        pid = spawn.start_detached([sys.executable, "-c", "pass"])

        deadline = time.monotonic() + 5

        while spawn.is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not spawn.is_running(pid), (
            "the exited child is still reported as running — it was left unreaped"
        )
