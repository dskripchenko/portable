"""
Databases: declared, initialised once, kept when removed.

The property that gets the most attention here is the one with no undo. A
database holds work, `portable service remove` is a command somebody will run
casually, and if it took the data directory with it there would be nothing to
say afterwards.

The catalogs are checked against indexes captured from the publishers. Both had
a detail that a hand-written fixture would have missed: MariaDB advertises its
downloads over plain HTTP and files its digest under `sha256sum`, not `sha256`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from portable.catalog import mariadb, postgres
from portable.services import (
    DEFAULT_PORTS,
    SUPERUSERS,
    InvalidService,
    Registry,
    Service,
    init_command,
    start_command,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mariadb_index() -> dict:
    return json.loads((FIXTURES / "mariadb-11.4.json").read_text(encoding="utf-8"))


@pytest.fixture
def postgres_release() -> dict:
    return json.loads((FIXTURES / "postgres-release.json").read_text(encoding="utf-8"))


class TestMariadbCatalog:
    def test_downloads_are_upgraded_to_tls(self, mariadb_index):
        # The API advertises `http://`. The digest arrives over https and makes
        # the archive verifiable either way, but there is no reason to accept a
        # plaintext transfer when the same path answers over TLS — and it
        # redirects to a community mirror, so the bytes come from a third party.
        build = mariadb.resolve(release=mariadb_index)

        assert build.url.startswith("https://")

    def test_the_digest_is_read_from_the_key_that_holds_it(self, mariadb_index):
        # `sha256sum`, not `sha256`. Reading the wrong one yields None, which
        # reads as "the publisher offers no checksum" — a silent downgrade from
        # verified to unverified.
        build = mariadb.resolve(release=mariadb_index)

        assert build.checksum is not None, "the digest was not found"
        assert len(build.checksum) == 64
        assert build.algorithm == "sha256"

    def test_it_takes_the_server_archive_and_not_the_debug_symbols(self, mariadb_index):
        build = mariadb.resolve(release=mariadb_index)

        assert build.filename.endswith("winx64.zip")
        assert "debug" not in build.filename


class TestPostgresCatalog:
    def test_it_resolves_the_windows_archive(self, postgres_release):
        build = postgres.resolve(release=postgres_release)

        assert build.filename.endswith("-x86_64-pc-windows-msvc.tar.gz")

    def test_it_is_a_tarball_and_the_unpacker_must_handle_that(self, postgres_release):
        # Everything else here ships zip. Assuming so would fail on this one
        # archive, at unpack time, after a 50 MB download.
        assert postgres.resolve(release=postgres_release).filename.endswith(".tar.gz")

    def test_the_absent_digest_is_recorded_rather_than_glossed_over(self, postgres_release):
        # The project publishes no checksums. Saying so lets a listing show
        # which runtimes are verified and which merely arrived.
        assert postgres.resolve(release=postgres_release).checksum is None


class TestDeclaration:
    def test_an_unknown_kind_is_refused(self, tmp_path):
        registry = Registry(tmp_path / "services.json")

        with pytest.raises(InvalidService) as excinfo:
            registry.add(Service(name="db", kind="oracle"))

        assert "postgres" in str(excinfo.value)

    def test_a_name_that_is_not_a_directory_name_is_refused(self, tmp_path):
        registry = Registry(tmp_path / "services.json")

        with pytest.raises(InvalidService):
            registry.add(Service(name="../escape", kind="postgres"))

    def test_removing_returns_what_was_removed_so_the_caller_can_say_where_the_data_is(
        self, tmp_path
    ):
        registry = Registry(tmp_path / "services.json")
        registry.add(Service(name="postgres", kind="postgres"))

        removed = registry.remove("postgres")

        assert removed is not None
        assert removed.data.name == "postgres"

    def test_an_empty_data_directory_does_not_count_as_initialised(self, tmp_path, monkeypatch):
        # What an interrupted `initdb` leaves behind. Treating it as done makes
        # the server start on it and report something about corruption.
        monkeypatch.setenv("PORTABLE_HOME", str(tmp_path))
        service = Service(name="postgres", kind="postgres")
        service.data.mkdir(parents=True)

        assert not service.initialised

        (service.data / "PG_VERSION").write_text("18", encoding="utf-8")

        assert service.initialised


class TestCommands:
    def paths_for(self, kind: str) -> dict:
        return {role: Path(f"/bin/{role}") for role in ("server", "initdb", "install", "client")}

    def test_postgres_is_initialised_with_a_fixed_collation(self):
        # `C` rather than the machine's locale. A database whose collation
        # follows the developer's regional settings sorts differently from
        # production and finds out in a test that passes for one person.
        command = init_command("postgres", self.paths_for("postgres"), Path("/data"))

        assert "--locale=C" in command
        assert "--encoding=UTF8" in command

    def test_the_superuser_is_the_one_each_kind_actually_creates(self):
        # A person has to connect. Making them know two conventions is a way of
        # being unhelpful for free.
        assert SUPERUSERS["postgres"] == "postgres"
        assert SUPERUSERS["mariadb"] == "root"

    def test_both_kinds_bind_the_loopback_only(self):
        # Trust authentication and a network-reachable port is how a laptop on
        # conference wifi becomes somebody else's. The binding is the protection.
        postgres_argv = start_command("postgres", self.paths_for("postgres"), Path("/d"), 5432)
        mariadb_argv = start_command("mariadb", self.paths_for("mariadb"), Path("/d"), 3306)

        assert "127.0.0.1" in postgres_argv
        assert any("bind-address=127.0.0.1" in argument for argument in mariadb_argv)

    def test_the_conventional_ports_are_the_defaults(self):
        # So a connection string copied from anywhere works unchanged.
        assert DEFAULT_PORTS == {"postgres": 5432, "mariadb": 3306}
