"""
Keeping a set of processes alive.

This is the core of the tool, not plumbing around it, and the reason is a fact
about Windows: there is no php-fpm there. FPM is a Unix-only SAPI, and the
Windows build ships `php-cgi.exe`, which serves **one request at a time** and
cannot fork children of its own. Concurrency therefore has to come from running
several of them, and something has to run several of them.

That something also has to expect them to exit. `PHP_FCGI_MAX_REQUESTS` makes
`php-cgi` terminate after a set number of requests — deliberately, as memory
hygiene. So a process disappearing is the normal case here, not the failure
case, and restarting is the main loop rather than error handling.

What stops it becoming an infinite loop when a process cannot start at all: a
window of restarts. A pool that has died five times in ten seconds has something
wrong with it that another restart will not fix, and hammering it makes the logs
useless for finding out what.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import spawn


class State(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    """Started, and still alive the last time it was looked at."""

    FAILED = "failed"
    """Died too often, too fast. Not restarted again without being asked."""


@dataclass(frozen=True)
class Spec:
    """How to start one process."""

    name: str
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None
    log: Path | None = None

    restart: bool = True
    """
    Whether exiting means "start it again".

    True for anything serving requests. False for one-shot work, where an exit
    is the end rather than an interruption.
    """


@dataclass
class Managed:
    """One process and what has happened to it."""

    spec: Spec
    state: State = State.STOPPED
    process: subprocess.Popen | None = None
    restarts: int = 0
    """Total since the supervisor started — a health signal worth showing."""

    last_exit: int | None = None
    failure: str | None = None
    _recent: list[float] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None


class Supervisor:
    """
    Starts processes, notices when they stop, starts them again.

    One background thread watches all of them. A thread per process would be
    simpler to write and would cost a thread per `php-cgi` in every pool of
    every PHP version on the machine, for work that is a poll and a comparison.
    """

    #: More than this many restarts inside `WINDOW` seconds means the thing is
    #: broken rather than recycling. Sized against the real case: a pool member
    #: hitting PHP_FCGI_MAX_REQUESTS restarts on the order of once a minute,
    #: never five times in ten seconds.
    BURST = 5
    WINDOW = 10.0

    #: How often the watcher looks. Small enough that a dead pool member is
    #: replaced before a request finds it, large enough to be free.
    INTERVAL = 0.25

    def __init__(self) -> None:
        self.watcher_error: str | None = None
        """The last fault inside the watch loop, or None. Surfaced by `status()`."""

        self._managed: dict[str, Managed] = {}
        self._lock = threading.RLock()
        self._watcher: threading.Thread | None = None
        self._stopping = threading.Event()

    def add(self, spec: Spec) -> Managed:
        with self._lock:
            if spec.name in self._managed:
                raise ValueError(f"{spec.name} is already supervised.")

            managed = Managed(spec=spec)
            self._managed[spec.name] = managed

            return managed

    def start(self, name: str) -> Managed:
        with self._lock:
            managed = self._require(name)

            if managed.state is State.RUNNING and self._alive(managed):
                return managed

            self._launch(managed)

            return managed

    def start_all(self) -> None:
        with self._lock:
            for name in list(self._managed):
                self.start(name)

        self._ensure_watching()

    def stop(self, name: str, timeout: float = 5.0) -> None:
        """
        Stop one process, politely then not.

        The state is set before the signal, so the watcher does not see the exit
        it is about to cause and helpfully start the thing again.
        """
        with self._lock:
            managed = self._require(name)
            managed.state = State.STOPPED
            process = managed.process
            managed.process = None

        if process is None or process.poll() is not None:
            return

        process.terminate()

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

    def stop_all(self, timeout: float = 5.0) -> None:
        """
        Stop everything and the watcher.

        Nothing may be left running: an orphaned `php-cgi.exe` holding a port
        makes the next `portable up` fail in a way that points nowhere near the
        real cause.
        """
        self._stopping.set()

        for name in list(self._managed):
            self.stop(name, timeout=timeout)

        watcher = self._watcher

        if watcher and watcher.is_alive():
            watcher.join(timeout=timeout)

        self._watcher = None
        self._stopping.clear()

    def status(self) -> list[dict]:
        """A snapshot, shaped for the control API to hand to a client."""
        with self._lock:
            return [
                {
                    "name": managed.spec.name,
                    "state": managed.state.value,
                    "pid": managed.pid,
                    "restarts": managed.restarts,
                    "last_exit": managed.last_exit,
                    "failure": managed.failure,
                }
                for managed in self._managed.values()
            ]

    def reap(self) -> None:
        """
        One pass: notice what has stopped, restart what should be running.

        Public because the tests drive it directly. Waiting on a background
        thread to have noticed makes a test that is slow when it passes and
        flaky when it does not.
        """
        with self._lock:
            for managed in self._managed.values():
                if managed.state is not State.RUNNING:
                    continue

                if self._alive(managed):
                    continue

                managed.last_exit = managed.process.poll() if managed.process else None
                managed.process = None

                if not managed.spec.restart:
                    managed.state = State.STOPPED
                    continue

                if self._bursting(managed):
                    managed.state = State.FAILED
                    managed.failure = (
                        f"exited {self.BURST} times within {self.WINDOW:.0f}s — "
                        f"not restarted again. Last exit code: {managed.last_exit}."
                    )
                    continue

                managed.restarts += 1
                self._launch(managed)

    def _launch(self, managed: Managed) -> None:
        spec = managed.spec

        if spec.log:
            spec.log.parent.mkdir(parents=True, exist_ok=True)

        managed.process = spawn.start_child(spec.argv, cwd=spec.cwd, env=spec.env, log=spec.log)
        managed.state = State.RUNNING
        managed.failure = None
        managed._recent.append(time.monotonic())

    def _bursting(self, managed: Managed) -> bool:
        now = time.monotonic()
        managed._recent = [at for at in managed._recent if now - at < self.WINDOW]

        return len(managed._recent) >= self.BURST

    @staticmethod
    def _alive(managed: Managed) -> bool:
        return managed.process is not None and managed.process.poll() is None

    def _require(self, name: str) -> Managed:
        if name not in self._managed:
            raise KeyError(f"{name} is not supervised.")

        return self._managed[name]

    def _ensure_watching(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return

        self._watcher = threading.Thread(target=self._watch, name="portable-watch", daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while not self._stopping.wait(self.INTERVAL):
            try:
                self.reap()
            except Exception as error:  # noqa: BLE001 - the watcher must not die
                # The thread must survive: a supervisor whose watcher died looks
                # exactly like one with nothing to do, and the difference only
                # shows up later, as something that needed restarting and was
                # not.
                #
                # But surviving silently is the failure this whole tool was
                # written in reaction to. The last fault is kept and reported by
                # `status()`, so "the watcher is broken" is something a client
                # can see rather than deduce.
                with self._lock:
                    self.watcher_error = f"{type(error).__name__}: {error}"
