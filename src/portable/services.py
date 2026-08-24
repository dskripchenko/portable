"""
Databases: declared, initialised once, then supervised like anything else.

They differ from PHP and the router in one way that shapes everything here: a
database has **state on disk**, and that state has to be created before the
server will start at all. `initdb` for PostgreSQL, `mariadb-install-db` for
MariaDB — one-time work whose result is a directory nobody may delete casually,
because it holds the data.

Which leads to the rule this module is built around: **removing a service never
removes its data.** `portable service remove postgres` stops the server and
forgets the declaration; the directory stays. Anything else turns a routine
command into a way of losing work, and there is no undo for that.

Everything binds to `127.0.0.1` and nothing else. A development database with
trust authentication reachable from the network is how a laptop on a conference
wifi becomes somebody else's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

KINDS = ("postgres", "mariadb", "redis")

#: Default ports. The conventional ones, so a connection string copied from
#: anywhere works — and taken as a preference rather than a promise: if
#: something already holds one, the next free port is used and reported.
DEFAULT_PORTS = {"postgres": 5432, "mariadb": 3306, "redis": 6379}

#: The administrative account each kind creates. Named here because a person
#: needs it to connect and should not have to know two conventions.
SUPERUSERS = {"postgres": "postgres", "mariadb": "root", "redis": ""}
"""Redis has no accounts by default, and an empty string says so rather than
inventing one for the sake of a uniform table."""

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


class InvalidService(ValueError):
    """The declaration will not do."""


@dataclass(frozen=True)
class Service:
    """One database instance."""

    name: str
    """`postgres` by default — the kind. A second instance of the same kind
    takes another name, and its own data directory follows from it."""

    kind: str
    version: str | None = None
    port: int | None = None
    """None until one is allocated, then remembered: a connection string that
    changed on every restart would be useless."""

    @property
    def data(self) -> Path:
        return paths.root() / "data" / self.name

    @property
    def superuser(self) -> str:
        return SUPERUSERS[self.kind]

    @property
    def needs_init(self) -> bool:
        """
        Whether this kind has to be prepared before it will start.

        Redis does not: it writes its dump into whatever directory it is pointed
        at and creates it if need be. Running a preparation step for it would
        mean inventing one.
        """
        return self.kind in ("postgres", "mariadb")

    @property
    def initialised(self) -> bool:
        """
        Whether the data directory is ready.

        A directory that exists but is empty counts as not initialised: that is
        what an interrupted `initdb` leaves behind, and starting a server on it
        fails in a way that reads like corruption.
        """
        if not self.needs_init:
            return True

        return self.data.is_dir() and any(self.data.iterdir())


def validate(service: Service) -> None:
    if service.kind not in KINDS:
        raise InvalidService(f"{service.kind!r} is not a database this knows. Known: {', '.join(KINDS)}.")

    if not NAME.match(service.name):
        raise InvalidService(
            f"{service.name!r} will not work as a service name — lowercase letters, "
            f"digits and hyphens. It becomes a directory name."
        )


def init_command(kind: str, executables: dict[str, Path], data: Path) -> list[str]:
    """
    The one-time command that creates the data directory.

    Authentication is deliberately trusting, and deliberately paired with
    binding to the loopback: this is a database for a developer's own machine,
    and a password prompt on every `psql` buys nothing when anything running as
    that user could read the data directory anyway. The protection is that
    nothing outside the machine can reach it.
    """
    if kind == "postgres":
        return [
            str(executables["initdb"]),
            "-D", str(data),
            "-U", SUPERUSERS["postgres"],
            "--auth=trust",
            "--encoding=UTF8",
            # `C` rather than the machine's locale: a database whose collation
            # depends on the developer's regional settings sorts differently from
            # production, and finds out in a test that passes for one person.
            "--locale=C",
        ]

    return [
        str(executables["install"]),
        f"--datadir={data}",
    ]


def start_command(kind: str, executables: dict[str, Path], data: Path, port: int) -> list[str]:
    if kind == "postgres":
        return [
            str(executables["server"]),
            "-D", str(data),
            "-p", str(port),
            "-h", "127.0.0.1",
        ]

    if kind == "redis":
        return [
            str(executables["server"]),
            "--port", str(port),
            "--bind", "127.0.0.1",
            "--dir", str(data),
            # Foreground: the supervisor owns the lifetime, and a server that
            # daemonises itself becomes a process nothing here can stop.
            "--daemonize", "no",
        ]

    return [
        str(executables["server"]),
        f"--datadir={data}",
        f"--port={port}",
        "--bind-address=127.0.0.1",
        # Otherwise MariaDB also opens a named pipe or a shared-memory channel,
        # which is one more way in than was asked for.
        "--skip-name-resolve",
    ]


#: Which binary is which, per kind. Looked up through `Installed.executable`,
#: so the archive's own shape does not matter.
EXECUTABLES = {
    "postgres": {"server": "postgres", "initdb": "initdb", "client": "psql"},
    "mariadb": {"server": "mariadbd", "install": "mariadb-install-db", "client": "mariadb"},
    "redis": {"server": "redis-server", "client": "redis-cli"},
}


class Registry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.root() / "services.json")

    def all(self) -> list[Service]:
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        return [
            Service(
                name=entry["name"],
                kind=entry["kind"],
                version=entry.get("version"),
                port=entry.get("port"),
            )
            for entry in raw
        ]

    def get(self, name: str) -> Service | None:
        return next((service for service in self.all() if service.name == name), None)

    def add(self, service: Service) -> None:
        validate(service)
        entries = [existing for existing in self.all() if existing.name != service.name]
        entries.append(service)
        self._write(entries)

    def remove(self, name: str) -> Service | None:
        """
        Forget a service. Its data directory is left alone — see the module note.
        """
        entries = self.all()
        found = next((service for service in entries if service.name == name), None)

        if found is None:
            return None

        self._write([service for service in entries if service.name != name])

        return found

    def _write(self, services: list[Service]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [
                    {
                        "name": service.name,
                        "kind": service.kind,
                        "version": service.version,
                        "port": service.port,
                    }
                    for service in sorted(services, key=lambda service: service.name)
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
