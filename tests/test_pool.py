"""
The pool, the ports it sits on, and the router in front of it.

All of this is arithmetic and document-building, which is why it can be tested
on a machine with no `php-cgi.exe` — every machine this is developed on. What
cannot be tested here is whether PHP actually answers, and that is checked by
running it: see the note in the README about the end-to-end run.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from portable import pool, ports
from portable.router import caddy
from portable.runtimes import Installed, NotInstalled, Registry


def fake_php(tmp_path: Path, version: str = "8.4.24") -> Installed:
    """An unpacked PHP, shaped like the real archive."""
    directory = tmp_path / "runtimes" / "php" / version
    (directory / "ext").mkdir(parents=True)

    for name in ("php.exe", "php-cgi.exe"):
        (directory / name).write_text("", encoding="utf-8")

    return Installed(name="php", version=version, directory=directory, managed=True)


class TestPorts:
    def test_it_finds_the_number_asked_for(self):
        found = ports.find(3)

        assert len(found) == 3
        assert len(set(found)) == 3, "the same port was handed out twice"

    def test_it_skips_ports_already_promised(self):
        # Allocating two pools in quick succession would otherwise give both the
        # same numbers: every one is genuinely free at the moment it is asked
        # about, because nothing has started yet.
        first = ports.find(2)
        second = ports.find(2, taken=set(first))

        assert not set(first) & set(second)

    def test_it_skips_a_port_something_is_listening_on(self):
        with socket.socket() as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            taken = held.getsockname()[1]

            assert not ports.is_free(taken)
            assert taken not in ports.find(3, candidates=range(taken, taken + 5))

    def test_running_out_says_so_rather_than_returning_fewer(self):
        # Returning a short list would produce a pool smaller than asked for,
        # and nothing downstream would notice until concurrency was wrong.
        with pytest.raises(ports.NoFreePort) as excinfo:
            ports.find(5, candidates=range(9990, 9992))

        assert "5" in str(excinfo.value)

    def test_the_ranges_avoid_the_dynamic_client_range(self):
        # Windows hands out 49152-65535 for outgoing connections. Anything
        # placed there can find its port taken between being chosen and being
        # bound — which is not hypothetical: the router's admin port was
        # allocated that way and lost the race on CI.
        assert ports.POOL_RANGE.stop <= 49152
        assert ports.ADMIN_RANGE.stop <= 49152

    def test_the_pool_and_the_admin_endpoint_cannot_collide(self):
        assert not set(ports.POOL_RANGE) & set(ports.ADMIN_RANGE)


class TestRuntimeRegistry:
    def test_it_remembers_what_was_added(self, tmp_path):
        registry = Registry(tmp_path / "runtimes.json")
        entry = fake_php(tmp_path)
        registry.add(entry)

        assert registry.get("php").directory == entry.directory

    def test_the_newest_version_wins_by_number_not_by_insertion_order(self, tmp_path):
        # A machine that installed 8.4 after 8.5 should still default to 8.5.
        registry = Registry(tmp_path / "runtimes.json")
        registry.add(fake_php(tmp_path, "8.5.9"))
        registry.add(fake_php(tmp_path, "8.4.24"))

        assert registry.get("php").version == "8.5.9"

    def test_a_branch_selects_the_newest_patch_in_it(self, tmp_path):
        registry = Registry(tmp_path / "runtimes.json")
        registry.add(fake_php(tmp_path, "8.4.1"))
        registry.add(fake_php(tmp_path, "8.4.24"))
        registry.add(fake_php(tmp_path, "8.5.9"))

        assert registry.get("php", "8.4").version == "8.4.24"

    def test_an_entry_whose_directory_is_gone_is_dropped(self, tmp_path):
        # A discovered runtime whose owner uninstalled it. Offering it produces
        # a failure when something is started rather than when it is listed.
        registry = Registry(tmp_path / "runtimes.json")
        entry = fake_php(tmp_path)
        registry.add(entry)

        import shutil

        shutil.rmtree(entry.directory)

        assert registry.all() == []

    def test_a_missing_version_names_what_is_present(self, tmp_path):
        registry = Registry(tmp_path / "runtimes.json")
        registry.add(fake_php(tmp_path, "8.4.24"))

        with pytest.raises(NotInstalled) as excinfo:
            registry.get("php", "7.4")

        assert "8.4.24" in str(excinfo.value)

    def test_it_finds_the_executable_wherever_the_archive_put_it(self, tmp_path):
        # Publishers disagree about depth, and a hardcoded relative path breaks
        # silently on the first archive that nests differently.
        directory = tmp_path / "nested"
        (directory / "bin").mkdir(parents=True)
        (directory / "bin" / "caddy.exe").write_text("", encoding="utf-8")

        entry = Installed(name="caddy", version="2.11.4", directory=directory, managed=True)

        assert entry.executable("caddy").name == "caddy.exe"

    def test_a_missing_executable_says_where_it_looked(self, tmp_path):
        entry = Installed(
            name="php", version="8.4.24", directory=tmp_path, managed=True
        )

        with pytest.raises(NotInstalled) as excinfo:
            entry.executable("php")

        assert "php-cgi" in str(excinfo.value)


class TestPool:
    def test_it_builds_one_worker_per_requested_slot(self, tmp_path):
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=4)

        assert len(built.workers) == 4
        assert len({worker.port for worker in built.workers}) == 4

    def test_each_worker_binds_its_own_port_in_fastcgi_mode(self, tmp_path):
        # `-b` is what turns php-cgi into a FastCGI server. Without it the
        # process reads one request from stdin and exits.
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=2)

        for worker in built.workers:
            assert "-b" in worker.spec.argv
            assert f"127.0.0.1:{worker.port}" in worker.spec.argv

    def test_workers_are_told_to_retire_themselves(self, tmp_path):
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=1)

        assert built.workers[0].spec.env["PHP_FCGI_MAX_REQUESTS"] == str(pool.DEFAULT_MAX_REQUESTS)

    def test_children_are_not_delegated_to_php(self, tmp_path):
        # `PHP_FCGI_CHILDREN` needs fork(), which Windows does not have. Setting
        # it would suggest PHP manages the pool when this tool does.
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=1)

        assert "PHP_FCGI_CHILDREN" not in built.workers[0].spec.env

    def test_a_dead_worker_is_meant_to_come_back(self, tmp_path):
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=1)

        assert built.workers[0].spec.restart is True

    def test_upstreams_are_addressed_for_the_router(self, tmp_path):
        built = pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=2)

        assert all(upstream.startswith("127.0.0.1:") for upstream in built.upstreams)
        assert len(built.upstreams) == 2

    def test_an_empty_pool_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            pool.build(fake_php(tmp_path), ini=tmp_path / "php.ini", logs=tmp_path, workers=0)


class TestGeneratedIni:
    def test_it_points_at_the_extension_directory_of_its_own_build(self, tmp_path):
        runtime = fake_php(tmp_path)
        ini = pool.ini_for(runtime, tmp_path / "conf")

        assert str(runtime.directory / "ext") in ini.read_text(encoding="utf-8")

    def test_it_is_written_once_and_then_left_alone(self, tmp_path):
        # Somebody will edit it — that is why it is on disk. Regenerating on
        # every start would quietly discard their work.
        runtime = fake_php(tmp_path)
        ini = pool.ini_for(runtime, tmp_path / "conf")
        ini.write_text("; edited by hand\n", encoding="utf-8")

        assert pool.ini_for(runtime, tmp_path / "conf").read_text() == "; edited by hand\n"

    def test_versions_do_not_share_one_file(self, tmp_path):
        first = pool.ini_for(fake_php(tmp_path, "8.4.24"), tmp_path / "conf")
        second = pool.ini_for(fake_php(tmp_path, "8.5.9"), tmp_path / "conf")

        assert first != second


class TestCaddyConfig:
    def site(self) -> caddy.Site:
        return caddy.Site(
            name="demo",
            root=Path("/srv/demo"),
            upstreams=["127.0.0.1:9001", "127.0.0.1:9002"],
        )

    def test_the_hostname_needs_no_hosts_file(self):
        # `*.localhost` resolves to the loopback on Windows and macOS without
        # anything being written to a system file or any privilege being asked
        # for. `.test` resolves nowhere without a hosts entry or a DNS server.
        assert self.site().hostname == "demo.localhost"

    def test_a_request_is_retried_across_the_pool_rather_than_failing(self):
        # The property that matters most here. A worker retires itself every
        # PHP_FCGI_MAX_REQUESTS; without retries the request arriving in that
        # window gets a 502 while the other workers sit idle. Verified against a
        # running stack: 15 requests during a worker replacement, all 200.
        route = caddy.route_for(self.site())
        proxy = _find_handler(route, "reverse_proxy")

        assert proxy["load_balancing"]["try_duration"] != "0s"
        assert proxy["health_checks"]["passive"]["max_fails"] >= 1

    def test_it_balances_by_idleness_not_by_turn(self):
        # A php-cgi worker serves one request at a time, so the useful question
        # is which one is free, not whose turn it is.
        proxy = _find_handler(caddy.route_for(self.site()), "reverse_proxy")

        assert proxy["load_balancing"]["selection_policy"]["policy"] == "least_conn"

    def test_every_worker_is_an_upstream(self):
        proxy = _find_handler(caddy.route_for(self.site()), "reverse_proxy")

        assert [upstream["dial"] for upstream in proxy["upstreams"]] == self.site().upstreams

    def test_only_php_reaches_the_pool(self):
        # Reversing this order sends `style.css` to PHP.
        route = caddy.route_for(self.site())
        subroutes = route["handle"][0]["routes"]
        php_route = next(r for r in subroutes if _has_handler(r, "reverse_proxy"))

        assert php_route["match"] == [{"path": ["*.php"]}]

    def test_a_hostname_no_site_claims_gets_an_answer_that_explains_itself(self):
        # Caddy's own default is an empty 200, which cannot be told apart from a
        # broken site.
        document = caddy.config([self.site()])
        routes = document["apps"]["http"]["servers"][caddy.SERVER]["routes"]
        fallback = routes[-1]

        assert "match" not in fallback, "the fallback must not be conditional"

        response = fallback["handle"][0]

        assert response["status_code"] == 404
        assert "demo.localhost" in response["body"], "it should name what is configured"

    def test_each_site_can_be_addressed_on_its_own(self):
        # Caddy's admin API can replace a node by id. Without one, changing a
        # single site means sending the whole document.
        assert caddy.route_for(self.site())["@id"] == "portable-site-demo"

    def test_caddy_is_told_the_config_is_already_json(self):
        # Without the empty adapter Caddy assumes a Caddyfile and fails on the
        # first brace.
        command = caddy.command(Path("/bin/caddy"), Path("/etc/caddy.json"))

        assert "--adapter" in command
        assert command[command.index("--adapter") + 1] == ""


def _find_handler(route: dict, handler: str) -> dict:
    for subroute in route["handle"][0]["routes"]:
        for candidate in subroute.get("handle", []):
            if candidate.get("handler") == handler:
                return candidate

    raise AssertionError(f"no {handler} handler in the route")


def _has_handler(route: dict, handler: str) -> bool:
    return any(candidate.get("handler") == handler for candidate in route.get("handle", []))
