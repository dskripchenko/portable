"""
Trusting the local certificate authority.

Caddy issues certificates from an authority of its own, which nobody trusts
until told to — so every https:// site is a warning page until its root is in
the store the browser reads.

The whole of this module is about doing that without administrator rights, which
is the constraint the tool exists under.
"""

from __future__ import annotations

import subprocess

import pytest

from portable import trust


class TestWhereItLooks:
    def test_the_root_lives_inside_the_installation(self):
        from portable import paths

        assert str(trust.root_certificate()).startswith(str(paths.root()))
        assert trust.root_certificate().name == "root.crt"

    def test_before_caddy_has_run_there_is_nothing_to_trust(self):
        # The authority is created on first use, not at install, so `trust`
        # before the first site has nothing to work with — and should say that
        # rather than fail obscurely somewhere inside certutil.
        assert trust.is_ready() is False

        with pytest.raises(trust.TrustFailed) as excinfo:
            trust.install()

        assert "Add a site" in str(excinfo.value)


class TestTheCommands:
    def test_windows_writes_to_the_users_store_and_not_the_machines(self, monkeypatch):
        """
        `-user` is the whole feature.

        Without it certutil writes to the machine store, which needs elevation —
        and this tool's first constraint is that it never asks for any.
        """
        monkeypatch.setattr(trust.platform, "system", lambda: "Windows")
        command = trust._install_command(trust.root_certificate())

        assert command[0] == "certutil"
        assert "-user" in command
        assert command.index("-user") < command.index("-addstore")

    def test_macos_uses_the_login_keychain(self, monkeypatch):
        # `-d` would mean the system keychain and a password prompt this tool
        # has no business raising.
        monkeypatch.setattr(trust.platform, "system", lambda: "Darwin")
        command = trust._install_command(trust.root_certificate())

        assert "login.keychain-db" in " ".join(command)
        assert "-d" not in command

    def test_an_unsupported_platform_says_where_the_certificate_is(self, monkeypatch):
        # Linux has no single answer: `update-ca-certificates` needs root, and
        # browsers read NSS databases that differ by distribution and profile.
        # Leaving somebody with the path is more use than leaving them with a
        # refusal.
        monkeypatch.setattr(trust.platform, "system", lambda: "Linux")

        with pytest.raises(trust.TrustFailed) as excinfo:
            trust._install_command(trust.root_certificate())

        assert str(trust.root_certificate()) in str(excinfo.value)


class TestWhenItGoesWrong:
    def test_a_hanging_prompt_is_named_as_one(self, monkeypatch):
        """
        Adding a root raises a confirmation, and it is easy to miss.

        "Timed out" alone sends people looking for a network problem. Naming the
        wrong operating system's dialog sends them further, which this used to
        do — it said Windows while running on macOS.
        """
        monkeypatch.setattr(trust.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            trust.subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("security", 120)),
        )

        with pytest.raises(trust.TrustFailed) as excinfo:
            trust._run(["security", "add-trusted-cert"], trust.root_certificate())

        assert "keychain password" in str(excinfo.value)
        assert "Windows" not in str(excinfo.value)

    def test_a_missing_tool_leaves_the_path_to_do_it_by_hand(self, monkeypatch):
        monkeypatch.setattr(
            trust.subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
        )

        with pytest.raises(trust.TrustFailed) as excinfo:
            trust._run(["certutil"], trust.root_certificate())

        assert str(trust.root_certificate()) in str(excinfo.value)

    def test_a_refusal_carries_what_the_tool_said(self, monkeypatch):
        monkeypatch.setattr(
            trust.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess([], 1, "", "Access is denied."),
        )

        with pytest.raises(trust.TrustFailed) as excinfo:
            trust._run(["certutil"], trust.root_certificate())

        assert "Access is denied." in str(excinfo.value)
