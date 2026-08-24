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


class CatalogError(RuntimeError):
    """A version was asked for that the publisher does not offer."""
