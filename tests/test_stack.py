"""
Sites, and making the processes match them.

`reconcile()` is written as a reconcile rather than as start/stop commands, so
the tests are about the same thing from several directions: does the running set
match the declared set, however it got there.

The processes started here are stand-ins, not PHP — what is under test is the
arithmetic of which pools should exist, not whether PHP answers. That is checked
by running the real thing; see the README.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from portable.runtimes import Installed
from portable.runtimes import Registry as Runtimes
from portable.sites import InvalidSite, Site
from portable.sites import Registry as Sites
from portable.stack import Stack, StackError
from portable.supervisor import Supervisor

FAKE_CADDY = Path(__file__).parent / "fake_caddy.py"


@pytest.fixture
def runtimes(tmp_path) -> Runtimes:
    registry = Runtimes(tmp_path / "runtimes.json")

    for version in ("8.3.33", "8.4.24"):
        directory = tmp_path / "php" / version
        directory.mkdir(parents=True)
        # A stand-in that stays up when started, so a "pool" behaves like one.
        _stub(directory / "php-cgi", "import time; time.sleep(60)")
        registry.add(Installed(name="php", version=version, directory=directory, managed=True))

    # A placeholder binary: the stand-in is launched through `router_command`
    # below, so nothing here needs to be executable. Relying on a shebang worked
    # on the author's machine and on neither CI platform — Windows has no such
    # thing, and the failure was a process that died writing nothing at all.
    router = tmp_path / "caddy"
    router.mkdir()
    (router / "caddy").write_text("", encoding="utf-8")
    registry.add(Installed(name="caddy", version="2.11.4", directory=router, managed=True))

    return registry


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _fake_router(_executable: Path, config_file: Path) -> list[str]:
    """The stand-in, started the way Caddy would be but through the interpreter."""
    return [sys.executable, str(FAKE_CADDY), "run", "--config", str(config_file), "--adapter", ""]


@pytest.fixture
def stack(runtimes) -> Stack:
    # High ports only. The real thing tries 80 first, and a suite whose speed
    # depends on what is listening on 80 — or on whether the runner may bind it
    # — is a suite that is slow on one machine and flaky on another.
    from portable import ports as port_finder

    instance = Stack(
        supervisor=Supervisor(),
        runtimes=runtimes,
        router_command=_fake_router,
        candidate_ports=tuple(port_finder.find(2, candidates=range(9700, 9800))),
    )

    yield instance

    instance.supervisor.stop_all(timeout=5)


def site(tmp_path, name: str = "demo", php: str | None = None) -> Site:
    root = tmp_path / "sites" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.php").write_text("<?php echo 1;", encoding="utf-8")

    return Site(name=name, root=root, php=php)


class TestSiteNames:
    def test_a_name_with_a_dot_is_refused(self, tmp_path):
        # It would claim `a.b.localhost` — a hostname nobody asked for.
        registry = Sites(tmp_path / "sites.json")

        with pytest.raises(InvalidSite) as excinfo:
            registry.add(Site(name="a.b", root=tmp_path))

        assert "hostname" in str(excinfo.value)

    def test_a_missing_directory_is_refused(self, tmp_path):
        registry = Sites(tmp_path / "sites.json")

        with pytest.raises(InvalidSite):
            registry.add(Site(name="demo", root=tmp_path / "nowhere"))

    def test_the_hostname_is_derived_and_never_stored(self, tmp_path):
        # Storing it would let it disagree with the name it came from.
        assert site(tmp_path, "demo").hostname == "demo.localhost"

    def test_adding_the_same_name_twice_replaces_rather_than_duplicates(self, tmp_path):
        registry = Sites(tmp_path / "sites.json")
        registry.add(site(tmp_path, "demo"))
        registry.add(site(tmp_path, "demo"))

        assert len(registry.all()) == 1


class TestPools:
    def test_two_sites_on_the_same_php_share_one_pool(self, stack, tmp_path):
        # Keyed by the resolved version, so `8.4` and `8.4.24` collapse rather
        # than starting two identical pools.
        result = stack.reconcile(
            [site(tmp_path, "one", php="8.4"), site(tmp_path, "two", php="8.4.24")]
        )

        assert result["pools"] == ["8.4.24"]

    def test_two_sites_on_different_php_get_a_pool_each(self, stack, tmp_path):
        result = stack.reconcile(
            [site(tmp_path, "old", php="8.3"), site(tmp_path, "new", php="8.4")]
        )

        assert result["pools"] == ["8.3.33", "8.4.24"]

    def test_a_site_with_no_preference_follows_the_newest_installed(self, stack, tmp_path):
        assert stack.reconcile([site(tmp_path, "demo")])["pools"] == ["8.4.24"]

    def test_pools_do_not_share_ports(self, stack, tmp_path):
        stack.reconcile([site(tmp_path, "old", php="8.3"), site(tmp_path, "new", php="8.4")])

        ports = [worker.port for built in stack.pools.values() for worker in built.workers]

        assert len(ports) == len(set(ports)), "two workers were given the same port"

    def test_a_pool_nothing_points_at_is_retired(self, stack, tmp_path):
        # Left running it holds four ports and a few hundred megabytes for a
        # version nothing uses — invisible, because nothing ever fails.
        stack.reconcile([site(tmp_path, "old", php="8.3")])
        result = stack.reconcile([site(tmp_path, "new", php="8.4")])

        assert result["pools_stopped"] == ["8.3.33"]
        assert stack.pools.keys() == {"8.4.24"}
        assert all("8.3.33" not in entry["name"] for entry in stack.supervisor.status())

    def test_reconciling_twice_changes_nothing(self, stack, tmp_path):
        # The property that makes this safe to call from every route that
        # modifies anything.
        sites = [site(tmp_path, "demo")]
        stack.reconcile(sites)
        before = {entry["name"]: entry["pid"] for entry in stack.supervisor.status()}

        result = stack.reconcile(sites)

        assert result["pools_started"] == []
        assert {entry["name"]: entry["pid"] for entry in stack.supervisor.status()} == before

    def test_removing_the_last_site_stops_everything(self, stack, tmp_path):
        stack.reconcile([site(tmp_path, "demo")])
        stack.reconcile([])

        assert stack.pools == {}
        assert all(entry["state"] == "stopped" for entry in stack.supervisor.status())

    def test_a_site_asking_for_a_php_that_is_not_installed_is_refused_by_name(
        self, stack, tmp_path
    ):
        with pytest.raises(StackError) as excinfo:
            stack.reconcile([site(tmp_path, "demo", php="7.4")])

        assert "7.4" in str(excinfo.value)
        assert "demo" in str(excinfo.value)


class TestPortProbe:
    def test_the_probe_hostname_cannot_belong_to_a_site(self):
        """
        The probe asks for a hostname and expects the unmatched-host answer. If
        a real site could be called that, the probe would get the site instead
        and read it as "the port is not ours".
        """
        from portable.sites import NAME

        label = Stack.PROBE_HOST.split(".")[0]

        assert "." in Stack.PROBE_HOST
        assert not NAME.match(Stack.PROBE_HOST), "a site could be named this"
        assert NAME.match(label), "the first label alone is a legal site name — that is the point"
