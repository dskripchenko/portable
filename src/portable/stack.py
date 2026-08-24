"""
Making what is running match what is declared.

One operation matters here — `reconcile()` — and it is written as a reconcile
rather than as a set of start/stop commands on purpose. Adding a site, removing
one, and recovering after a crash then all take the same path, instead of each
being its own sequence with its own way of being half-finished.

What it decides:

- a pool of `php-cgi` workers per PHP version any site asks for, and none for a
  version nothing asks for;
- Caddy, running, with a configuration covering exactly the sites that exist.

Caddy is reconfigured through its admin API rather than restarted. Restarting
drops every connection in flight, and adding a second site should not interrupt
the first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, pool, ports
from .router import caddy
from .runtimes import Installed, NotInstalled
from .runtimes import Registry as Runtimes
from .sites import Site
from .supervisor import Spec, Supervisor

#: The port sites are served on.
#:
#: 80 is attempted first and is not a privilege question on Windows, which — one
#: of the few places it is friendlier than Unix — lets an ordinary user bind
#: below 1024. It can still be held by `http.sys`, IIS or the World Wide Web
#: Publishing Service, so a fallback is needed and 8080 is the conventional one.
PREFERRED_PORT = 80
FALLBACK_PORT = 8080


class StackError(RuntimeError):
    """Something needed is not installed, or will not start."""


@dataclass
class Stack:
    """The running processes, and the arrangement they were started for."""

    supervisor: Supervisor
    runtimes: Runtimes
    pools: dict[str, pool.Pool] = field(default_factory=dict)
    """Keyed by resolved PHP version, not by what a site asked for: two sites
    saying `8.4` and `8.4.24` share one pool rather than starting two."""

    candidate_ports: tuple[int, ...] = (PREFERRED_PORT, FALLBACK_PORT)
    """
    Ports to try, in order.

    80 first because `demo.localhost` reads better than `demo.localhost:8080`,
    and on Windows an ordinary user may bind it. A field rather than a constant
    so a machine that wants a fixed port can have one — and so the tests do not
    depend on what happens to be listening on 80.
    """

    port: int | None = None
    admin: str | None = None
    """
    Where this installation's Caddy takes its orders.

    Allocated rather than left at Caddy's default of 2019: any other Caddy on
    the machine already holds that, and the second to start fails with an error
    about a port rather than about a conflict.
    """

    caddy_running: bool = False

    def reconcile(self, sites: list[Site]) -> dict:
        """
        Bring the processes into line with [sites]. Returns what changed.

        Safe to call repeatedly: it starts what is missing and stops what is no
        longer wanted, and doing nothing is the normal outcome.
        """
        if not sites:
            self._stop_everything()

            return {"sites": 0, "pools": [], "port": None}

        wanted = self._versions_for(sites)
        started = self._ensure_pools(wanted)
        stopped = self._stop_unused_pools(wanted)
        resolved = self._resolve(sites)
        self._ensure_caddy(resolved)

        return {
            "sites": len(sites),
            "pools": sorted(self.pools),
            "pools_started": started,
            "pools_stopped": stopped,
            "port": self.port,
        }

    # ------------------------------------------------------------------- pools

    def _versions_for(self, sites: list[Site]) -> dict[str, Installed]:
        """
        Which PHP each site actually gets, resolved once.

        A site asking for nothing follows the newest installed version; one
        asking for `8.4` gets the newest `8.4.x`. Both collapse to the same key
        when they land on the same build, which is what stops two sites from
        starting two identical pools.
        """
        resolved: dict[str, Installed] = {}

        for site in sites:
            try:
                runtime = self.runtimes.get("php", site.php)
            except NotInstalled as error:
                raise StackError(
                    f"{site.name} needs PHP {site.php or '(any)'}, which is not installed. "
                    f"{error}"
                ) from error

            resolved[runtime.version] = runtime

        return resolved

    def _ensure_pools(self, wanted: dict[str, Installed]) -> list[str]:
        started = []

        for version, runtime in wanted.items():
            if version in self.pools:
                continue

            ini = pool.ini_for(runtime, paths.root() / "conf")
            reserved = {worker.port for existing in self.pools.values() for worker in existing.workers}
            built = pool.build(runtime, ini=ini, logs=paths.logs(), reserved=reserved)

            for spec in built.specs:
                self.supervisor.add(spec)
                self.supervisor.start(spec.name)

            self.pools[version] = built
            started.append(version)

        return started

    def _stop_unused_pools(self, wanted: dict[str, Installed]) -> list[str]:
        """
        Retire a pool nothing points at any more.

        Left running it would hold four ports and a few hundred megabytes for a
        PHP version no site uses — invisible, because nothing would ever fail.
        """
        stopped = []

        for version in list(self.pools):
            if version in wanted:
                continue

            for worker in self.pools.pop(version).workers:
                self.supervisor.stop(worker.name)
                self.supervisor.forget(worker.name)

            stopped.append(version)

        return stopped

    # ------------------------------------------------------------------- caddy

    def _resolve(self, sites: list[Site]) -> list[caddy.Site]:
        resolved = []

        for site in sites:
            runtime = self.runtimes.get("php", site.php)
            built = self.pools[runtime.version]

            resolved.append(
                caddy.Site(
                    name=site.name,
                    root=site.root,
                    upstreams=built.upstreams,
                    index=site.index,
                )
            )

        return resolved

    def _ensure_caddy(self, sites: list[caddy.Site]) -> None:
        if self.caddy_running:
            self._push_config(sites)

            return

        self._start_caddy(sites)

    def _start_caddy(self, sites: list[caddy.Site]) -> None:
        try:
            runtime = self.runtimes.get("caddy")
        except NotInstalled as error:
            raise StackError(f"Caddy is not installed. {error}") from error

        if self.admin is None:
            self.admin = f"127.0.0.1:{ports.ephemeral()}"

        # Attempted and observed, rather than predicted: `http.sys` reserves
        # ports on Windows without listening on them, so nothing short of trying
        # tells you whether one is yours.
        for candidate in self.candidate_ports:
            config_file = self._write_config(sites, candidate)
            spec = Spec(
                name="caddy",
                argv=caddy.command(runtime.executable("caddy"), config_file),
                log=paths.logs() / "caddy.log",
                restart=True,
            )

            try:
                self.supervisor.add(spec)
            except ValueError:
                self.supervisor.forget("caddy")
                self.supervisor.add(spec)

            self.supervisor.start("caddy")

            if self._serving_on(candidate):
                self.port = candidate
                self.caddy_running = True

                return

            self.supervisor.stop("caddy")

        tried = " or ".join(str(candidate) for candidate in self.candidate_ports)

        raise StackError(
            f"Caddy would not start on port {tried}. See {paths.logs() / 'caddy.log'}."
        )

    def _write_config(self, sites: list[caddy.Site], port: int) -> Path:
        directory = paths.root() / "conf"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "caddy.json"
        target.write_text(
            json.dumps(
                caddy.config(sites, listen=port, admin=self.admin or caddy.DEFAULT_ADMIN),
                indent=2,
            ),
            encoding="utf-8",
        )

        return target

    def _push_config(self, sites: list[caddy.Site]) -> None:
        """
        Hand Caddy a new configuration without restarting it.

        The whole reason the configuration is JSON. A restart would drop every
        connection in flight, and adding a second site has no business
        interrupting the first.
        """
        document = caddy.config(
            sites,
            listen=self.port or self.candidate_ports[0],
            admin=self.admin or caddy.DEFAULT_ADMIN,
        )
        self._write_config(sites, self.port or self.candidate_ports[0])

        import urllib.request

        request = urllib.request.Request(
            f"http://{self.admin or caddy.DEFAULT_ADMIN}/load",
            data=json.dumps(document).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()

    #: Sent to a port to find out whether *our* Caddy is the thing on it. The
    #: hostname is one no site can be called — a dot is rejected by the site name
    #: rules — so the answer is always the unmatched-host route.
    PROBE_HOST = "portable.probe.invalid"

    def _serving_on(self, port: int, timeout: float = 10.0) -> bool:
        """
        Whether **our** router is answering on [port].

        Not "is Caddy alive": its admin endpoint answers on its own port and
        keeps answering when the site listener fails to bind, so asking it would
        report success for a port held by something else entirely — IIS, or
        `http.sys`, or another web server on the same machine. The fallback to
        8080 would then never happen and nothing would be served.

        That is not hypothetical. On the machine this was written on, port 80 was
        already held by another tool's nginx; both were listening, on different
        address families, and only a probe of the port itself could tell which
        one would answer.

        The probe asks for a hostname no site can have and looks for the
        signature of our own unmatched-host route. A foreign server answering
        404 to the same request would not include it.
        """
        import time
        import urllib.error
        import urllib.request

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/", headers={"Host": self.PROBE_HOST}
                )

                with urllib.request.urlopen(request, timeout=2) as response:
                    body = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)

                continue

            return "portable: no site is configured" in body

        return False

    def _stop_everything(self) -> None:
        for version in list(self.pools):
            for worker in self.pools.pop(version).workers:
                self.supervisor.stop(worker.name)
                self.supervisor.forget(worker.name)

        if self.caddy_running:
            self.supervisor.stop("caddy")
            self.supervisor.forget("caddy")
            self.caddy_running = False
            self.port = None
