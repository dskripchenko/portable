"""
An HTTP server that does not ask the network who it is.

`http.server.HTTPServer.server_bind()` calls `socket.getfqdn()` on the address
it has just bound, to fill in `server_name`. For `127.0.0.1` that is a reverse
DNS query, and where the resolver is slow or filtered it takes tens of seconds
or never returns at all. The process starts, binds nothing further, and hangs —
with no error anywhere, because nothing has failed.

macOS CI runners do it every time. A managed corporate network with a filtered
resolver behaves the same way, and those are the machines this tool exists for.

This lives in its own module because the mistake was made twice: once in the
daemon's control API, and again in the test double for Caddy. A shared, named
thing is harder to walk past than a comment in one file.

`server_name` only feeds CGI environment variables neither server produces.
"""

from __future__ import annotations

import socketserver
from http.server import ThreadingHTTPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` without the reverse lookup at bind time."""

    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]
