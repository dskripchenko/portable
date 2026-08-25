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

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


class TestNothingHoldsSomebodyElsesDirectory:
    """
    A process holds its working directory open.

    On Windows that means the folder cannot be deleted or renamed for as long as
    it runs, and Explorer says only that it is "open in another program". So a
    daemon started from a project folder quietly locks it — and it matters more
    once `portable` is on PATH, because then it is run from wherever the person
    happens to be rather than from where it lives.
    """

    def test_a_supervised_process_stands_in_the_installation(self, tmp_path, monkeypatch):
        from portable import paths, supervisor

        where: dict = {}

        def remember(argv, cwd=None, env=None, log=None):
            where["cwd"] = cwd

            return subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
            )

        monkeypatch.setattr(supervisor.spawn, "start_child", remember)

        keeper = supervisor.Supervisor()
        keeper.add(supervisor.Spec(name="thing", argv=["x"], restart=False))

        try:
            keeper.start("thing")
        finally:
            keeper.stop_all()

        assert where["cwd"] == paths.root()

    def test_a_spec_may_still_ask_for_somewhere_else(self, tmp_path, monkeypatch):
        # The default is ours; it is not a rule. Something that genuinely needs
        # a directory should be able to say so.
        from portable import supervisor

        where: dict = {}

        def remember(argv, cwd=None, env=None, log=None):
            where["cwd"] = cwd

            return subprocess.Popen(
                [sys.executable, "-c", "pass"], cwd=cwd, stdout=subprocess.DEVNULL
            )

        monkeypatch.setattr(supervisor.spawn, "start_child", remember)

        keeper = supervisor.Supervisor()
        keeper.add(supervisor.Spec(name="thing", argv=["x"], cwd=tmp_path, restart=False))

        try:
            keeper.start("thing")
        finally:
            keeper.stop_all()

        assert where["cwd"] == tmp_path


@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
class TestSurvivingTheThingThatStartedIt:
    """
    The last claim in the README that was an expectation rather than a
    measurement.

    A terminal closing takes its console and its process group with it, and an
    IDE goes further: it puts everything it starts into a **job object** with
    kill-on-close, so that quitting cleans up whatever the run configuration
    left behind. A supervisor caught in one dies with the editor.

    `CREATE_BREAKAWAY_FROM_JOB` is what steps out of it, and this is where that
    is checked rather than assumed.
    """

    def job_with_kill_on_close(self):
        """A job object that kills everything in it when the handle closes."""
        import ctypes
        import ctypes.wintypes

        class LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
                ("LimitFlags", ctypes.wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.wintypes.ULONG)),
                ("PriorityClass", ctypes.wintypes.DWORD),
                ("SchedulingClass", ctypes.wintypes.DWORD),
            ]

        class COUNTERS(ctypes.Structure):
            _fields_ = [("Reserved", ctypes.c_byte * 48)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", LIMITS),
                ("IoInfo", COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)

        assert job, "could not create a job object"

        limits = EXTENDED()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        assert kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(limits), ctypes.sizeof(limits)
        ), "could not set kill-on-close"

        return job

    def test_a_detached_process_escapes_a_job_that_kills_its_members(self, tmp_path):
        """
        The case an IDE creates.

        A parent is put in a job that kills everything in it when closed; the
        parent starts a detached child and exits; the job is closed. The parent
        must die and the child must not.
        """
        import ctypes

        kernel32 = ctypes.windll.kernel32
        job = self.job_with_kill_on_close()

        marker = tmp_path / "alive.txt"
        source = str(Path(__file__).resolve().parent.parent / "src")

        # The parent: joins the job, starts a detached child, and leaves.
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys, time;"
                    "sys.path.insert(0, sys.argv[1]);"
                    "from portable import spawn;"
                    "print(spawn.start_detached([sys.executable, '-c',"
                    "    \"import sys, time; open(sys.argv[1], 'w').write('yes'); time.sleep(60)\","
                    "    sys.argv[2]]), flush=True);"
                    "time.sleep(0.5)"
                ),
                source,
                str(marker),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )

        assert kernel32.AssignProcessToJobObject(job, int(parent._handle)), (
            "could not put the parent in the job"
        )

        detached = int(parent.stdout.readline().strip())
        parent.wait(timeout=30)

        deadline = time.monotonic() + 10

        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.1)

        assert marker.exists(), "the detached process never started"

        # Closing the handle is what an IDE does when it quits.
        kernel32.CloseHandle(job)
        time.sleep(1.5)

        try:
            assert spawn.is_running(detached), (
                "the detached process was killed with the job — an IDE closing "
                "would take the supervisor with it"
            )
        finally:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(detached)], capture_output=True, check=False
            )

    def test_it_still_starts_where_breakaway_is_forbidden(self, tmp_path):
        """
        Some jobs refuse to let anything out.

        `CREATE_BREAKAWAY_FROM_JOB` then fails outright, and a supervisor that
        will not start at all is worse than one that starts and shares the
        editor's fate. The fallback drops the flag and keeps the other two.
        """
        flags = spawn.detached_flags(breakaway=False)

        assert flags & spawn.DETACHED_PROCESS
        assert flags & spawn.CREATE_NEW_PROCESS_GROUP
        assert not flags & spawn.CREATE_BREAKAWAY_FROM_JOB

        pid = spawn.start_detached(
            [sys.executable, "-c", "import time; time.sleep(5)"], log=tmp_path / "out.log"
        )

        assert spawn.is_running(pid)

        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
