"""
Python: installing it from python.org, and serving applications written in it.

The catalog is read from pages captured from python.org — filtered to the
entries this tool reads, otherwise as published. The applications are stand-ins
started through the real interpreter running these tests: what is under test is
which process is started, where, with what, and that the router is pointed at
it — not whether Django answers. That is checked by running the real thing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from portable import apps, spawn
from portable.catalog import CatalogError
from portable.catalog import python as catalog
from portable.router import caddy
from portable.runtimes import Installed
from portable.runtimes import Registry as Runtimes
from portable.sites import InvalidSite, Site
from portable.sites import Registry as Sites
from portable.stack import Stack, StackError
from portable.supervisor import Supervisor

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CADDY = Path(__file__).parent / "fake_caddy.py"


@pytest.fixture
def pages() -> list[dict]:
    return [
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for name in (
            "python-index-windows.json",
            "python-index-windows-recent.json",
            "python-index-windows-legacy.json",
        )
    ]


def _quoted(path: str) -> str:
    """A path as one word of a command line, spaces and all."""
    return f'"{path}"'


# ----------------------------------------------------------------------- catalog


class TestTheCatalog:
    def test_latest_is_the_newest_final_release(self, pages):
        # The first page also has 3.16 alphas, newer by number and not by intent.
        build = catalog.resolve("latest", pages=pages)

        assert build.version == "3.15.2"
        assert not catalog.is_prerelease(build.version)

    def test_a_line_resolves_to_its_newest_final_release(self, pages):
        build = catalog.resolve("3.13", pages=pages)

        assert build.version == "3.13.17"
        assert build.url.endswith("/python-3.13.17-amd64.zip")
        assert build.filename == "python-3.13.17-amd64.zip"
        assert build.variant == "amd64"

    def test_every_build_from_the_current_pages_carries_a_sha256(self, pages):
        build = catalog.resolve("3.14", pages=pages)

        assert build.algorithm == "sha256"
        assert build.checksum and len(build.checksum) == 64

    def test_only_the_regular_x64_build_is_offered(self, pages):
        # The fixture keeps a free-threaded, an embeddable, a 32-bit, an ARM64
        # and a test-suite entry on purpose: none of them is what a project's
        # requirements were written against.
        build = catalog.resolve("3.13.17", pages=pages)

        assert "amd64.zip" in build.url
        assert "embed" not in build.url and "t-amd64" not in build.url

    def test_an_older_patch_is_found_on_a_later_page(self, pages):
        build = catalog.resolve("3.12.4", pages=pages)

        assert build.version == "3.12.4"
        assert build.checksum

    def test_the_oldest_page_installs_unverified(self, pages):
        # NuGet packages, without a digest. Installable, and said to be
        # unverified rather than quietly treated as checked.
        build = catalog.resolve("3.10", pages=pages)

        assert build.version == "3.10.11"
        assert build.filename.endswith(".nupkg")
        assert build.checksum is None

    def test_a_line_with_only_prereleases_is_refused_with_what_it_has(self, pages):
        with pytest.raises(CatalogError) as excinfo:
            catalog.resolve("3.16", pages=pages)

        assert "3.16.0a2" in str(excinfo.value)

    def test_a_prerelease_installs_when_named(self, pages):
        assert catalog.resolve("3.16.0a2", pages=pages).version == "3.16.0a2"

    def test_a_version_that_never_existed_names_its_line(self, pages):
        with pytest.raises(CatalogError) as excinfo:
            catalog.resolve("3.13.99", pages=pages)

        assert "3.13.17" in str(excinfo.value)

    def test_later_pages_are_not_read_when_the_first_answers(self, pages):
        read = []

        def lazily():
            for page in pages:
                read.append(page)
                yield page

        catalog.resolve("3.13", pages=lazily())

        assert len(read) == 1

    def test_the_listing_is_one_per_line_newest_first(self, pages):
        offers = catalog.available(pages=pages)
        versions = [offer.version for offer in offers]

        assert versions[0] == "3.16.0a2"
        assert "pre-release" in offers[0].note
        assert "3.15.2" in versions and "3.13.17" in versions
        assert len({catalog.line(version) for version in versions}) == len(versions)

    def test_a_line_lists_every_release_across_the_pages(self, pages):
        versions = [offer.version for offer in catalog.available(pages=pages, line="3.12")]

        assert versions[0] == "3.12.10"
        assert "3.12.0" in versions
        assert versions == sorted(versions, key=catalog._version_key, reverse=True)

    def test_a_release_sorts_after_its_candidates(self):
        assert catalog._version_key("3.15.0") > catalog._version_key("3.15.0rc3")
        assert catalog._version_key("3.15.0rc1") > catalog._version_key("3.15.0b4")

    def test_the_line_is_the_minor(self):
        assert catalog.line("3.13.17") == "3.13"


class TestTheNextPageIsFollowed:
    def test_a_relative_next_is_resolved_against_the_index(self, monkeypatch):
        fetched = []

        def fetch(url):
            fetched.append(url)

            return {"versions": [], "next": "index-windows-recent.json"} if len(fetched) == 1 else {
                "versions": []
            }

        monkeypatch.setattr(catalog, "_fetch_page", fetch)

        list(catalog._pages())

        assert fetched == [
            catalog.INDEX_URL,
            "https://www.python.org/ftp/python/index-windows-recent.json",
        ]

    def test_a_page_pointing_at_itself_ends_the_walk(self, monkeypatch):
        monkeypatch.setattr(
            catalog, "_fetch_page", lambda url: {"versions": [], "next": "index-windows.json"}
        )

        assert len(list(catalog._pages())) == 1


# ------------------------------------------------------------------------- apps


def python_runtime(tmp_path, version: str = "3.13.17") -> Installed:
    directory = tmp_path / "runtimes" / "python" / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "python.exe").write_text("", encoding="utf-8")

    return Installed(name="python", version=version, directory=directory, managed=True)


def make_venv(root: Path, home: Path | None = None) -> Path:
    environment = root / ".venv"
    scripts = environment / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    (scripts / "uvicorn.exe").write_text("", encoding="utf-8")
    (environment / "pyvenv.cfg").write_text(
        f"home = {home or root / 'elsewhere'}\nversion = 3.13.17\n", encoding="utf-8"
    )

    return environment


class TestDescribingAnApplication:
    def test_the_port_goes_where_the_command_says(self, tmp_path):
        site = Site(name="api", root=tmp_path, kind="python", command="python -m http.server {port}")

        app = apps.build(site, 9123, python_runtime(tmp_path), log=tmp_path / "log")

        assert app.spec.argv[-1] == "9123"
        assert app.spec.env["PORT"] == "9123"
        assert app.upstream == "127.0.0.1:9123"

    def test_python_means_the_installed_interpreter_without_a_venv(self, tmp_path):
        runtime = python_runtime(tmp_path)
        site = Site(name="api", root=tmp_path, kind="python", command="python app.py")

        app = apps.build(site, 9000, runtime, log=tmp_path / "log")

        assert app.spec.argv[0] == str(runtime.directory / "python.exe")
        assert "VIRTUAL_ENV" not in app.spec.env

    def test_the_projects_venv_wins(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        environment = make_venv(project)
        site = Site(name="api", root=project, kind="python", command="python app.py")

        app = apps.build(site, 9000, python_runtime(tmp_path), log=tmp_path / "log")

        assert app.spec.argv[0] == str(environment / "Scripts" / "python.exe")
        assert app.spec.env["VIRTUAL_ENV"] == str(environment)
        assert app.spec.env["PATH"].split(os.pathsep)[0] == str(environment / "Scripts")

    def test_a_console_script_is_taken_from_the_venv(self, tmp_path):
        # On Windows `CreateProcess` searches the daemon's PATH, not the child's,
        # so a bare `uvicorn` would be found nowhere — or in another Python.
        project = tmp_path / "project"
        project.mkdir()
        environment = make_venv(project)
        site = Site(name="api", root=project, kind="python", command="uvicorn main:app --port {port}")

        app = apps.build(site, 9000, None, log=tmp_path / "log")

        assert app.spec.argv[0] == str(environment / "Scripts" / "uvicorn.exe")

    def test_a_command_found_nowhere_says_how_to_fix_it(self, tmp_path):
        site = Site(name="api", root=tmp_path, kind="python", command="no-such-server-anywhere")

        with pytest.raises(apps.InvalidCommand) as excinfo:
            apps.build(site, 9000, python_runtime(tmp_path), log=tmp_path / "log")

        assert "python -m no-such-server-anywhere" in str(excinfo.value)

    def test_a_relative_path_is_the_projects(self, tmp_path):
        site = Site(name="api", root=tmp_path, kind="python", command="bin/serve --port {port}")

        app = apps.build(site, 9000, python_runtime(tmp_path), log=tmp_path / "log")

        assert app.spec.argv[0] == str((tmp_path / "bin" / "serve").resolve())

    def test_it_runs_in_the_project_and_its_tree_is_stopped_together(self, tmp_path):
        site = Site(name="api", root=tmp_path, kind="python", command="python app.py")

        spec = apps.build(site, 9000, python_runtime(tmp_path), log=tmp_path / "log").spec

        assert spec.cwd == tmp_path
        assert spec.tree is True
        assert spec.name == "python-api"
        assert spec.env["PYTHONUNBUFFERED"] == "1"

    def test_django_is_recognised_and_nothing_else_is_guessed(self, tmp_path):
        assert apps.default_command(tmp_path) is None

        (tmp_path / "main.py").write_text("", encoding="utf-8")
        assert apps.default_command(tmp_path) is None

        (tmp_path / "manage.py").write_text("", encoding="utf-8")
        assert apps.default_command(tmp_path) == "python manage.py runserver 127.0.0.1:{port}"

    def test_a_venv_says_which_python_it_was_made_from(self, tmp_path):
        environment = make_venv(tmp_path, home=tmp_path / "py")

        assert apps.venv_home(environment) == tmp_path / "py"


class TestPythonSitesAreRecorded:
    def test_a_python_site_round_trips(self, tmp_path):
        registry = Sites(tmp_path / "sites.json")
        registry.add(
            Site(name="api", root=tmp_path, kind="python", python="3.13", command="python a.py")
        )

        site = registry.get("api")

        assert site.is_python
        assert (site.python, site.command) == ("3.13", "python a.py")

    def test_a_file_from_before_python_sites_reads_as_php(self, tmp_path):
        path = tmp_path / "sites.json"
        path.write_text(
            json.dumps([{"name": "demo", "root": str(tmp_path), "php": "8.4"}]), encoding="utf-8"
        )

        site = Sites(path).get("demo")

        assert site.kind == "php" and not site.is_python

    def test_a_python_site_without_a_command_is_refused_with_an_example(self, tmp_path):
        with pytest.raises(InvalidSite) as excinfo:
            Sites(tmp_path / "sites.json").add(Site(name="api", root=tmp_path, kind="python"))

        assert "{port}" in str(excinfo.value)


class TestTheRoute:
    def test_a_python_site_is_proxied_whole(self, tmp_path):
        site = caddy.Site(name="api", root=tmp_path, upstreams=["127.0.0.1:9123"], proxy=True)

        route = caddy.route_for(site)
        handlers = [
            handler["handler"]
            for step in route["handle"][0]["routes"]
            for handler in step["handle"]
        ]

        assert route["match"] == [{"host": ["api.localhost"]}]
        assert "reverse_proxy" in handlers
        # No files, no front controller: the application has its own routing.
        assert "file_server" not in handlers and "rewrite" not in handlers

        proxy = route["handle"][0]["routes"][-1]["handle"][0]
        assert proxy["upstreams"] == [{"dial": "127.0.0.1:9123"}]
        assert "transport" not in proxy, "that would be FastCGI"

    def test_override_routes_come_before_the_proxy(self, tmp_path):
        site = caddy.Site(name="api", root=tmp_path, upstreams=["127.0.0.1:9123"], proxy=True)
        extra = [{"handle": [{"handler": "headers"}]}]

        steps = caddy.route_for(site, extra=extra)["handle"][0]["routes"]

        assert steps[1] == extra[0]
        assert steps[-1]["handle"][0]["handler"] == "reverse_proxy"


# ------------------------------------------------------------------------ stack


def _fake_router(_executable: Path, config_file: Path) -> list[str]:
    return [sys.executable, str(FAKE_CADDY), "run", "--config", str(config_file), "--adapter", ""]


@pytest.fixture
def runtimes(tmp_path) -> Runtimes:
    registry = Runtimes(tmp_path / "runtimes.json")
    registry.add(python_runtime(tmp_path))

    router = tmp_path / "caddy"
    router.mkdir()
    (router / "caddy").write_text("", encoding="utf-8")
    registry.add(Installed(name="caddy", version="2.11.4", directory=router, managed=True))

    return registry


@pytest.fixture
def stack(runtimes):
    from portable import ports as port_finder

    instance = Stack(
        supervisor=Supervisor(),
        runtimes=runtimes,
        router_command=_fake_router,
        candidate_ports=tuple(port_finder.find(2, candidates=range(9800, 9900))),
        app_grace=0.5,
    )

    yield instance

    instance.supervisor.stop_all(timeout=5)


def sleeper(tmp_path, name: str = "api", seconds: int = 60) -> Site:
    """A Python site whose application stays up, and does nothing else."""
    root = tmp_path / "sites" / name
    root.mkdir(parents=True, exist_ok=True)

    return Site(
        name=name,
        root=root,
        kind="python",
        command=f"{_quoted(sys.executable)} -c \"import time; time.sleep({seconds})\" {{port}}",
    )


class TestServingAPythonSite:
    def test_the_application_is_started_and_the_router_points_at_it(self, stack, tmp_path):
        from portable import paths

        result = stack.reconcile([sleeper(tmp_path)])

        assert result["apps_started"] == ["api"]
        app = stack.apps["api"]
        assert stack.supervisor.alive("python-api")

        document = json.loads((paths.conf() / "caddy.json").read_text(encoding="utf-8"))
        routes = document["apps"]["http"]["servers"]["portable"]["routes"]
        proxied = json.dumps(routes[0])
        assert f"127.0.0.1:{app.port}" in proxied
        assert "fastcgi" not in proxied

    def test_no_php_is_needed_for_a_python_only_machine(self, stack, tmp_path):
        result = stack.reconcile([sleeper(tmp_path)])

        assert result["pools"] == []

    def test_reconciling_again_leaves_it_running(self, stack, tmp_path):
        stack.reconcile([sleeper(tmp_path)])
        pid = stack.supervisor.status()[0]["pid"]

        result = stack.reconcile([sleeper(tmp_path)])

        assert result["apps_started"] == []
        assert next(e for e in stack.supervisor.status() if e["name"] == "python-api")["pid"] == pid

    def test_a_changed_command_restarts_it_on_the_same_port(self, stack, tmp_path):
        stack.reconcile([sleeper(tmp_path)])
        port = stack.apps["api"].port

        result = stack.reconcile([sleeper(tmp_path, seconds=61)])

        assert result["apps_started"] == ["api"]
        assert stack.apps["api"].port == port

    def test_removing_the_site_stops_the_application(self, stack, tmp_path):
        stack.reconcile([sleeper(tmp_path), sleeper(tmp_path, "other")])

        result = stack.reconcile([sleeper(tmp_path, "other")])

        assert result["apps_stopped"] == ["api"]
        assert "python-api" not in {entry["name"] for entry in stack.supervisor.status()}

    def test_two_sites_get_two_ports(self, stack, tmp_path):
        stack.reconcile([sleeper(tmp_path), sleeper(tmp_path, "other")])

        assert stack.apps["api"].port != stack.apps["other"].port

    def test_an_application_that_exits_at_once_is_reported_with_its_output(self, stack, tmp_path):
        root = tmp_path / "broken"
        root.mkdir()
        site = Site(
            name="broken",
            root=root,
            kind="python",
            command=f"{_quoted(sys.executable)} -c \"import sys; print('no module named app'); sys.exit(3)\"",
        )

        with pytest.raises(StackError) as excinfo:
            stack.reconcile([site])

        assert "exited as soon as it started" in str(excinfo.value)
        assert "no module named app" in str(excinfo.value)
        assert "broken" not in stack.apps

    def test_without_a_python_or_a_venv_it_says_which(self, tmp_path):
        registry = Runtimes(tmp_path / "empty.json")
        bare = Stack(supervisor=Supervisor(), runtimes=registry)

        with pytest.raises(StackError) as excinfo:
            bare._ensure_apps([sleeper(tmp_path)])

        assert "virtual environment" in str(excinfo.value)

    def test_a_restart_brings_up_a_new_process(self, stack, tmp_path):
        stack.reconcile([sleeper(tmp_path)])
        before = stack.supervisor.status()[0]["pid"]

        assert stack.restart_app("api") is True

        after = next(e for e in stack.supervisor.status() if e["name"] == "python-api")["pid"]
        assert after != before
        assert stack.restart_app("nothing") is False


def _gone(pid: int) -> bool:
    """Exited — or, on POSIX, a zombie waiting for init, which holds no port."""
    if not spawn.is_running(pid):
        return True

    if os.name == "nt":
        return False

    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()

    return not state or state.startswith("Z")


class TestStoppingATree:
    def test_a_reloaders_child_goes_with_it(self, tmp_path):
        """
        The case this exists for: a watcher whose child holds the port.

        Stopping only the watcher leaves the child listening, and the next
        start of the same site fails on a port nobody appears to hold. Run on
        both platforms, because they do it differently: a process group here,
        `taskkill /T` on Windows.
        """
        marker = tmp_path / "child.pid"
        script = (
            "import subprocess, sys, time;"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            f"open({str(marker)!r}, 'w').write(str(child.pid));"
            "time.sleep(60)"
        )
        process = spawn.start_child([sys.executable, "-c", script], group=True)

        deadline = time.monotonic() + 15

        while not marker.exists() or not marker.read_text():
            assert time.monotonic() < deadline, "the child never started"
            time.sleep(0.05)

        child = int(marker.read_text())

        spawn.stop_tree(process, timeout=5)

        deadline = time.monotonic() + 10

        while not _gone(child):
            assert time.monotonic() < deadline, "the child outlived its parent"
            time.sleep(0.05)


# -------------------------------------------------------------------------- api


class TestTheApi:
    def server(self):
        from portable.daemon.server import ControlServer

        return ControlServer()

    def test_a_command_makes_it_a_python_site(self, tmp_path):
        server = self.server()
        server._reconcile = dict

        result = server._site_add(
            {"name": "api", "root": str(tmp_path), "command": "uvicorn main:app --port {port}"}
        )

        assert result["kind"] == "python"
        assert result["port_in_command"] is True
        assert server.sites.get("api").is_python

    def test_a_django_project_needs_no_command(self, tmp_path):
        (tmp_path / "manage.py").write_text("", encoding="utf-8")
        server = self.server()
        server._reconcile = dict

        result = server._site_add({"name": "shop", "root": str(tmp_path), "python": ""})

        assert result["command_detected"] is True
        assert "runserver" in result["command"]
        assert server.sites.get("shop").python is None

    def test_a_python_site_is_served_from_the_directory_given(self, tmp_path):
        # No looking for public/: the application decides what it serves.
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "index.php").write_text("", encoding="utf-8")
        server = self.server()
        server._reconcile = dict

        result = server._site_add(
            {"name": "api", "root": str(tmp_path), "python": "3.13", "command": "python a.py"}
        )

        assert result["root"] == str(tmp_path)
        assert server.sites.get("api").python == "3.13"

    def test_php_and_python_together_are_refused(self, tmp_path):
        from portable.daemon.server import ApiError

        server = self.server()

        with pytest.raises(ApiError):
            server._site_add(
                {"name": "api", "root": str(tmp_path), "php": "8.4", "command": "python a.py"}
            )

    def test_removing_the_python_a_site_runs_on_is_refused(self, tmp_path):
        server = self.server()
        runtime = python_runtime(tmp_path)
        server.runtimes.add(runtime)
        server.sites.add(Site(name="api", root=tmp_path, kind="python", command="python a.py"))

        with pytest.raises(Exception) as excinfo:
            server._runtime_remove({"name": "python", "version": runtime.version})

        assert "api" in str(excinfo.value.args)

    def test_a_venv_made_from_it_counts_as_using_it(self, tmp_path):
        server = self.server()
        runtime = python_runtime(tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        make_venv(project, home=runtime.directory)
        server.runtimes.add(runtime)
        server.sites.add(Site(name="api", root=project, kind="python", command="python a.py"))

        from portable.daemon.server import ApiError

        with pytest.raises(ApiError):
            server._runtime_remove({"name": "python", "version": runtime.version})

    def test_a_venv_made_from_another_python_does_not(self, tmp_path):
        server = self.server()
        runtime = python_runtime(tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        make_venv(project, home=tmp_path / "some-other-python")
        server.runtimes.add(runtime)
        server.sites.add(Site(name="api", root=project, kind="python", command="python a.py"))

        result = server._runtime_remove({"name": "python", "version": runtime.version})

        assert result["deleted"] is True

    def test_the_environment_reaches_pips_scripts(self, tmp_path):
        server = self.server()
        runtime = python_runtime(tmp_path)
        server.runtimes.add(runtime)

        path = server._environment({})["path"]

        assert str(runtime.directory) in path
        assert str(runtime.directory / "Scripts") in path

    def test_a_line_is_passed_to_the_catalog_as_a_line(self, monkeypatch):
        # It was passed as `branch`, which no catalog accepts: `available php
        # 8.3` failed with a TypeError instead of listing the archive.
        asked = []
        server = self.server()

        def available(pages=None, line=None, limit=8):
            asked.append(line)

            return []

        monkeypatch.setattr(catalog, "available", available)

        server._available({"name": "python", "branch": "3.12"})

        assert asked == ["3.12"]
