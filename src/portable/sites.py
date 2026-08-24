"""
What is served, and from where.

A site is three facts: a name, a directory, and which PHP answers for it. The
hostname follows from the name and is not stored — deriving it means it can
never disagree with the name it was derived from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

#: A label that is safe in a hostname and on a filesystem. Deliberately narrow:
#: a name containing a dot would produce `a.b.localhost` and quietly claim a
#: hostname the person did not ask for.
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidSite(ValueError):
    """The name or the directory will not do."""


@dataclass(frozen=True)
class Site:
    name: str
    root: Path
    php: str | None = None
    """
    Which PHP version, or None for whichever is newest.

    None rather than the resolved version on purpose: a site pinned to nothing
    should follow the machine when a newer PHP is installed, and storing the
    resolved value at creation time would freeze it silently.
    """

    index: str = "index.php"

    @property
    def hostname(self) -> str:
        return f"{self.name}.localhost"


def validate(name: str, root: Path) -> None:
    if not NAME.match(name):
        raise InvalidSite(
            f"{name!r} will not work as a site name. Use lowercase letters, digits "
            f"and hyphens — the name becomes a hostname, and a dot in it would "
            f"claim a different one than you asked for."
        )

    if not root.is_dir():
        raise InvalidSite(f"{root} is not a directory.")


class Registry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.root() / "sites.json")

    def all(self) -> list[Site]:
        if not self.path.exists():
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        return [
            Site(
                name=entry["name"],
                root=Path(entry["root"]),
                php=entry.get("php"),
                index=entry.get("index", "index.php"),
            )
            for entry in raw
        ]

    def get(self, name: str) -> Site | None:
        return next((site for site in self.all() if site.name == name), None)

    def add(self, site: Site) -> None:
        validate(site.name, site.root)

        entries = [existing for existing in self.all() if existing.name != site.name]
        entries.append(site)
        self._write(entries)

    def remove(self, name: str) -> bool:
        entries = self.all()
        remaining = [site for site in entries if site.name != name]

        if len(remaining) == len(entries):
            return False

        self._write(remaining)

        return True

    def _write(self, sites: list[Site]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [
                    {
                        "name": site.name,
                        "root": str(site.root),
                        "php": site.php,
                        "index": site.index,
                    }
                    for site in sorted(sites, key=lambda site: site.name)
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
