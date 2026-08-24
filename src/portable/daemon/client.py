"""
Talking to the daemon.

The CLI's only means of doing anything, and the shape an IDE plugin will
reimplement in Kotlin. Kept deliberately thin: it finds the daemon, adds the
token, and turns a non-2xx into an exception carrying the key the server sent.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path

from .discovery import Endpoint, read


class NotRunning(RuntimeError):
    """No daemon to talk to."""


class CallFailed(RuntimeError):
    """The daemon answered, and the answer was a refusal."""

    def __init__(self, status: int, key: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.key = key
        self.message = message


class Client:
    def __init__(self, endpoint: Endpoint | None = None, path: Path | None = None) -> None:
        found = endpoint or read(path)

        if found is None:
            raise NotRunning(
                "No daemon is running. Start one with `portable up`."
            )

        self.endpoint = found

    def call(self, method: str, route: str, payload: dict | None = None, timeout: float = 30) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.endpoint.url}{route}",
            data=body,
            method=method,
            headers={
                "X-Portable-Token": self.endpoint.token,
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = _decode(error)

            raise CallFailed(
                error.code,
                detail.get("errorKey", "unknown"),
                detail.get("message", str(error)),
            ) from error
        except urllib.error.URLError as error:
            # The discovery file said a daemon was there and nothing answered.
            raise NotRunning(f"The daemon did not answer on {self.endpoint.url}: {error.reason}") from error
        except (http.client.HTTPException, ConnectionError, TimeoutError) as error:
            # An answer that started and stopped — a truncated body, a reset
            # connection. `IncompleteRead` is an `HTTPException` and not a
            # `URLError`, so it used to escape both this and the loop in
            # `portable up` that retries while the daemon is starting, and
            # arrived at the person as a traceback about bytes.
            #
            # Treated as "not answering", which is what it is: the caller that
            # was polling keeps polling, and the caller that was not gets a
            # sentence instead of a stack.
            raise NotRunning(
                f"The daemon answered on {self.endpoint.url} and the answer broke off: "
                f"{type(error).__name__}: {error}"
            ) from error

    def ping(self) -> dict:
        return self.call("GET", "/v1/ping")

    def status(self) -> dict:
        return self.call("GET", "/v1/status")

    def shutdown(self) -> dict:
        return self.call("POST", "/v1/shutdown", {})


def _decode(error: urllib.error.HTTPError) -> dict:
    try:
        return json.loads(error.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
