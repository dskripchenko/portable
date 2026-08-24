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


#: Subdirectories a PHP project puts its front controller in, most common first.
#:
#: A list of directory names rather than a list of frameworks, and the
#: difference matters: `public` is Laravel, Symfony, Laminas and half of
#: everything else, `web` is Craft and older Symfony, `webroot` is CakePHP — and
#: the next framework to appear will use one of these without this tool needing
#: to have heard of it. Nothing here identifies a framework, so nothing here can
#: identify one wrongly.
FRONT_CONTROLLER_DIRECTORIES = ("public", "web", "webroot", "public_html", "htdocs", "www")


def document_root(given: Path, index: str = "index.php") -> tuple[Path, bool]:
    """
    Where a project's front controller actually is. Returns it and whether it
    was found rather than given.

    Pointing a site at the repository root instead of `public/` serves the
    source of the application over HTTP — `.env` included — and does it while
    appearing to work, because the framework's own router never runs and the
    browser shows a directory listing or a blank page.

    Two rules keep this from ever being clever at somebody's expense:

    - **A directory that has the index file is used as given.** WordPress and
      anything with a front controller at its root are answered correctly by
      doing nothing.
    - **Only one level down, only these names, and only when the file is really
      there.** Existence of a `public` directory is not enough; it must hold the
      index. A project with a `public` full of images and its front controller
      at the root would otherwise be served from the wrong place — which is the
      same mistake, arrived at from the other side.
    """
    if (given / index).is_file():
        return given, False

    for name in FRONT_CONTROLLER_DIRECTORIES:
        candidate = given / name

        if (candidate / index).is_file():
            return candidate, True

    # No front controller anywhere obvious. Left exactly as given: a directory
    # of static files is a perfectly ordinary thing to serve, and guessing
    # further would mean guessing.
    return given, False


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
