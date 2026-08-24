"""
A pool of `php-cgi` processes, standing in for the php-fpm that Windows lacks.

FPM is a Unix-only SAPI. The Windows build of PHP ships `php-cgi.exe`, which
speaks FastCGI when given `-b <address:port>` and serves **one request at a
time**. It also cannot fork children of its own: `PHP_FCGI_CHILDREN` is read on
platforms with `fork()`, which Windows is not.

So concurrency is N separate processes on N separate ports, and the router
balances across them. Everything below follows from that.

Two consequences worth naming, because they look like bugs otherwise:

- **A pool member exiting is normal.** `PHP_FCGI_MAX_REQUESTS` makes it
  terminate deliberately after a set number of requests, as memory hygiene. The
  supervisor starting it again is the design, not error recovery.
- **The pool size is the concurrency limit.** With four workers, a fifth
  simultaneous request waits. There is no queue inside `php-cgi` to absorb it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import ports
from .runtimes import Installed
from .supervisor import Spec

#: Workers per PHP version. Four is enough for a browser opening a page that
#: fires a handful of XHRs, which is the shape of local development; a page that
#: waits on itself — PHP calling its own API — needs at least two.
DEFAULT_WORKERS = 4

#: Requests before a worker retires itself. PHP's own recommendation for
#: long-running FastCGI, and the reason the supervisor treats an exit as routine.
DEFAULT_MAX_REQUESTS = 500


@dataclass(frozen=True)
class Worker:
    name: str
    port: int
    spec: Spec


@dataclass(frozen=True)
class Pool:
    """One PHP version's workers, and where to reach them."""

    version: str
    workers: list[Worker]

    @property
    def upstreams(self) -> list[str]:
        """`127.0.0.1:9000` per worker, for the router's balancer."""
        return [f"127.0.0.1:{worker.port}" for worker in self.workers]

    @property
    def specs(self) -> list[Spec]:
        return [worker.spec for worker in self.workers]


def worker_command(executable: Path, port: int, ini: Path) -> list[str]:
    """
    How one worker is started.

    `-b` is what turns `php-cgi` into a FastCGI server; without it the process
    reads a single request from standard input and exits.

    A named function rather than an inline list because it is the seam between
    "how the pool is shaped" and "what binary is run" — the same boundary
    `router_command` draws for Caddy. Anyone wrapping `php-cgi` in something,
    and the tests that must run where `php-cgi.exe` does not exist, replace
    exactly this.
    """
    return [str(executable), "-b", f"127.0.0.1:{port}", "-c", str(ini)]


def build(
    runtime: Installed,
    ini: Path,
    logs: Path,
    workers: int = DEFAULT_WORKERS,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    reserved: set[int] | None = None,
    command: Callable[[Path, int, Path], list[str]] = worker_command,
) -> Pool:
    """
    Describe a pool. Nothing is started here.

    Building and running are separate so that the arithmetic — how many, on
    which ports, with what environment — can be tested on a machine that has no
    `php-cgi.exe` at all. Which is every machine this is developed on.
    """
    if workers < 1:
        raise ValueError("A pool needs at least one worker.")

    executable = runtime.executable("php")
    allocated = ports.find(workers, taken=reserved)

    return Pool(
        version=runtime.version,
        workers=[
            Worker(
                name=f"php-{runtime.version}-{index}",
                port=port,
                spec=Spec(
                    name=f"php-{runtime.version}-{index}",
                    argv=command(executable, port, ini),
                    env=_environment(max_requests),
                    log=logs / f"php-{runtime.version}-{index}.log",
                    restart=True,
                ),
            )
            for index, port in enumerate(allocated, start=1)
        ],
    )


def _environment(max_requests: int) -> dict[str, str]:
    environment = dict(os.environ)

    # Honoured on Windows: the worker counts requests and exits when it reaches
    # the limit. `PHP_FCGI_CHILDREN` is deliberately absent — it needs `fork()`,
    # and setting it here would suggest the pool is managed by PHP when it is
    # managed by us.
    environment["PHP_FCGI_MAX_REQUESTS"] = str(max_requests)

    return environment


def ini_for(runtime: Installed, into: Path) -> Path:
    """
    Write a `php.ini` for this version and return its path.

    Generated rather than taken from the archive: PHP ships `php.ini-development`
    and `php.ini-production` and no `php.ini` at all, so a fresh unpack has no
    configuration and every extension switched off.

    The file is written once and then left alone. Someone will edit it — that is
    the point of having it on disk — and regenerating it on every start would
    quietly discard their work.
    """
    into.mkdir(parents=True, exist_ok=True)
    target = into / f"php-{runtime.version}.ini"

    if target.exists():
        return target

    extension_dir = runtime.directory / "ext"
    lines = [
        "; Written by portable when this PHP was installed, and not touched again.",
        "; Edit freely — nothing here regenerates it.",
        "",
        f'extension_dir = "{extension_dir}"',
        "",
        "; A development default. `display_errors` is on because the alternative",
        "; on a local machine is a blank page and a hunt through the log.",
        "display_errors = On",
        "error_reporting = E_ALL",
        "",
        "; Uploads and execution limits raised past the defaults, which are",
        "; sized for shared hosting rather than for a machine you own.",
        "upload_max_filesize = 128M",
        "post_max_size = 128M",
        "memory_limit = 512M",
        "max_execution_time = 300",
        "",
        "; Extensions common enough that their absence reads as a broken install.",
        "extension = curl",
        "extension = fileinfo",
        "extension = gd",
        "extension = intl",
        "extension = mbstring",
        "extension = openssl",
        "extension = pdo_mysql",
        "extension = pdo_pgsql",
        "extension = pdo_sqlite",
        "extension = zip",
        "",
        "[opcache]",
        "; On, because a request that recompiles every file on every hit is the",
        "; difference between local development being pleasant and not.",
        "zend_extension = opcache",
        "opcache.enable = 1",
        "opcache.enable_cli = 0",
        "; Revalidate on every request: correctness beats speed when the files",
        "; under it are being edited continuously.",
        "opcache.validate_timestamps = 1",
        "opcache.revalidate_freq = 0",
    ]

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return target
