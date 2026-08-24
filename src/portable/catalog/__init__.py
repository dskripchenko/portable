"""
Which builds exist, and where to get them.

A catalog answers one question — "give me PHP 8.4 for this machine" — and
returns something concrete enough to download and verify: a URL, a checksum and
the exact version that was resolved.

Nothing here downloads anything. Resolution and acquisition are separate so that
the first can be tested without the network doing any real work, and so that a
failure to resolve reads differently from a failure to fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Build:
    """One downloadable artifact, pinned."""

    name: str
    """What this is — `php`, `caddy`."""

    version: str
    """The concrete version resolved, never a range: `8.4.24`, not `8.4`."""

    url: str

    filename: str

    checksum: str | None = None
    """
    The publisher's own digest, lowercase hex, where one is offered.

    None means it cannot be verified from the listing alone. Recorded rather
    than papered over: a checksum we computed ourselves would only prove the
    file did not change between two of our own downloads.
    """

    algorithm: str = "sha256"
    """
    Which digest [checksum] is.

    Not a constant, because publishers disagree: php.net lists sha256 beside
    every archive, Caddy publishes sha512. Assuming one and being handed the
    other is a verification that silently never matches.
    """

    variant: str | None = None
    """The build flavour, where a project ships several: `nts-vs17-x64`."""

    @property
    def slug(self) -> str:
        """Directory name under `runtimes/<name>/`."""
        return f"{self.version}-{self.variant}" if self.variant else self.version


@dataclass(frozen=True)
class Offer:
    """
    One version a publisher currently has on offer.

    Deliberately thinner than `Build`: answering "what can I install" should not
    cost a request per version, and most of what `Build` carries — a URL, a
    digest, an archive name — is only knowable, or only worth knowing, once a
    choice has been made.
    """

    version: str

    note: str = ""
    """
    Why this entry is worth telling apart from its neighbours: `LTS`, `branch
    8.4`, `release candidate`. Empty when there is nothing to say, which is the
    common case and should read as such rather than as missing data.
    """


class CatalogError(RuntimeError):
    """A version was asked for that the publisher does not offer."""


def modules() -> dict[str, Any]:
    """
    Every runtime this tool can fetch, by name.

    One table, read by the command line for what it offers and by the daemon for
    what it accepts. They were two lists before, and they disagreed: the CLI
    advertised `install postgres`, `mariadb`, `node` and `redis` while the daemon
    knew only PHP and Caddy and refused all four — so the databases could not be
    installed at all, and `service add postgres` failed on a runtime there was no
    way to obtain.

    Adding a runtime is now one entry here, and `test_catalog` checks the two
    sides still agree.
    """
    from . import caddy, mariadb, node, php, postgres, redis

    return {
        "php": php,
        "caddy": caddy,
        "node": node,
        "postgres": postgres,
        "mariadb": mariadb,
        "redis": redis,
    }


def names() -> list[str]:
    return sorted(modules())


def module(name: str) -> Any:
    found = modules().get(name.lower())

    if found is None:
        raise CatalogError(f"Nothing is known about {name!r}. Installable: {', '.join(names())}.")

    return found
