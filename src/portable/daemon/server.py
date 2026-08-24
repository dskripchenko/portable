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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .. import paths
from ..supervisor import Supervisor
from . import discovery

VERSION = "0.0.1"

#: The API's own version, in the path. Present from the first release so that a
#: plugin pinned to `/v1` keeps working while the CLI moves on.
API = "/v1"


class ControlServer:
    """The daemon: a supervisor, and an HTTP face for it."""

    def __init__(self, supervisor: Supervisor | None = None, token: str | None = None) -> None:
        self.supervisor = supervisor or Supervisor()
        self.token = token or discovery.new_token()
        self._http: ThreadingHTTPServer | None = None
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
        self._http = ThreadingHTTPServer(("127.0.0.1", port), handler)
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
        }

    def _ping(self, _payload: dict) -> dict:
        """Cheap and unconditional — the one call a client makes to find out if
        anybody is home."""
        return {"ok": True, "version": VERSION, "protocol": discovery.PROTOCOL}

    def _status(self, _payload: dict) -> dict:
        return {
            "version": VERSION,
            "home": str(paths.root()),
            "processes": self.supervisor.status(),
        }

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
