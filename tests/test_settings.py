"""
The port, and where a project's front controller is.

Both are small and both replace a decision the tool used to make silently: two
hardcoded ports with no way past them, and a document root taken literally even
when it plainly pointed at the wrong directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portable import ports, settings
from portable.sites import document_root


class TestThePort:
    def test_nothing_chosen_means_try_eighty_then_eight_thousand_and_eighty(self):
        assert settings.candidate_ports() == (80, 8080)

    def test_a_chosen_port_is_the_only_candidate(self):
        """
        No falling back to 8080 after somebody asked for 8888.

        The reason for choosing at all is that the defaults were not usable, so
        quietly landing on one of them puts the site at an address the person
        did not pick and was not told about.
        """
        settings.set_port(8888)

        assert settings.candidate_ports() == (8888,)

    def test_it_can_be_given_back(self):
        settings.set_port(8888)
        settings.set_port(None)

        assert settings.candidate_ports() == (80, 8080)

    def test_the_ports_this_tool_hands_out_are_refused(self):
        # A site on one of these takes a number from under a worker that is
        # about to ask for it — intermittently, under load, which is the worst
        # way to find out.
        for port in (ports.POOL_RANGE.start, ports.ADMIN_RANGE.start):
            with pytest.raises(settings.InvalidSetting):
                settings.set_port(port)

    def test_the_dynamic_range_is_refused(self):
        # Windows hands 49152-65535 out for outgoing connections, so one can be
        # taken between being chosen and being bound.
        with pytest.raises(settings.InvalidSetting) as excinfo:
            settings.set_port(50000)

        assert "49152" in str(excinfo.value)

    def test_a_settings_file_nobody_can_parse_does_not_stop_anything(self):
        from portable import paths

        paths.config_file().parent.mkdir(parents=True, exist_ok=True)
        paths.config_file().write_text("{ this is not json", encoding="utf-8")

        assert settings.candidate_ports() == (80, 8080)


class TestTheDocumentRoot:
    def project(self, tmp_path: Path, *files: str) -> Path:
        for name in files:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")

        return tmp_path

    def test_a_laravel_project_is_served_from_public(self, tmp_path):
        """
        The failure this prevents is not a 404.

        Pointing a site at the repository root serves the application's source
        over HTTP, `.env` included — and does it while appearing to merely not
        work, because the framework's router never runs.
        """
        root = self.project(tmp_path, "artisan", "public/index.php", ".env")
        found, detected = document_root(root)

        assert found == root / "public"
        assert detected is True

    def test_a_front_controller_at_the_root_is_left_alone(self, tmp_path):
        # WordPress, and anything else with index.php where it was pointed.
        root = self.project(tmp_path, "index.php", "wp-config.php", "public/logo.png")
        found, detected = document_root(root)

        assert found == root
        assert detected is False

    def test_a_public_directory_without_the_index_is_not_taken(self, tmp_path):
        """
        Existence of `public/` is not evidence.

        A project keeping its images in `public/` and its front controller
        elsewhere would otherwise be served from the wrong directory — the same
        mistake as the first test, arrived at from the other side.
        """
        root = self.project(tmp_path, "public/logo.png", "src/app.php")
        found, detected = document_root(root)

        assert found == root
        assert detected is False

    def test_the_older_conventions_are_known_too(self, tmp_path):
        # `web` is Craft and older Symfony, `webroot` is CakePHP. Directory
        # names rather than frameworks: nothing here identifies a framework, so
        # nothing here can identify one wrongly.
        for name in ("web", "webroot", "public_html"):
            root = self.project(tmp_path / name, f"{name}/index.php")
            found, detected = document_root(root)

            assert found == root / name, name
            assert detected is True

    def test_a_directory_of_static_files_is_served_as_given(self, tmp_path):
        # Perfectly ordinary. Guessing further would mean guessing.
        root = self.project(tmp_path, "index.html", "style.css")

        assert document_root(root) == (root, False)


class TestAPortThatCannotBeUsed:
    """
    A setting that fails to apply must not be stored.

    Keeping it leaves a machine that serves nothing at every start from now on,
    for a reason recorded only in the daemon's log — the port having been
    accepted, reported as set, and then quietly wrong. It happened here: `port
    8899` was taken by another program, the change failed, and the value stayed.
    """

    def server(self):
        from portable.daemon.server import ControlServer

        return ControlServer()

    def test_it_is_given_back_when_it_will_not_bind(self):
        from portable.daemon.server import ApiError

        server = self.server()
        settings.set_port(8123)

        def explode():
            raise ApiError(500, "no", "Caddy would not start on port 8899.")

        server._reconcile = explode

        with pytest.raises(ApiError):
            server._port_set({"port": 8899})

        assert settings.read().get("port") == 8123, "the broken port was kept"
        assert server.stack.candidate_ports == (8123,)

    def test_the_reason_reaches_status(self):
        # A daemon that is up, lists its sites and answers every question except
        # the one that matters is worse company than one that is down.
        server = self.server()
        server.stack.reconcile = lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("Caddy would not start on port 8899.")
        )

        server.restore()

        assert "8899" in server._status({})["router_error"]

    def test_a_working_restore_clears_it(self):
        server = self.server()
        server.stack.reconcile = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("nope"))
        server.restore()

        server.stack.reconcile = lambda *_a, **_k: {"sites": 0}
        server.restore()

        assert server._status({})["router_error"] is None


class TestChoosingAProxy:
    """
    A wrong proxy is found out at the next download, five attempts and two and
    a half minutes of connect timeouts later, as an error naming the host being
    fetched rather than the proxy in front of it. So it is checked while it can
    still be explained.
    """

    def test_it_is_remembered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PORTABLE_HOME", str(tmp_path))

        settings.set_proxy("http://proxy.corp:3128")

        assert settings.proxy() == "http://proxy.corp:3128"

    def test_clearing_goes_back_to_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PORTABLE_HOME", str(tmp_path))
        settings.set_proxy("http://proxy.corp:3128")

        settings.set_proxy(None)

        assert settings.proxy() is None

    def test_socks_is_refused_with_the_reason(self):
        # Python's standard library speaks to HTTP proxies. Accepting a SOCKS
        # address would fail at the first download and blame the host.
        with pytest.raises(settings.InvalidSetting) as refused:
            settings.check_proxy("socks5://127.0.0.1:1080")

        assert "SOCKS" in str(refused.value)

    def test_a_bare_host_is_refused(self):
        with pytest.raises(settings.InvalidSetting):
            settings.check_proxy("proxy.corp:3128")

    def test_a_missing_port_is_refused_as_the_typo_it_usually_is(self):
        with pytest.raises(settings.InvalidSetting) as refused:
            settings.check_proxy("http://proxy.corp")

        assert "3128" in str(refused.value), "it should say what a port looks like"

    def test_credentials_survive_but_the_password_does_not_get_printed(self):
        # They are ordinary in this world, and they end up in `version --json`,
        # in the log, and in whatever gets pasted into an issue.
        settings.check_proxy("http://bob:hunter2@proxy.corp:3128")

        shown = settings.without_password("http://bob:hunter2@proxy.corp:3128")

        assert "hunter2" not in shown
        assert "bob" in shown and "proxy.corp:3128" in shown

    def test_one_without_a_password_is_shown_whole(self):
        assert (
            settings.without_password("http://proxy.corp:3128") == "http://proxy.corp:3128"
        )

    def test_nothing_is_the_empty_string_rather_than_the_word_none(self):
        assert settings.without_password(None) == ""
