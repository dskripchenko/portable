"""
Keeping processes alive, and knowing when to stop trying.

These run real processes rather than mocks. A mocked `Popen` would prove the
supervisor calls the functions it calls, which is not the question — the
question is whether a process that exits gets replaced, and that only has an
answer if something actually exits.

The processes are short Python programs, so the suite behaves the same on
Windows as it does here. That matters: the platform this is written for is not
the platform it is written on.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from portable.supervisor import Spec, State, Supervisor


def python_running(seconds: float = 30) -> list[str]:
    """A process that stays up until it is stopped."""
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def python_exiting(code: int = 0) -> list[str]:
    """A process that exits immediately — the `PHP_FCGI_MAX_REQUESTS` shape."""
    return [sys.executable, "-c", f"raise SystemExit({code})"]


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True

        time.sleep(interval)

    return False


@pytest.fixture
def supervisor():
    instance = Supervisor()

    yield instance

    instance.stop_all(timeout=5)


class TestStarting:
    def test_a_started_process_is_running_and_has_a_pid(self, supervisor):
        supervisor.add(Spec(name="one", argv=python_running()))
        managed = supervisor.start("one")

        assert managed.state is State.RUNNING
        assert managed.pid and managed.pid > 0

    def test_starting_twice_does_not_start_a_second_copy(self, supervisor):
        # Two `php-cgi` on one port is a pool where half the requests fail.
        supervisor.add(Spec(name="one", argv=python_running()))
        first = supervisor.start("one").pid
        second = supervisor.start("one").pid

        assert first == second

    def test_an_unknown_name_is_refused(self, supervisor):
        with pytest.raises(KeyError):
            supervisor.start("nothing")


class TestRestarting:
    def test_a_process_that_exits_is_started_again(self, supervisor):
        # The normal case, not the failure case: PHP_FCGI_MAX_REQUESTS makes
        # `php-cgi` exit on purpose after N requests.
        supervisor.add(Spec(name="recycler", argv=python_exiting()))
        supervisor.start("recycler")

        assert wait_until(lambda: (supervisor.reap(), supervisor.status()[0]["restarts"] > 0)[1])

    def test_a_killed_process_is_replaced(self, supervisor):
        supervisor.add(Spec(name="one", argv=python_running()))
        managed = supervisor.start("one")
        original = managed.pid

        managed.process.kill()
        managed.process.wait(timeout=5)

        assert wait_until(lambda: (supervisor.reap(), managed.pid not in (None, original))[1])
        assert managed.state is State.RUNNING

    def test_a_process_marked_not_to_restart_stays_stopped(self, supervisor):
        supervisor.add(Spec(name="once", argv=python_exiting(), restart=False))
        supervisor.start("once")

        assert wait_until(lambda: (supervisor.reap(), supervisor.status()[0]["state"] == "stopped")[1])
        assert supervisor.status()[0]["restarts"] == 0

    def test_a_process_that_cannot_stay_up_is_given_up_on(self, supervisor):
        # Without this the supervisor spins: a misconfigured `php-cgi` that
        # exits instantly would be restarted thousands of times a second and
        # bury the one log line saying why.
        supervisor.add(Spec(name="broken", argv=python_exiting(code=1)))
        supervisor.start("broken")

        def settled() -> bool:
            supervisor.reap()

            return supervisor.status()[0]["state"] == State.FAILED.value

        assert wait_until(settled, timeout=10)

        status = supervisor.status()[0]

        assert status["restarts"] <= Supervisor.BURST
        assert "not restarted again" in status["failure"]
        assert str(status["last_exit"]) == "1", "the exit code has to survive into the report"


class TestStopping:
    def test_stopping_leaves_nothing_running(self, supervisor):
        # An orphaned `php-cgi.exe` holding a port makes the next `portable up`
        # fail for reasons that point nowhere near the real cause.
        supervisor.add(Spec(name="one", argv=python_running()))
        supervisor.add(Spec(name="two", argv=python_running()))
        supervisor.start_all()

        processes = [supervisor._managed[name].process for name in ("one", "two")]
        supervisor.stop_all(timeout=5)

        for process in processes:
            assert process.poll() is not None, "a supervised process survived stop_all"

    def test_a_stopped_process_is_not_restarted_by_the_watcher(self, supervisor):
        # The race this guards: `stop` kills the process, the watcher notices an
        # exit it caused, and dutifully starts it again.
        supervisor.add(Spec(name="one", argv=python_running()))
        supervisor.start_all()
        supervisor.stop("one")

        time.sleep(Supervisor.INTERVAL * 4)
        supervisor.reap()

        assert supervisor.status()[0]["state"] == "stopped"
        assert supervisor.status()[0]["pid"] is None


class TestLogging:
    def test_output_is_written_to_the_log_it_was_given(self, supervisor, tmp_path):
        log = tmp_path / "logs" / "one.log"
        supervisor.add(
            Spec(
                name="one",
                argv=[sys.executable, "-c", "print('started')"],
                log=log,
                restart=False,
            )
        )
        supervisor.start("one")

        assert wait_until(lambda: log.exists() and "started" in log.read_text())

    def test_the_log_directory_is_created(self, supervisor, tmp_path):
        log = Path(tmp_path) / "deep" / "nested" / "one.log"
        supervisor.add(Spec(name="one", argv=python_exiting(), log=log, restart=False))
        supervisor.start("one")

        assert log.parent.exists()
