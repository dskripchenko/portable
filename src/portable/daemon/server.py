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

import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .. import acquire, paths
from ..catalog import CatalogError
from ..catalog import caddy as caddy_catalog
from ..catalog import php as php_catalog
from ..http import LoopbackHTTPServer
from ..runtimes import Installed, NotInstalled
from ..runtimes import Registry as Runtimes
from ..services import InvalidService
from ..services import Registry as Services
from ..services import Service as ServiceRecord
from ..sites import InvalidSite, Site
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
        self._http: LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()

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
            ("GET", f"{API}/sites"): self._sites,
            ("POST", f"{API}/sites/add"): self._site_add,
            ("POST", f"{API}/sites/remove"): self._site_remove,
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

    def _install(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").lower()
        version = str(payload.get("version") or "latest")
        existing = payload.get("from")

        if existing:
            return self._adopt(name, Path(str(existing)).expanduser().resolve(), version)

        if name not in ("php", "caddy"):
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "unknown-runtime",
                f"Nothing is known about {name!r}. Installable: php, caddy.",
            )

        try:
            build = (php_catalog if name == "php" else caddy_catalog).resolve(version)

            if name == "caddy" and build.checksum is None:
                # Caddy lists its digests in a separate file, so resolution
                # leaves the field empty rather than costing every caller a
                # second request. Fetched here, where the file is about to be
                # verified.
                found = caddy_catalog.checksum_for(
                    build.filename, net_read(caddy_catalog.checksum_url(build.version))
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

        site = Site(name=name, root=root, php=payload.get("php") or None)

        try:
            self.sites.add(site)
        except InvalidSite as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid-site", str(error)) from error

        self._reconcile()

        return {
            "name": site.name,
            "hostname": site.hostname,
            "url": self._url_for(site.hostname),
            "root": str(site.root),
        }

    def _site_remove(self, payload: dict) -> dict:
        name = str(payload.get("name") or "")

        if not self.sites.remove(name):
            raise ApiError(HTTPStatus.NOT_FOUND, "no-such-site", f"No site called {name!r}.")

        self._reconcile()

        return {"removed": name}

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
            length = int(self.headers.get("Content-Length") or 0)

            if not length:
                return {}

            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
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
