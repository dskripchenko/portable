"""
Networks that drop connections, which is most of them.

This came from a real Windows session: `install php 8.3` failing three times
with `record layer failure` and `WinError 10054` while `install php 8.4` went
through in between, on the same machine within the same minute. The tool made
exactly one attempt at everything and reported the socket error verbatim.

A TLS handshake reset mid-record is what traffic inspection does to traffic it
dislikes, and it is intermittent by nature — so the failure is not "the host is
down", it is "ask again".
"""

from __future__ import annotations

import ssl
import urllib.error

import pytest

from portable import acquire, net
from portable.catalog import Build


class Response:
    """Enough of an HTTP response for the downloader."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.body[self._read : self._read + (size if size > 0 else len(self.body))]
        self._read += len(chunk)

        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestRetrying:
    def test_a_reset_handshake_is_tried_again(self, monkeypatch):
        # `SSLError` is not a `URLError`, so it escaped every handler there was
        # and arrived as `[SSL] record layer failure (_ssl.c:2660)`.
        attempts = []
        monkeypatch.setattr(net, "BACKOFF", 0)

        def flaky(url, timeout, offset=0):
            attempts.append(1)

            if len(attempts) < 3:
                raise ssl.SSLError("[SSL] record layer failure")

            return Response(b"ok")

        monkeypatch.setattr(net, "_open_once", flaky)

        assert net.open_url("https://example.invalid/x").read() == b"ok"
        assert len(attempts) == 3

    def test_a_missing_file_is_not_tried_again(self, monkeypatch):
        """
        A 404 is an answer, not a failure to get one.

        `HTTPError` subclasses `URLError`, so retrying transient `URLError`s
        nearly meant retrying every 404 five times across fifteen seconds, to
        tell somebody what they already knew after the first.
        """
        attempts = []
        monkeypatch.setattr(net, "BACKOFF", 0)

        def missing(url, timeout, offset=0):
            attempts.append(1)

            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(net, "_open_once", missing)

        with pytest.raises(urllib.error.HTTPError):
            net.open_url("https://example.invalid/x")

        assert len(attempts) == 1

    def test_a_server_error_is(self, monkeypatch):
        attempts = []
        monkeypatch.setattr(net, "BACKOFF", 0)

        def unwell(url, timeout, offset=0):
            attempts.append(1)

            if len(attempts) < 2:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

            return Response(b"ok")

        monkeypatch.setattr(net, "_open_once", unwell)

        assert net.open_url("https://example.invalid/x").read() == b"ok"
        assert len(attempts) == 2

    def test_giving_up_lists_the_attempts_and_names_the_pattern(self, monkeypatch):
        # Five identical resets and five different errors mean different things.
        monkeypatch.setattr(net, "BACKOFF", 0)
        monkeypatch.setattr(
            net,
            "_open_once",
            lambda *a, **k: (_ for _ in ()).throw(ConnectionResetError("WinError 10054")),
        )

        with pytest.raises(net.Unreachable) as excinfo:
            net.open_url("https://example.invalid/x")

        message = str(excinfo.value)

        assert "10054" in message
        assert "HTTPS_PROXY" in message, "it should mention the one setting that can help"


class TestResuming:
    def build(self) -> Build:
        return Build(name="php", version="8.3.0", url="https://example.invalid/php.zip",
                     filename="php.zip")

    def test_a_transfer_that_breaks_continues_where_it_stopped(self, monkeypatch, tmp_path):
        """
        Reconnecting alone is not enough.

        These archives run to ninety megabytes. A connection that keeps dropping
        will drop partway through, so starting again from nothing means never
        finishing however many attempts are allowed. Asking for the rest turns a
        bad network into a slow one.
        """
        whole = bytes(range(256)) * 400
        offsets = []

        def flaky(url, timeout=300, offset=0):
            offsets.append(offset)

            if offset == 0:
                # Half of it arrives and the connection closes — cleanly, as far
                # as the socket is concerned, which is the case that used to be
                # accepted as a finished download.
                return Response(
                    whole[: len(whole) // 2], headers={"Content-Length": str(len(whole))}
                )

            return Response(
                whole[offset:],
                status=206,
                headers={"Content-Length": str(len(whole) - offset)},
            )

        monkeypatch.setattr(acquire.net, "open_url", flaky)
        partial = tmp_path / "php.zip.part"

        acquire._fetch(self.build(), partial)

        assert partial.read_bytes() == whole
        assert offsets == [0, len(whole) // 2], "it did not ask to continue"

    def test_a_server_that_ignores_the_range_starts_over(self, monkeypatch, tmp_path):
        # Answering 200 with the whole file means what is on disk has to go:
        # appending would put the first bytes in twice.
        whole = b"abcdefghij" * 100

        def ignores_range(url, timeout=300, offset=0):
            return Response(whole, status=200)

        monkeypatch.setattr(acquire.net, "open_url", ignores_range)
        partial = tmp_path / "php.zip.part"
        partial.write_bytes(b"xxxxx")

        acquire._fetch(self.build(), partial)

        assert partial.read_bytes() == whole

    def test_it_stops_when_an_attempt_moves_nothing(self, monkeypatch, tmp_path):
        # Resuming a transfer that makes no progress is a loop, not a recovery.
        attempts = []

        def stalled(url, timeout=300, offset=0):
            attempts.append(offset)

            raise ConnectionResetError("10054")

        monkeypatch.setattr(acquire.net, "open_url", stalled)
        partial = tmp_path / "php.zip.part"
        partial.write_bytes(b"already here")

        with pytest.raises(acquire.TransferFailed):
            acquire._fetch(self.build(), partial)

        assert len(attempts) == 1

    def test_what_was_kept_is_reported(self, monkeypatch, tmp_path):
        # So that the next attempt being fast is not a surprise, and so nobody
        # deletes the directory thinking it is rubbish.
        partial = tmp_path / "php.zip.part"
        partial.write_bytes(b"x" * (3 * 1048576))

        assert "3 MB" in acquire._kept(partial)


class TestATruncatedBody:
    """
    A transfer can stop early and look like it finished.

    The socket closes, `read` returns nothing, and the loop ends contentedly on
    a file missing its last thirty megabytes. Nothing downstream would notice
    for PostgreSQL, Redis or an archived PHP — none of which the publisher gives
    a checksum for, so the short archive would simply be unpacked.
    """

    def test_it_is_not_mistaken_for_a_finished_one(self, monkeypatch, tmp_path):
        whole = b"z" * 5000
        seen = []

        def half(url, timeout=300, offset=0):
            seen.append(offset)

            return Response(whole[offset : offset + 2000],
                            status=206 if offset else 200,
                            headers={"Content-Length": str(len(whole) - offset)})

        monkeypatch.setattr(acquire.net, "open_url", half)
        partial = tmp_path / "x.part"

        acquire._fetch(Build(name="php", version="1", url="https://x.invalid/x",
                             filename="x"), partial)

        assert partial.read_bytes() == whole
        assert len(seen) == 3, "it accepted a short body instead of asking for the rest"


class TestSayingItIsWaiting:
    """
    Silence is what makes a working command look hung.

    An unreachable host costs five attempts, each waiting out a thirty-second
    connect timeout, and an index fetched twice doubles it — reported as five
    minutes of a dashboard doing nothing while it was doing exactly what it had
    been told.
    """

    def test_each_wait_is_announced(self, monkeypatch, capsys):
        monkeypatch.setattr(net, "BACKOFF", 0)
        monkeypatch.setattr(
            net,
            "_open_once",
            lambda *a, **k: (_ for _ in ()).throw(ConnectionResetError("10054")),
        )

        with pytest.raises(net.Unreachable):
            net.open_url("https://downloads.mariadb.org/rest-api/mariadb/")

        said = capsys.readouterr().err

        assert said.count("attempt") == net.ATTEMPTS - 1, "the last one is the failure itself"
        assert "downloads.mariadb.org" in said, "it should name the host being waited on"
