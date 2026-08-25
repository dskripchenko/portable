"""
The control API, exercised over real HTTP.

Not by calling the route functions directly — that would skip authorisation,
routing and encoding, which is most of what there is to get wrong here. The
server is started, a socket is opened, and the answers are read back.

Authorisation gets the most attention on purpose. This API starts processes and
listens where every other process running as this user can reach it, so "the
token is checked" is not a detail of the design, it is the design.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from portable import paths, spawn
from portable.daemon import discovery
from portable.daemon.client import CallFailed, Client, NotRunning
from portable.daemon.server import ControlServer
from portable.supervisor import Spec, Supervisor


@pytest.fixture
def server():
    instance = ControlServer()
    instance.start(port=0)

    yield instance

    instance.stop(timeout=5)


@pytest.fixture
def endpoint(server) -> discovery.Endpoint:
    import os

    return discovery.Endpoint(port=server.port, token=server.token, pid=os.getpid())


def raw(server, route: str, token: str | None, method: str = "GET") -> tuple[int, dict]:
    """A request built by hand, so a test can send a wrong token on purpose."""
    headers = {"X-Portable-Token": token} if token is not None else {}
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{route}", method=method, headers=headers
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


class TestAuthorisation:
    def test_a_request_without_a_token_is_refused(self, server):
        status, body = raw(server, "/v1/ping", token=None)

        assert status == 401
        assert body["errorKey"] == "bad-token"

    def test_a_wrong_token_is_refused(self, server):
        status, body = raw(server, "/v1/ping", token="not-the-token")

        assert status == 401
        assert body["errorKey"] == "bad-token"

    def test_a_token_that_is_a_prefix_of_the_real_one_is_refused(self, server):
        # The check is `compare_digest`, not `==` or `startswith`. This is the
        # test that would fail if someone ever "simplified" it.
        status, _ = raw(server, "/v1/ping", token=server.token[:-1])

        assert status == 401

    def test_the_right_token_gets_through(self, server):
        status, body = raw(server, "/v1/ping", token=server.token)

        assert status == 200
        assert body["ok"] is True

    def test_authorisation_is_checked_before_routing(self, server):
        # Otherwise an unauthenticated caller learns which routes exist by
        # reading which ones answer 404 and which answer 401.
        status, body = raw(server, "/v1/nothing-here", token=None)

        assert status == 401
        assert body["errorKey"] == "bad-token"


class TestListening:
    def test_it_binds_to_the_loopback_only(self, server):
        # Binding to 0.0.0.0 would put an API that starts processes on the
        # network. There is no configuration option for this, and should not be.
        assert server._http.server_address[0] == "127.0.0.1"

    def test_it_takes_an_ephemeral_port(self, server):
        # A fixed port makes two installations fight, and the loser fails at
        # startup for a reason that reads like a bug.
        assert server.port > 0


class TestRoutes:
    def test_status_reports_what_is_supervised(self):
        supervisor = Supervisor()
        supervisor.add(Spec(name="thing", argv=["true"]))
        server = ControlServer(supervisor=supervisor)
        server.start(port=0)

        try:
            _, body = raw(server, "/v1/status", token=server.token)

            assert [process["name"] for process in body["processes"]] == ["thing"]
            assert body["home"]
        finally:
            server.stop(timeout=5)

    def test_an_unknown_route_says_so_with_a_key(self, server):
        status, body = raw(server, "/v1/does-not-exist", token=server.token)

        assert status == 404
        assert body["errorKey"] == "unknown-route"


class TestClient:
    def test_it_talks_to_a_running_daemon(self, server, endpoint):
        client = Client(endpoint=endpoint)

        assert client.ping()["ok"] is True
        assert "processes" in client.status()

    def test_a_refusal_arrives_with_its_key_intact(self, server, endpoint):
        # The key is what a plugin branches on. A message alone would force it
        # to match on English text.
        client = Client(endpoint=endpoint)

        with pytest.raises(CallFailed) as excinfo:
            client.call("GET", "/v1/nope")

        assert excinfo.value.key == "unknown-route"
        assert excinfo.value.status == 404

    def test_no_daemon_is_a_distinct_failure_from_a_refusal(self, tmp_path):
        # "Nothing is running" and "it said no" want different answers from the
        # caller, so they are different exceptions.
        with pytest.raises(NotRunning):
            Client(path=tmp_path / "absent.json")


class TestDiscovery:
    def test_the_file_round_trips(self, tmp_path):
        import os

        path = tmp_path / "daemon.json"
        written = discovery.Endpoint(port=12345, token="t", pid=os.getpid())
        discovery.write(written, path)

        assert discovery.read(path) == written

    def test_a_file_naming_a_dead_process_is_treated_as_absent(self, tmp_path):
        # A daemon killed rather than stopped leaves a note behind. Trusting it
        # sends every later client at a port that now belongs to something else.
        path = tmp_path / "daemon.json"
        discovery.write(discovery.Endpoint(port=1, token="t", pid=0x7FFFFFFF), path)

        assert discovery.read(path) is None
        assert not path.exists(), "the stale file should be cleared away"

    def test_a_corrupt_file_is_treated_as_absent(self, tmp_path):
        path = tmp_path / "daemon.json"
        path.write_text("{ truncated by a crash", encoding="utf-8")

        assert discovery.read(path) is None

    @pytest.mark.skipif(discovery.os.name == "nt", reason="POSIX permissions")
    def test_the_token_file_is_not_readable_by_others(self, tmp_path):
        import os
        import stat

        path = tmp_path / "daemon.json"
        discovery.write(discovery.Endpoint(port=1, token="secret", pid=os.getpid()), path)
        mode = stat.S_IMODE(path.stat().st_mode)

        assert not mode & stat.S_IRGRP
        assert not mode & stat.S_IROTH

    def test_tokens_are_not_predictable(self):
        assert len({discovery.new_token() for _ in range(50)}) == 50
        assert len(discovery.new_token()) >= 32


class TestBindingDoesNotDependOnDns:
    def test_starting_does_not_wait_on_a_reverse_lookup(self):
        """
        `HTTPServer.server_bind` calls `socket.getfqdn()` on the bound address —
        a reverse DNS query for 127.0.0.1. Where the resolver is slow or
        filtered it takes tens of seconds or hangs outright, and the daemon
        never reaches the point of listening.

        macOS CI runners hang on it every time. A corporate machine with a
        filtered resolver — the kind this tool is written for — behaves
        identically.
        """
        import time

        server = ControlServer()
        started = time.monotonic()

        try:
            server.start(port=0)
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"binding took {elapsed:.1f}s — something is resolving names"
            assert server._http.server_name == "localhost", "the lookup happened after all"
        finally:
            server.stop(timeout=5)


class TestRestoringAfterARestart:
    """
    Sites and databases outlive the daemon.

    They are written down, so a person who declared a site expects it served
    the next time this is running. Without this, `portable down` followed by
    `portable up` leaves every site declared and none of them reachable — and
    nothing says so, which is the worst version of it: the declaration is still
    listed, the daemon is up, and the browser gets nothing.
    """

    def test_declared_sites_are_started_again(self, tmp_path):
        from portable.daemon.server import ControlServer
        from portable.sites import Site

        server = ControlServer()
        server.sites.add(Site(name="demo", root=tmp_path))

        started = []
        server.stack.reconcile = lambda sites, services: started.append(
            [site.name for site in sites]
        ) or {"sites": len(sites)}

        server.restore()

        assert started == [["demo"]]

    def test_a_failure_to_restore_does_not_stop_the_daemon(self, tmp_path):
        """
        The property that makes this safe to do at startup.

        Restoring is exactly where a machine-shaped failure lands — a port taken
        since last time, a runtime deleted from under us. A daemon that refuses
        to start because of one is a daemon nothing can reach to fix it,
        including the command that would move it off that port.
        """
        from portable.daemon.server import ControlServer

        server = ControlServer()

        def explode(sites, services):
            raise RuntimeError("port 80 is taken")

        server.stack.reconcile = explode

        result = server.restore()

        assert result["restored"] is False
        assert "port 80" in result["error"]

    def test_the_daemon_actually_calls_it_on_startup(self, tmp_path):
        """
        Run the real entry point, not the class.

        The call sits in `__main__.py`, which no other test executes — and a
        restore that is implemented, tested and never wired up is indisputably
        worse than one that was never written, because everything reads as done.
        This is the same gap that once let the daemon ship unable to import its
        own package: green locally, broken everywhere else.
        """
        import subprocess

        log = tmp_path / "daemon.log"
        pid = spawn.start_detached(
            [sys.executable, "-m", "portable.daemon"],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            },
            log=log,
        )

        try:
            deadline = time.monotonic() + 20

            while time.monotonic() < deadline:
                if "restored:" in log.read_text(encoding="utf-8", errors="replace"):
                    break

                time.sleep(0.1)
            else:
                raise AssertionError(
                    f"The daemon never reported restoring anything.\n{paths.tail(log)}"
                )
        finally:
            endpoint = discovery.read()

            if endpoint is not None:
                with contextlib.suppress(Exception):
                    Client(endpoint=endpoint).shutdown()

            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)] if os.name == "nt"
                    else ["kill", "-9", str(pid)],
                    capture_output=True,
                    check=False,
                )


class TestTheDiscoveryFileIsNeverSeenHalfWritten:
    """
    The failure this prevents is a daemon that is alive and unreachable.

    `read` treats a file it cannot parse as debris and deletes it — which is
    right for a file truncated by a crash. But the writer used to truncate and
    then fill, so for a moment the file was legitimately empty, and a client
    polling ten times a second while the daemon starts lands in that moment. It
    then deleted the note the daemon had just written and would never write
    again, and polled an empty directory until it gave up.

    It failed about one Windows CI run in four, as a timeout with nothing in any
    log to explain it.
    """

    def test_a_reader_racing_the_writer_never_sees_nothing(self, tmp_path):
        import threading

        target = tmp_path / "daemon.json"
        endpoint = discovery.Endpoint(port=1234, token="t" * 43, pid=os.getpid())
        stop = threading.Event()
        seen = []

        def write_repeatedly():
            while not stop.is_set():
                discovery.write(endpoint, target)

        writer = threading.Thread(target=write_repeatedly, daemon=True)
        writer.start()

        try:
            deadline = time.monotonic() + 2

            while time.monotonic() < deadline:
                if target.exists():
                    seen.append(discovery.read(target))
        finally:
            stop.set()
            writer.join(timeout=5)

        # The property that matters is not that every read succeeds — Windows
        # denies the read outright while the rename is in progress, and a reader
        # that looks again a moment later is fine. It is that no read ever
        # destroys the file. That is what turned a momentary miss into a daemon
        # nothing could reach for the rest of its life.
        assert target.exists(), "a reader deleted the file"
        assert any(entry is not None for entry in seen), "no read ever succeeded"
        assert all(
            entry is None or (entry.port == 1234 and entry.pid == os.getpid())
            for entry in seen
        ), "a read returned something other than what was written"

    def test_nothing_is_left_behind_beside_it(self, tmp_path):
        # The temporary is renamed into place, not left as litter in a directory
        # the tool also uses for pids and sockets.
        target = tmp_path / "run" / "daemon.json"
        discovery.write(discovery.Endpoint(port=1, token="t", pid=os.getpid()), target)

        assert [entry.name for entry in target.parent.iterdir()] == ["daemon.json"]


class TestUpdatesAndRemoval:
    """
    Updating stays on the line it finds, and removing is what balances that.

    The two belong together. An update installs alongside rather than replacing,
    so that anything pinned to the old version keeps working — which is right,
    and means something eventually has to take the old one away.
    """

    def server(self):
        from portable.daemon.server import ControlServer

        return ControlServer()

    def php(self, tmp_path, version: str, managed: bool = True):
        from portable.runtimes import Installed

        directory = tmp_path / "runtimes" / "php" / version
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "php.exe").write_text("", encoding="utf-8")

        return Installed(name="php", version=version, directory=directory, managed=managed)

    def test_an_update_stays_inside_its_own_line(self, tmp_path, monkeypatch):
        """
        8.4.24 looks for the newest 8.4.x, never 8.5.

        A PHP branch change brings deprecations to every site that pinned
        nothing, and a database major will not open the data directory the
        previous one created. Those are installed by name, deliberately.
        """
        from portable.catalog import php as php_catalog

        asked = []
        server = self.server()
        server.runtimes.add(self.php(tmp_path, "8.4.24"))

        def available(index=None, line=None, archive=None):
            asked.append(line)

            return [type("O", (), {"version": "8.4.26", "note": ""})()]

        monkeypatch.setattr(php_catalog, "available", available)

        result = server._updates({})

        # `available(line=...)`, not `resolve(...)`: each publisher's resolve
        # takes something different, and asking all six for "the newest 8"
        # produced 404s from Redis and PostgreSQL and nothing at all for an
        # archived PHP whose branch the index no longer lists.
        assert asked == ["8.4"], "it asked for something other than its own branch"
        assert result["runtimes"][0]["update"] is True
        assert result["runtimes"][0]["latest"] == "8.4.26"

    def test_an_adopted_runtime_is_reported_rather_than_skipped(self, tmp_path):
        # It may well be out of date. Saying nothing about it reads as saying it
        # is current.
        server = self.server()
        server.runtimes.add(self.php(tmp_path, "8.2.0", managed=False))

        entry = server._updates({})["runtimes"][0]

        assert entry["managed"] is False
        assert entry["update"] is False

    def test_one_publisher_being_down_does_not_hide_the_others(self, tmp_path, monkeypatch):
        from portable.catalog import php as php_catalog

        server = self.server()
        server.runtimes.add(self.php(tmp_path, "8.4.24"))
        monkeypatch.setattr(
            php_catalog, "available", lambda *a, **k: (_ for _ in ()).throw(OSError("no network"))
        )

        entry = server._updates({})["runtimes"][0]

        assert "no network" in entry["error"]
        assert entry["installed"] == "8.4.24"

    def test_removing_the_last_php_a_site_relies_on_is_refused(self, tmp_path):
        """
        The failure this prevents is a site that stops starting.

        A site pinned to `8.4` follows whatever 8.4.x is newest, so taking away
        the only one leaves it failing with a message about a version nobody
        typed.
        """
        from portable.sites import Site

        server = self.server()
        server.runtimes.add(self.php(tmp_path, "8.4.24"))
        server.sites.add(Site(name="demo", root=tmp_path, php="8.4"))

        with pytest.raises(Exception) as excinfo:
            server._runtime_remove({"name": "php", "version": "8.4.24"})

        assert "demo" in str(excinfo.value.args)

    def test_removing_one_of_two_on_the_same_line_is_allowed(self, tmp_path):
        # The site follows the other one. This is the case that makes updating
        # and then reclaiming disk possible at all.
        from portable.sites import Site

        server = self.server()
        server.runtimes.add(self.php(tmp_path, "8.4.24"))
        server.runtimes.add(self.php(tmp_path, "8.4.26"))
        server.sites.add(Site(name="demo", root=tmp_path, php="8.4"))

        result = server._runtime_remove({"name": "php", "version": "8.4.24"})

        assert result["deleted"] is True
        assert not (tmp_path / "runtimes" / "php" / "8.4.24").exists()

    def test_an_adopted_runtime_is_forgotten_and_not_deleted(self, tmp_path):
        # It belongs to whoever put it there. Forgetting it is the whole of what
        # this tool is entitled to do about it.
        server = self.server()
        entry = self.php(tmp_path, "8.1.0", managed=False)
        server.runtimes.add(entry)

        result = server._runtime_remove({"name": "php", "version": "8.1.0"})

        assert result["deleted"] is False
        assert entry.directory.exists(), "somebody else's PHP was deleted"


class TestAFailedSiteAddLeavesNothingBehind:
    """
    A site that could not be started must not be listed as if it exists.

    `service add` already worked this way; `site add` did not, so a PHP that
    would not start left a site `list` reported cheerfully — inviting the
    conclusion that it exists and is merely stopped, and a second confusion when
    starting it does nothing.
    """

    def server(self):
        from portable.daemon.server import ControlServer

        return ControlServer()

    def test_a_new_site_is_withdrawn(self, tmp_path):
        from portable.daemon.server import ApiError

        server = self.server()

        def explode(*_args, **_kwargs):
            raise ApiError(500, "nope", "PHP would not start")

        server._reconcile = explode

        with pytest.raises(ApiError):
            server._site_add({"name": "demo", "root": str(tmp_path)})

        assert server.sites.all() == []

    def test_an_existing_one_is_not_destroyed_by_a_failed_replacement(self, tmp_path):
        """
        The case that makes a blanket rollback wrong.

        Re-adding a site — to move its root, or change its PHP — that then fails
        must leave the declaration that was working exactly where it was.
        Deleting it would turn a failed edit into a lost site.
        """
        from portable.daemon.server import ApiError
        from portable.sites import Site

        server = self.server()
        server.sites.add(Site(name="demo", root=tmp_path, php="8.4"))

        def explode(*_args, **_kwargs):
            raise ApiError(500, "nope", "PHP would not start")

        server._reconcile = explode

        with pytest.raises(ApiError):
            server._site_add({"name": "demo", "root": str(tmp_path), "php": "7.1"})

        assert [site.name for site in server.sites.all()] == ["demo"]


class TestAnAnswerThatBreaksOff:
    """
    A truncated response is "not answering", not a traceback.

    `IncompleteRead` is an `http.client.HTTPException` and not a `URLError`, so
    it escaped both the client and the retry loop in `portable up` — and reached
    the person as a stack trace about bytes, during startup, which is exactly
    when a client should simply look again.
    """

    def test_it_is_reported_as_not_running(self, monkeypatch):
        import http.client
        import urllib.request

        from portable.daemon.client import Client, NotRunning

        client = Client(endpoint=discovery.Endpoint(port=1, token="t", pid=os.getpid()))

        def truncated(*_args, **_kwargs):
            raise http.client.IncompleteRead(b"", 18)

        monkeypatch.setattr(urllib.request, "urlopen", truncated)

        with pytest.raises(NotRunning) as excinfo:
            client.call("GET", "/v1/ping")

        assert "broke off" in str(excinfo.value)

    def test_a_reset_connection_is_too(self, monkeypatch):
        import urllib.request

        from portable.daemon.client import Client, NotRunning

        client = Client(endpoint=discovery.Endpoint(port=1, token="t", pid=os.getpid()))
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(ConnectionResetError("reset by peer")),
        )

        with pytest.raises(NotRunning):
            client.call("GET", "/v1/ping")

    def test_the_startup_wait_keeps_waiting_through_one(self, monkeypatch):
        # The property that matters: a truncated answer during startup must not
        # end the wait, because the daemon is very likely about to be ready.
        from portable import cli

        endpoint = discovery.Endpoint(port=1, token="t", pid=os.getpid())
        attempts = []

        monkeypatch.setattr(cli.spawn, "is_running", lambda pid: True)
        monkeypatch.setattr(cli.discovery, "read", lambda: endpoint)

        class Flaky:
            def __init__(self, endpoint=None):
                pass

            def ping(self):
                attempts.append(1)

                if len(attempts) < 3:
                    raise cli.NotRunning("the answer broke off")

                return {"ok": True}

        monkeypatch.setattr(cli, "Client", Flaky)

        assert cli._await_daemon(pid=1, timeout=5) is endpoint
        assert len(attempts) == 3, "it gave up on the first truncated answer"


class TestReadingAnAdoptedRuntimesVersion:
    """
    The strings below are what these programs actually print.

    Guessing from the directory name works until it does not — a PHP unpacked
    into `php-latest` would be recorded as version `latest` and sort below
    everything — so the runtime is asked. Which only helps if the answer is read
    correctly, and each of these publishers answers differently.
    """

    def parse(self, output: str) -> str:
        from portable.daemon import server

        return server._parse_version(output)

    def test_postgres_numbers_its_releases_with_two_components(self):
        """
        `16.13`, not `16.13.0`.

        A pattern demanding three found nothing and recorded "unknown" — and
        that is not merely untidy. The registry keys on name and version, so two
        adopted PostgreSQLs would both be "unknown" and the second would replace
        the first.
        """
        assert self.parse("postgres (PostgreSQL) 16.13(ServBay)") == "16.13"

    def test_mariadb_prints_its_own_path_first(self):
        """
        And the path has numbers in it.

        `/package/mariadb/11.4/11.4.10/bin/mariadbd Ver 11.4.10-MariaDB-log` —
        a version pattern applied to the whole line finds the directory before
        the release. On this author's machine the two happen to agree, which is
        exactly the kind of coincidence that hides a bug.
        """
        printed = (
            "/Applications/ServBay/package/mariadb/11.4/11.4.10/bin/mariadbd  "
            "Ver 11.4.10-MariaDB-log for osx12.00 on arm64"
        )

        assert self.parse(printed) == "11.4.10"

    def test_a_path_that_disagrees_with_the_version_is_not_believed(self):
        # The case the machine here cannot produce: a binary in one directory
        # reporting a different version. The path must lose.
        printed = "/opt/mariadb/10.6/bin/mariadbd  Ver 11.4.10-MariaDB for linux"

        assert self.parse(printed) == "11.4.10"

    def test_php_and_node_and_caddy_are_read_too(self):
        assert self.parse("PHP 8.2.30 (cli) (built: Jan  1 2026)") == "8.2.30"
        assert self.parse("v24.19.0") == "24.19.0"
        assert self.parse("v2.11.4 h1:XKxkMTgNSizEvKG6QHue6cAsFOteU2qA61w2tKkCWi0=") == "2.11.4"

    def test_nothing_recognisable_says_so(self):
        assert self.parse("some program with no version at all") == "unknown"
