"""
The control API.

Everything this tool can do is done through here. The CLI holds no logic of its
own — it makes a request and prints the answer — and that is the whole design:
when an IDE plugin arrives it becomes the second client of an API that already
exists, with nothing to retrofit and no feature reachable only from a terminal.

Keeping to it takes discipline in one specific place. The temptation is always
to let the CLI "just do this bit directly" because a round trip seems silly for
something small. Every time that wins, the plugin inherits a gap.

Bound to `127.0.0.1` and nothing else, and every request must carry the token
from the discovery file. See `discovery.py` for why that is not ceremony.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import shutil
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .. import acquire, extensions, paths, pecl, pool, settings
from .. import catalog as catalogs
from ..catalog import CatalogError
from ..http import LoopbackHTTPServer
from ..runtimes import Installed, NotInstalled
from ..runtimes import Registry as Runtimes
from ..services import InvalidService
from ..services import Registry as Services
from ..services import Service as ServiceRecord
from ..sites import InvalidSite, Site, document_root
from ..sites import Registry as Sites
from ..stack import Stack, StackError
from ..supervisor import Supervisor
from . import discovery


def _version_of(name: str, directory: Path) -> str:
    """
    Ask an adopted runtime what version it is.

    Guessing from the directory name works until it does not: a PHP unpacked
    into `php-latest` would be recorded as version `latest` and sort below
    everything.
    """
    import re
    import subprocess

    entry = Installed(name=name, version="0", directory=directory, managed=False)

    try:
        executable = entry.executable("php-cli" if name == "php" else name)
        output = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            # A non-zero exit is not fatal here: some builds print their version
            # and exit oddly, and the version is a label, not a gate.
            check=False,
        ).stdout
    except (NotInstalled, OSError, subprocess.SubprocessError):
        return "unknown"

    found = re.search(r"(\d+\.\d+\.\d+)", output)

    return found.group(1) if found else "unknown"


def net_read(url: str) -> str:
    """Indirection for the tests, which have no business reaching the network."""
    from .. import net

    return net.read_text(url)


VERSION = "0.0.1"

#: The API's own version, in the path. Present from the first release so that a
#: plugin pinned to `/v1` keeps working while the CLI moves on.
API = "/v1"


class ControlServer:
    """The daemon: a supervisor, and an HTTP face for it."""

    def __init__(self, supervisor: Supervisor | None = None, token: str | None = None) -> None:
        self.supervisor = supervisor or Supervisor()
        self.runtimes = Runtimes()
        self.sites = Sites()
        self.services_registry = Services()
        self.stack = Stack(supervisor=self.supervisor, runtimes=self.runtimes)
        self.token = token or discovery.new_token()
        self.router_error: str | None = None
        self._http: LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    def restore(self) -> dict:
        """
        Start what was declared before the last shutdown.

        Sites and databases outlive the daemon — they are written down, and a
        person who declared a site expects it served the next time this is
        running, not to have to declare it a second time.

        Called after the daemon is listening and discoverable, and never allowed
        to prevent that. Restoring is exactly where a machine-shaped failure
        surfaces — a port taken since last time, a runtime deleted from under us
        — and a daemon that refuses to start because of one is a daemon nothing
        can reach to fix it, including the command that would move the port.
        """
        try:
            restored = self.stack.reconcile(self.sites.all(), self.services_registry.all())
            self.router_error = None

            return restored
        # Deliberately everything. Narrowing this to the failures thought of
        # today is how an unforeseen one takes the daemon down with it, and the
        # whole point here is that nothing restoring can do that.
        except Exception as error:  # noqa: BLE001
            print(f"restore failed: {type(error).__name__}: {error}", flush=True)

            # Kept so `status` can say why nothing is being served. The log line
            # above is the only other record, and nobody has reason to open it
            # when the daemon is up and answering.
            self.router_error = str(error)

            return {"restored": False, "error": str(error)}

    @property
    def port(self) -> int:
        if self._http is None:
            raise RuntimeError("The server is not listening yet.")

        return self._http.server_address[1]

    def start(self, port: int = 0) -> int:
        """
        Listen, and return the port actually taken.

        Port 0 asks the operating system for a free one. Choosing a fixed port
        would make two installations — a stable one and one being worked on —
        fight over it, and the loser fails at startup for a reason that reads
        like a bug.
        """
        handler = _make_handler(self)
        self._http = LoopbackHTTPServer(("127.0.0.1", port), handler)
        self._http.daemon_threads = True

        self._thread = threading.Thread(
            target=self._http.serve_forever,
            name="portable-control",
            daemon=True,
        )
        self._thread.start()

        return self.port

    def stop(self, timeout: float = 5.0) -> None:
        self._shutdown.set()
        self.supervisor.stop_all(timeout=timeout)

        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        self._thread = None

    def wait(self) -> None:
        """Block until something asks the daemon to shut down."""
        self._shutdown.wait()

    # ------------------------------------------------------------------ routes

    def routes(self) -> dict[tuple[str, str], Callable[[dict], Any]]:
        return {
            ("GET", f"{API}/ping"): self._ping,
            ("GET", f"{API}/status"): self._status,
            ("POST", f"{API}/shutdown"): self._shutdown_route,
            ("GET", f"{API}/runtimes"): self._runtimes,
            ("POST", f"{API}/runtimes/install"): self._install,
            ("GET", f"{API}/runtimes/available"): self._available,
            ("GET", f"{API}/runtimes/updates"): self._updates,
            ("GET", f"{API}/port"): self._port,
            ("POST", f"{API}/port"): self._port_set,
            ("POST", f"{API}/runtimes/remove"): self._runtime_remove,
            ("GET", f"{API}/php/extensions"): self._extensions,
            ("POST", f"{API}/php/extensions/install"): self._extension_install,
            ("POST", f"{API}/php/extensions/enable"): self._extension_enable,
            ("POST", f"{API}/php/extensions/disable"): self._extension_disable,
            ("GET", f"{API}/sites"): self._sites,
            ("POST", f"{API}/sites/add"): self._site_add,
            ("POST", f"{API}/sites/remove"): self._site_remove,
            ("GET", f"{API}/environment"): self._environment,
            ("GET", f"{API}/services"): self._services_list,
            ("POST", f"{API}/services/add"): self._service_add,
            ("POST", f"{API}/services/remove"): self._service_remove,
        }

    def _ping(self, _payload: dict) -> dict:
        """Cheap and unconditional — the one call a client makes to find out if
        anybody is home."""
        return {"ok": True, "version": VERSION, "protocol": discovery.PROTOCOL}

    def _status(self, _payload: dict) -> dict:
        return {
            "version": VERSION,
            # Why nothing is being served, when nothing is. A daemon that is up,
            # lists its sites and answers every question except the one that
            # matters is worse company than one that is down: the failure
            # happened at startup and was recorded in a log nobody has reason to
            # open.
            "router_error": self.router_error,
            "home": str(paths.root()),
            "port": self.stack.port,
            "sites": len(self.sites.all()),
            "services": len(self.services_registry.all()),
            "processes": self.supervisor.status(),
        }

    def _runtimes(self, _payload: dict) -> dict:
        return {
            "runtimes": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "variant": entry.variant,
                    "directory": str(entry.directory),
                    # Whether this tool may replace it. A runtime found on the
                    # machine is used and never modified — deleting somebody
                    # else's PHP would be a surprising thing for this to do.
                    "managed": entry.managed,
                }
                for entry in self.runtimes.all()
            ]
        }

    def _available(self, payload: dict) -> dict:
        """What each publisher currently offers, and what is already here."""
        name = str(payload.get("name") or "").lower()

        try:
            catalog = catalogs.module(name)
        except CatalogError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown-runtime", str(error)) from error

        branch = payload.get("branch") or None

        try:
            offers = catalog.available(branch=branch) if branch else catalog.available()
        except CatalogError as error:
            raise ApiError(HTTPStatus.BAD_GATEWAY, "catalog-unavailable", str(error)) from error

        installed = {entry.version for entry in self.runtimes.all() if entry.name == name}

        return {
            "name": name,
            "versions": [
                {
                    "version": offer.version,
                    "note": offer.note,
                    # So a listing can be read without cross-referencing
                    # `portable runtimes`, which is the whole reason somebody
                    # runs this before installing anything.
                    "installed": offer.version in installed,
                }
                for offer in offers
            ],
        }

    def _port(self, _payload: dict) -> dict:
        return {
            "candidates": list(settings.candidate_ports()),
            "chosen": settings.read().get("port"),
            "serving": self.stack.port,
        }

    def _port_set(self, payload: dict) -> dict:
        """
        Choose the port sites are served on, and move them to it now.

        Applied immediately rather than at the next start. A setting that takes
        effect later is a setting somebody sets, tests, sees no change from, and
        sets again differently.
        """
        raw = payload.get("port")
        wanted = None if raw in (None, "", "auto") else raw
        was = settings.read().get("port")

        try:
            candidates = settings.set_port(None if wanted is None else int(wanted))
        except (TypeError, ValueError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad-port", str(error)) from error

        self.stack.candidate_ports = candidates

        # The router binds its port once, at startup, so it has to be replaced
        # rather than reconfigured — the admin API can change routes on a
        # running Caddy and cannot move it to a different socket.
        self.stack._stop_web()

        try:
            self._reconcile()
        except ApiError:
            # A port that cannot be bound is not stored. Keeping it would leave
            # a machine that fails to serve anything at every start from now on,
            # for a reason recorded only in the daemon's log — the setting
            # having been accepted, reported as set, and then quietly wrong.
            settings.set_port(was)
            self.stack.candidate_ports = settings.candidate_ports()

            with contextlib.suppress(Exception):
                self._reconcile()

            raise

        return {
            "candidates": list(candidates),
            "chosen": settings.read().get("port"),
            "serving": self.stack.port,
        }

    def _updates(self, payload: dict) -> dict:
        """
        Which installed runtimes have a newer release on their own line.

        Within the line only — the newest 8.4.x for an 8.4, not 8.5. Crossing a
        line is an upgrade with consequences the tool cannot judge: a PHP major
        brings deprecations to every site that pinned nothing, and a database
        major will not open the data directory the previous one created. Those
        are installed by name, deliberately, or not at all.

        Adopted runtimes are reported as not updatable rather than omitted. They
        may well be out of date, and saying nothing about them reads as saying
        they are current.
        """
        only = str(payload.get("name") or "").lower() or None
        found = []

        for entry in self.runtimes.all():
            if only and entry.name != only:
                continue

            try:
                catalog = catalogs.module(entry.name)
            except CatalogError:
                continue

            record = {
                "name": entry.name,
                "installed": entry.version,
                "line": catalog.line(entry.version),
                "managed": entry.managed,
                "latest": None,
                "update": False,
            }

            if entry.managed:
                try:
                    # `available` rather than `resolve`, and this is not a
                    # preference. Each catalog's `resolve` takes what its
                    # publisher accepts — a branch for PHP, an exact tag for the
                    # GitHub-published ones — so asking every catalog for "the
                    # newest 8" produced a 404 from Redis and PostgreSQL and
                    # nothing at all for an archived PHP whose branch the index
                    # no longer lists. Listing a line is one question all six
                    # answer.
                    offers = catalog.available(line=catalog.line(entry.version))
                    record["latest"] = offers[0].version if offers else None
                    record["update"] = bool(offers) and _newer(offers[0].version, entry.version)
                except (CatalogError, OSError) as error:
                    # One publisher being unreachable should not hide what the
                    # others said. Reported per runtime rather than failing the
                    # whole answer.
                    record["error"] = str(error)

            found.append(record)

        return {"runtimes": found}

    def _runtime_remove(self, payload: dict) -> dict:
        """
        Delete an installed runtime and reclaim its disk.

        Needed because updating installs alongside rather than replacing — the
        old version stays so that anything pinned to it keeps working, which is
        right, and means something eventually has to take it away.
        """
        name = str(payload.get("name") or "").lower()
        version = str(payload.get("version") or "")
        entry = next(
            (
                candidate
                for candidate in self.runtimes.all()
                if candidate.name == name and candidate.version == version
            ),
            None,
        )

        if entry is None:
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                "no-such-runtime",
                f"{name} {version} is not installed. "
                f"Installed: {', '.join(f'{item.name} {item.version}' for item in self.runtimes.all()) or 'nothing'}.",
            )

        if used_by := self._what_uses(entry):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "in-use",
                f"{name} {version} is used by {', '.join(used_by)}. "
                f"Point those elsewhere first, or remove them.",
            )

        self.runtimes.remove(name, version)

        # An adopted runtime is forgotten, never deleted. It belongs to whoever
        # put it there, and removing it from this tool's list is the whole of
        # what this tool is entitled to do about it.
        removed_files = False

        if entry.managed and entry.directory.is_dir():
            shutil.rmtree(entry.directory, ignore_errors=True)
            removed_files = not entry.directory.exists()

        return {
            "name": name,
            "version": version,
            "directory": str(entry.directory),
            "deleted": removed_files,
            "managed": entry.managed,
        }

    def _what_uses(self, entry: Installed) -> list[str]:
        """
        Sites and databases that would break if this runtime went away.

        A site pinned to `8.4` follows whatever 8.4.x is newest, so it is only
        held to this exact build when nothing newer of that line is installed.
        Checked rather than assumed: removing the only 8.4 out from under a site
        pinned to 8.4 leaves it failing to start with a message about a version
        nobody typed.
        """
        catalog = catalogs.modules().get(entry.name)
        line = catalog.line(entry.version) if catalog else entry.version
        siblings = [
            other
            for other in self.runtimes.of(entry.name)
            if other.version != entry.version and (not catalog or catalog.line(other.version) == line)
        ]

        if siblings:
            return []

        users = []

        if entry.name == "php":
            users += [
                f"site {site.name}"
                for site in self.sites.all()
                if site.php in (None, line, entry.version)
            ]

        users += [
            f"service {service.name}"
            for service in self.services_registry.all()
            if service.kind == entry.name and service.version in (None, line, entry.version)
        ]

        return users

    # -------------------------------------------------------------- extensions

    def _php_for(self, payload: dict) -> tuple[Installed, Path]:
        """The PHP an extension command applies to, and its ini."""
        version = payload.get("php") or None

        try:
            runtime = self.runtimes.get("php", version)
        except NotInstalled as error:
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-runtime", str(error)) from error

        return runtime, pool.ini_for(runtime, paths.root() / "conf")

    def _extensions(self, payload: dict) -> dict:
        runtime, ini = self._php_for(payload)

        return {
            "php": runtime.version,
            "ini": str(ini),
            "extensions": extensions.report(ini, runtime.directory),
        }

    def _extension_install(self, payload: dict) -> dict:
        """
        Fetch an extension the build does not ship, and switch it on.

        Downloading is only half of it. An extension installed and not loaded
        looks exactly like one that failed to install, so this ends with the
        line in the ini and the workers replaced — otherwise the command is
        finished and PHP still has no xdebug.
        """
        name = str(payload.get("name") or "").lower()
        runtime, ini = self._php_for(payload)

        # Adopted runtimes are read and never written to. Dropping a DLL into a
        # PHP that Homebrew, ServBay or a colleague's installer manages would be
        # a surprising thing for this to do, and their next update would remove
        # it without either side knowing why.
        if not runtime.managed:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "not-ours",
                f"PHP {runtime.version} at {runtime.directory} was found on this machine "
                f"rather than installed here, and is never modified. Install a PHP with "
                f"`portable install php` to add extensions to it.",
            )

        if not runtime.variant:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "unknown-variant",
                f"PHP {runtime.version} has no recorded build variant, so there is nothing "
                f"to match an extension against.",
            )

        branch = ".".join(runtime.version.split(".")[:2])

        try:
            build = pecl.resolve(
                name,
                php=branch,
                variant=runtime.variant,
                version=str(payload.get("version") or "latest"),
            )
            installed = pecl.install(build, runtime.directory)
        except CatalogError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-extension", str(error)) from error

        extensions.enable(ini, name, runtime.directory)
        restarted = self.stack.reload_php(runtime.version)

        if restarted:
            self._reconcile()

        return {
            "php": runtime.version,
            "name": name,
            "version": build.version,
            "file": str(installed),
            "enabled": True,
            "restarted": restarted,
            # PECL publishes its Windows builds without digests, and this is a
            # library about to be loaded into every PHP process. Said plainly.
            "verified": build.checksum is not None,
        }

    def _extension_enable(self, payload: dict) -> dict:
        return self._extension_change(payload, enabling=True)

    def _extension_disable(self, payload: dict) -> dict:
        return self._extension_change(payload, enabling=False)

    def _extension_change(self, payload: dict, enabling: bool) -> dict:
        name = str(payload.get("name") or "")
        runtime, ini = self._php_for(payload)

        try:
            changed = (
                extensions.enable(ini, name, runtime.directory)
                if enabling
                else extensions.disable(ini, name)
            )
        except extensions.UnknownExtension as error:
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-extension", str(error)) from error

        # The workers read the ini once, at startup. Without this the command
        # reports success and nothing whatsoever happens, which invites the
        # conclusion that the extension is broken rather than not yet loaded.
        restarted = False

        if changed and self.stack.reload_php(runtime.version):
            self._reconcile()
            restarted = True

        return {
            "php": runtime.version,
            "name": name.lower(),
            "enabled": enabling,
            "changed": changed,
            "restarted": restarted,
            "ini": str(ini),
        }

    def _install(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").lower()
        version = str(payload.get("version") or "latest")
        existing = payload.get("from")

        if existing:
            return self._adopt(name, Path(str(existing)).expanduser().resolve(), version)

        try:
            catalog = catalogs.module(name)
        except CatalogError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown-runtime", str(error)) from error

        try:
            build = catalog.resolve(version)

            if build.checksum is None and hasattr(catalog, "checksum_url"):
                # Caddy and Node both publish digests in a separate file, so
                # resolution leaves the field empty rather than costing every
                # caller a second request for a value most never use. Fetched
                # here, where the archive is about to be verified.
                #
                # Asked of the module rather than of the name: a publisher that
                # starts or stops doing this should be a change in its own
                # catalog, not a new branch in the daemon.
                found = catalog.checksum_for(
                    build.filename, net_read(catalog.checksum_url(build.version))
                )

                if found is not None:
                    digest, algorithm = found
                    build = replace(build, checksum=digest, algorithm=algorithm)

            acquired = acquire.install(build)
        except CatalogError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-version", str(error)) from error
        except acquire.VerificationError as error:
            raise ApiError(HTTPStatus.BAD_GATEWAY, "verification-failed", str(error)) from error

        entry = Installed(
            name=name,
            version=acquired.build.version,
            directory=acquired.directory,
            managed=True,
            variant=acquired.build.variant,
        )
        self.runtimes.add(entry)

        return {
            "name": entry.name,
            "version": entry.version,
            "directory": str(entry.directory),
            "verified": acquired.verified,
        }

    def _adopt(self, name: str, directory: Path, version: str) -> dict:
        """
        Register something already on this machine.

        Not a fallback for a failed download. Statically built PHP cannot load
        extensions at runtime, so anyone who needs one the prebuilt binaries
        lack has exactly this way of staying unblocked — and a machine that
        already has a working PHP should not be made to fetch a second one.

        Adopted, never modified: this tool will not update it and will not
        delete it. Removing a PHP that Homebrew or another tool installed would
        be a surprising thing for a program called `portable` to do.
        """
        if not directory.is_dir():
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "no-such-directory",
                f"{directory} is not a directory.",
            )

        entry = Installed(
            name=name,
            version=version if version != "latest" else _version_of(name, directory),
            directory=directory,
            managed=False,
        )

        try:
            executable = entry.executable(name)
        except NotInstalled as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "no-executable", str(error)) from error

        self.runtimes.add(entry)

        return {
            "name": entry.name,
            "version": entry.version,
            "directory": str(entry.directory),
            "executable": str(executable),
            "managed": False,
            "verified": False,
        }

    def _sites(self, _payload: dict) -> dict:
        return {
            "port": self.stack.port,
            "sites": [
                {
                    "name": site.name,
                    "hostname": site.hostname,
                    "url": self._url_for(site.hostname),
                    "root": str(site.root),
                    "php": site.php,
                }
                for site in self.sites.all()
            ],
        }

    def _site_add(self, payload: dict) -> dict:
        name = str(payload.get("name") or "")
        root = Path(str(payload.get("root") or "")).expanduser()

        detected = False

        # Only when the caller has not said otherwise. `--exact` exists because
        # somebody who typed a path meant it, and a tool that quietly serves a
        # different directory than the one it was handed is worse than one that
        # serves the wrong one obediently.
        if root.is_dir() and not payload.get("exact"):
            root, detected = document_root(root)

        site = Site(name=name, root=root, php=payload.get("php") or None)
        previous = {existing.name for existing in self.sites.all()}

        try:
            self.sites.add(site)
        except InvalidSite as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid-site", str(error)) from error

        try:
            self._reconcile()
        except ApiError:
            # The declaration is withdrawn again — the same rule `service add`
            # already followed, and for the same reason: a failed `add` that
            # leaves the site listed invites the conclusion that it exists and
            # is merely stopped, and being confused a second time when starting
            # it does nothing.
            #
            # Only when the site is new. Re-adding an existing one that fails
            # must not delete the working declaration it was replacing.
            if site.name not in previous:
                self.sites.remove(site.name)

            raise

        return {
            "name": site.name,
            "hostname": site.hostname,
            "url": self._url_for(site.hostname),
            "root": str(site.root),
            # Reported, always. Serving a directory other than the one that was
            # named is right far more often than not, and still has to be said
            # out loud — otherwise the first surprise is somebody wondering why
            # their edits to the wrong index.php change nothing.
            "detected": detected,
        }

    def _site_remove(self, payload: dict) -> dict:
        name = str(payload.get("name") or "")

        if not self.sites.remove(name):
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-site", f"No site called {name!r}.")

        self._reconcile()

        return {"removed": name}

    def _environment(self, _payload: dict) -> dict:
        """
        The directories a shell would need on PATH to reach what is installed.

        Node is why this exists. It is not a service and not a router — it is a
        toolchain, used from a terminal, and this tool does not touch the system
        PATH. So instead of changing the machine, it answers what the machine
        would need, and `portable run` and `portable env` do the rest locally.

        The knowledge stays here; only the applying is done by the client. A
        plugin offering a terminal gets the same answer from the same place.
        """
        entries = []

        for entry in self.runtimes.all():
            directory = entry.directory / "bin"

            if not directory.is_dir():
                directory = entry.directory

            entries.append(
                {
                    "name": entry.name,
                    "version": entry.version,
                    "path": str(directory),
                }
            )

        return {
            "path": [entry["path"] for entry in entries],
            "runtimes": entries,
            "vars": {
                # So a script can tell it is running inside one of these, and
                # find the rest of the installation without being told.
                "PORTABLE_HOME": str(paths.root()),
            },
        }

    def _services_list(self, _payload: dict) -> dict:
        declared = {service.name: service for service in self.services_registry.all()}
        running = {entry["name"]: entry for entry in self.stack.service_report()}

        return {
            "services": [
                {
                    "name": name,
                    "kind": service.kind,
                    "version": service.version,
                    "running": name in running,
                    "port": running.get(name, {}).get("port", service.port),
                    "user": service.superuser,
                    "data": str(service.data),
                }
                for name, service in sorted(declared.items())
            ]
        }

    def _service_add(self, payload: dict) -> dict:
        kind = str(payload.get("kind") or "")
        name = str(payload.get("name") or kind)

        service = ServiceRecord(
            name=name,
            kind=kind,
            version=payload.get("version") or None,
            port=payload.get("port") or None,
        )

        try:
            self.services_registry.add(service)
        except InvalidService as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid-service", str(error)) from error

        try:
            self._reconcile()
        except ApiError:
            # The declaration is withdrawn again. Otherwise a failed `add` — a
            # missing runtime, an initialisation that would not complete —
            # leaves a service that `list` reports as merely "stopped", which
            # invites starting it and being confused twice.
            self.services_registry.remove(service.name)

            raise

        running = {entry["name"]: entry for entry in self.stack.service_report()}
        port = running.get(name, {}).get("port")

        # Remembered so a connection string does not change on the next restart.
        if port and service.port != port:
            self.services_registry.add(replace(service, port=port))

        return {
            "name": name,
            "kind": kind,
            "port": port,
            "user": service.superuser,
            "data": str(service.data),
        }

    def _service_remove(self, payload: dict) -> dict:
        name = str(payload.get("name") or "")
        removed = self.services_registry.remove(name)

        if removed is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-service", f"No service called {name!r}.")

        self._reconcile()

        return {
            "removed": name,
            # Said explicitly, every time. Somebody will eventually run this
            # expecting a clean slate, and the difference matters.
            "data_kept": str(removed.data),
        }

    def _reconcile(self) -> dict:
        """
        Make the processes match the sites, turning refusals into API errors.

        Every route that changes something ends here rather than starting and
        stopping things itself: one path means one way for it to be half-done.
        """
        try:
            return self.stack.reconcile(self.sites.all(), self.services_registry.all())
        except (StackError, NotInstalled) as error:
            raise ApiError(HTTPStatus.CONFLICT, "cannot-serve", str(error)) from error

    def _url_for(self, hostname: str) -> str:
        port = self.stack.port

        # Port 80 is left off: `demo.localhost` is the address, and printing
        # `demo.localhost:80` invites someone to think the port matters.
        return f"http://{hostname}" if port in (None, 80) else f"http://{hostname}:{port}"

    def _shutdown_route(self, _payload: dict) -> dict:
        # Answered before anything is torn down, so the caller gets a reply
        # rather than a dropped connection it has to interpret.
        threading.Thread(target=self._deferred_stop, daemon=True).start()

        return {"stopping": True}

    def _deferred_stop(self) -> None:
        self._shutdown.set()


class ApiError(Exception):
    """An error with a key a client can act on, not just a sentence."""

    def __init__(self, status: int, key: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.key = key
        self.message = message


def _newer(candidate: str, than: str) -> bool:
    def key(version: str) -> tuple[int, ...]:
        return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))

    return key(candidate) > key(than)


def _make_handler(server: ControlServer):
    routes = server.routes()

    class Handler(BaseHTTPRequestHandler):
        # Silences the default line-per-request on stderr. The daemon's output
        # goes to a log file, and an access log is not what belongs in it.
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def _handle(self, method: str) -> None:
            try:
                self._authorise()
                handler = routes.get((method, self.path.split("?")[0]))

                if handler is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown-route", f"No {method} {self.path}.")

                self._reply(HTTPStatus.OK, handler(self._payload()))
            except ApiError as error:
                self._reply(error.status, {"errorKey": error.key, "message": error.message})
            except Exception as error:  # noqa: BLE001
                # Reported rather than swallowed. A daemon that answers 500 and
                # writes nothing anywhere is a thing this author has debugged
                # before and does not intend to again.
                self._reply(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"errorKey": "internal", "message": f"{type(error).__name__}: {error}"},
                )

        def _authorise(self) -> None:
            offered = self.headers.get("X-Portable-Token", "")

            # Constant-time: a plain `==` leaks the token's prefix through how
            # long the comparison takes, and this API starts processes.
            if not secrets.compare_digest(offered, server.token):
                raise ApiError(
                    HTTPStatus.UNAUTHORIZED,
                    "bad-token",
                    "The request carried no valid token. It is in the daemon file — "
                    "see `portable status`.",
                )

        def _payload(self) -> dict:
            """
            The request's parameters, from the query string or the body.

            A GET carrying a body is a thing servers and proxies are entitled to
            drop, so a read that takes an argument — "what versions of PHP are
            there" — has to take it in the URL. Merged into the same dictionary
            the handlers already read, so no handler needs to know or care which
            way it arrived.
            """
            query = urllib.parse.urlparse(self.path).query
            parameters = {
                key: values[-1]
                for key, values in urllib.parse.parse_qs(query).items()
                if values
            }

            length = int(self.headers.get("Content-Length") or 0)

            if not length:
                return parameters

            try:
                return {**parameters, **json.loads(self.rfile.read(length).decode("utf-8"))}
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "malformed-body", f"The body is not JSON: {error}"
                ) from error

        def _reply(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode("utf-8")

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler
