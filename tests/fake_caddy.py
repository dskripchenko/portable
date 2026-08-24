"""
A stand-in for Caddy, obeying the same configuration document.

Not a mock. It reads the JSON this tool generates, listens where that document
says to listen, answers the unmatched-host route the port probe looks for, and
accepts `POST /load` on its admin address. That makes the whole of
`Stack.reconcile()` testable — the probe, the fallback to another port, the live
reconfiguration — without downloading a 40 MB binary on every run.

What it deliberately does not do is serve anything. Whether PHP answers is not a
question this can address, and pretending otherwise would make a green suite
that proves less than it appears to.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE: dict = {"document": {}}


def _sites(document: dict) -> list[str]:
    server = document.get("apps", {}).get("http", {}).get("servers", {}).get("portable", {})
    hosts: list[str] = []

    for route in server.get("routes", []):
        for match in route.get("match", []):
            hosts.extend(match.get("host", []))

    return hosts


def _listen_port(document: dict) -> int:
    server = document.get("apps", {}).get("http", {}).get("servers", {}).get("portable", {})
    listen = server.get("listen", [":80"])[0]

    return int(listen.lstrip(":"))


def _admin_address(document: dict) -> tuple[str, int]:
    host, _, port = document.get("admin", {}).get("listen", "127.0.0.1:2019").partition(":")

    return host, int(port)


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:
        host = (self.headers.get("Host") or "").split(":")[0]
        known = _sites(STATE["document"])

        if host in known:
            self._reply(200, f"site {host}\n")
        else:
            # The exact phrase the port probe looks for. Its wording is part of
            # the contract between the router and the thing that starts it.
            self._reply(
                404,
                "portable: no site is configured for this hostname.\n\n"
                f"Configured: {', '.join(sorted(known)) or 'nothing yet'}\n",
            )

    def _reply(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class Admin(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        STATE["document"] = json.loads(self.rfile.read(length).decode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        encoded = json.dumps(STATE["document"]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    # Announced at every step, because a silent process is a process nobody can
    # diagnose. A whole afternoon went into chasing an empty log that was empty
    # because this said nothing even when it worked.
    print(f"fake caddy: argv={sys.argv[1:]}", flush=True)

    # Started the way Caddy is: `run --config <file> --adapter ""`.
    config_file = sys.argv[sys.argv.index("--config") + 1]
    with open(config_file, encoding="utf-8") as handle:
        STATE["document"] = json.load(handle)

    admin_host, admin_port = _admin_address(STATE["document"])
    site_port = _listen_port(STATE["document"])
    print(f"fake caddy: admin={admin_host}:{admin_port} site=127.0.0.1:{site_port}", flush=True)

    admin = ThreadingHTTPServer((admin_host, admin_port), Admin)
    admin.daemon_threads = True
    threading.Thread(target=admin.serve_forever, daemon=True).start()

    # Binding the site port is what can fail — a privileged port, or one already
    # held. Failing here rather than pretending is the whole point: it is what
    # makes the fallback in `Stack` reachable in a test.
    site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
    site.daemon_threads = True
    print(f"fake caddy: listening on {site_port}", flush=True)
    site.serve_forever()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as error:  # noqa: BLE001
        print(f"fake caddy failed: {type(error).__name__}: {error}", flush=True)
        raise
