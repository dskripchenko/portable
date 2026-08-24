"""
Every test runs against its own installation directory.

Without this a test that calls anything using the default layout writes into the
real `~/.portable` — the developer's own working installation. That is the kind
of thing which passes for months and then, on the one run that goes wrong,
deletes a runtime somebody was using.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PORTABLE_HOME", str(home))

    return home
