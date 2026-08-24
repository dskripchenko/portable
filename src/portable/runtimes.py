"""
What is installed, and where its executables are.

Two kinds of entry live here and are treated alike: runtimes this tool
downloaded and unpacked, and runtimes that were already on the machine and were
pointed at. The supervisor cannot tell them apart and does not need to — it
needs a path and a version. Only responsibility for updates differs.

That is not a fallback for when downloading fails. Statically built PHP cannot
load extensions at runtime, so anyone needing one the prebuilt binaries lack has
exactly one way to stay unblocked, and it is this.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths

#: Executable names per runtime, in the order they should be looked for. The
#: first that exists wins.
#:
#: `php-cgi` and not `php-fpm`: FPM is a Unix-only SAPI and the Windows build
#: does not contain it. That single fact is why this tool needs a process pool
#: at all — see `pool.py`.
EXECUTABLES = {
    "php": ("php-cgi.exe", "php-cgi"),
    "php-cli": ("php.exe", "php"),
    "caddy": ("caddy.exe", "caddy"),
}


class NotInstalled(RuntimeError):
    """Nothing on this machine provides what was asked for."""


@dataclass(frozen=True)
class Installed:
    """One runtime that can be started."""

    name: str
    version: str
    directory: Path
    managed: bool
    """
    True when this tool downloaded it and can replace it.

    False for something discovered on the machine: it is used, never modified,
    and never deleted. Removing a PHP that Homebrew or another tool installed
    would be a surprising thing for a program called `portable` to do.
    """

    variant: str | None = None

    def executable(self, kind: str | None = None) -> Path:
        """
        The binary to run, by kind — `php`, `php-cli`, `caddy`.

        Searched rather than assumed: publishers put binaries at different
        depths, and a hardcoded relative path breaks silently on the first
        archive that disagrees.
        """
        names = EXECUTABLES.get(kind or self.name)

        if not names:
            raise NotInstalled(f"Nothing is known about executables for {kind or self.name!r}.")

        for candidate in names:
            direct = self.directory / candidate

            if direct.is_file():
                return direct

        for candidate in names:
            found = next(self.directory.rglob(candidate), None)

            if found is not None:
                return found

        raise NotInstalled(
            f"{self.name} {self.version} is installed at {self.directory}, but none of "
            f"{', '.join(names)} is in it. The archive may have unpacked into an "
            f"unexpected shape."
        )


class Registry:
    """
    Everything installed, remembered across runs.

    Kept as a file rather than rediscovered by walking the directories: a
    discovered runtime lives wherever its owner put it, and there is nothing to
    walk.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.root() / "runtimes.json")

    def all(self) -> list[Installed]:
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        return [
            Installed(
                name=entry["name"],
                version=entry["version"],
                directory=Path(entry["directory"]),
                managed=bool(entry.get("managed", False)),
                variant=entry.get("variant"),
            )
            for entry in raw
            # An entry whose directory has gone — a discovered runtime whose
            # owner uninstalled it — is dropped rather than offered. Offering it
            # produces a failure at start time instead of at list time.
            if Path(entry["directory"]).is_dir()
        ]

    def of(self, name: str) -> list[Installed]:
        return [entry for entry in self.all() if entry.name == name]

    def get(self, name: str, version: str | None = None) -> Installed:
        candidates = self.of(name)

        if not candidates:
            raise NotInstalled(f"No {name} is installed. Add one with `portable install {name}`.")

        if version is None:
            # Newest by version, not by insertion: a machine that installed 8.4
            # after 8.5 should still default to 8.5.
            return max(candidates, key=lambda entry: _version_key(entry.version))

        exact = [entry for entry in candidates if entry.version == version]

        if exact:
            return exact[0]

        # A branch: `8.4` matching `8.4.24`.
        prefixed = [entry for entry in candidates if entry.version.startswith(f"{version}.")]

        if prefixed:
            return max(prefixed, key=lambda entry: _version_key(entry.version))

        available = ", ".join(sorted(entry.version for entry in candidates))

        raise NotInstalled(f"No {name} {version} is installed. Present: {available}.")

    def add(self, entry: Installed) -> None:
        entries = [
            existing
            for existing in self.all()
            if not (existing.name == entry.name and existing.version == entry.version)
        ]
        entries.append(entry)
        self._write(entries)

    def remove(self, name: str, version: str) -> None:
        self._write(
            [
                entry
                for entry in self.all()
                if not (entry.name == name and entry.version == version)
            ]
        )

    def _write(self, entries: list[Installed]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = []

        for entry in entries:
            record = asdict(entry)
            record["directory"] = str(entry.directory)
            payload.append(record)

        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _version_key(version: str) -> tuple[int, ...]:
    parts = []

    for piece in version.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        parts.append(int(digits) if digits else 0)

    return tuple(parts)
