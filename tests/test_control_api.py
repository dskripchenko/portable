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

import json
import urllib.error
import urllib.request

import pytest

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
