"""
The daemon's entry point: `python -m portable.daemon`.

Started detached by `portable up`. It writes down where it landed, waits until
something asks it to stop, and clears the note on the way out — so a client that
finds a discovery file can trust that something is listening behind it.
"""

from __future__ import annotations

import json
import os
import signal
import sys

from .. import paths
from . import discovery
from .server import ControlServer


def main() -> int:
    # Before anything that can fail, and flushed immediately. An empty log is
    # otherwise indistinguishable between "never ran", "died during import" and
    # "started and vanished" — three different problems with three different
    # fixes.
    print(f"portable daemon starting: pid={os.getpid()} python={sys.executable}", flush=True)

    paths.ensure_layout()

    server = ControlServer()
    port = server.start(port=0)

    endpoint = discovery.Endpoint(port=port, token=server.token, pid=os.getpid())
    discovery.write(endpoint)

    # SIGTERM is how anything other than the control API asks — a `kill`, or the
    # system going down. Handled rather than ignored so the supervised
    # processes are stopped too: leaving an orphaned `php-cgi.exe` on a port
    # makes the next start fail for reasons that point nowhere near the cause.
    def handle(_signum, _frame):
        server._shutdown.set()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handle)
            except (ValueError, OSError):
                # Not the main thread, or not supported here. The control API
                # still works, which is the path that matters.
                pass

    print(f"listening on 127.0.0.1:{port}", flush=True)

    # After the discovery file, deliberately. Restoring touches ports and starts
    # processes, which is the part most likely to fail on a machine that has
    # changed since last time — and if it does, the daemon must already be
    # reachable so that it can be told to do something else.
    restored = server.restore()
    print(f"restored: {json.dumps(restored, default=str)}", flush=True)

    try:
        server.wait()
    except BaseException as error:
        print(f"daemon failed: {type(error).__name__}: {error}", flush=True)
        raise
    finally:
        server.stop()
        discovery.clear()
        print("daemon stopped", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
