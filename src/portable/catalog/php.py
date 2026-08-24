"""
PHP for Windows, from the index php.net publishes itself.

Windows is the platform where PHP is easy to obtain: official builds, a
machine-readable index, checksums included. On macOS and Linux none of that
exists — which is one of the reasons this tool starts with Windows.

The build we want is always **non-thread-safe**. PHP's FastCGI SAPI runs one
request per process, so thread safety buys nothing and costs perhaps a fifth of
the performance. The thread-safe builds exist for Apache with a threaded MPM,
which is not how anything here runs.
"""

from __future__ import annotations

import json

from .. import net
from . import Build, CatalogError

INDEX_URL = "https://downloads.php.net/~windows/releases/releases.json"
DOWNLOAD_BASE = "https://downloads.php.net/~windows/releases"

#: The compiler each branch is built with. PHP for Windows pins one per branch,
#: and picking the wrong one is not a slow path but an unloadable extension:
#: a `php_redis.dll` built for vs16 will not load into a vs17 binary.
#:
#: Read from the index rather than hardcoded — the branch that introduces vs18
#: should not require a release of this tool.
_ARCH = "x64"


def _fetch_index(url: str = INDEX_URL) -> dict:
    return json.loads(net.read_text(url))


def branches(index: dict | None = None) -> list[str]:
    """Every branch the index offers, newest first: `['8.5', '8.4', ...]`."""
    index = index if index is not None else _fetch_index()

    return sorted(
        (key for key in index if _looks_like_branch(key)),
        key=_version_key,
        reverse=True,
    )


def resolve(version: str = "latest", index: dict | None = None) -> Build:
    """
    A concrete build for a requested version.

    Accepts a branch (`8.4`), an exact version (`8.4.24`) or `latest`. Only the
    releases currently published are searched: the index lists one patch release
    per branch, and older ones move to an archive this does not read. Asking for
    a superseded patch version therefore fails, and says so, rather than
    silently handing back a different build than the one that was named.
    """
    index = index if index is not None else _fetch_index()
    available = branches(index)

    if not available:
        raise CatalogError("The PHP index lists no branches at all.")

    if version == "latest":
        branch = available[0]
    elif version in index and _looks_like_branch(version):
        branch = version
    else:
        # An exact version: find the branch whose current release matches.
        branch = next(
            (
                name
                for name in available
                if str(index[name].get("version", "")) == version
            ),
            "",
        )

        if not branch:
            current = ", ".join(f"{name} ({index[name].get('version')})" for name in available)
            raise CatalogError(
                f"PHP {version} is not among the currently published releases. "
                f"Available: {current}. Superseded patch releases live in an "
                f"archive this index does not cover."
            )

    entry = index[branch]
    resolved = str(entry.get("version") or "")
    variant = _variant_of(entry, branch)
    archive = entry[variant]["zip"]

    return Build(
        name="php",
        version=resolved,
        url=f"{DOWNLOAD_BASE}/{archive['path']}",
        filename=archive["path"],
        checksum=archive.get("sha256"),
        algorithm="sha256",
        variant=variant,
    )


def _variant_of(entry: dict, branch: str) -> str:
    """
    The non-thread-safe x64 flavour of whatever compiler this branch uses.

    The compiler is discovered rather than assumed: branches move from vs16 to
    vs17 and will move again, and a hardcoded list would make that a release of
    this tool instead of a fact about the index.
    """
    candidates = sorted(
        (key for key in entry if key.startswith("nts-") and key.endswith(f"-{_ARCH}")),
        reverse=True,
    )

    if not candidates:
        offered = ", ".join(sorted(key for key in entry if "-" in key))
        raise CatalogError(
            f"PHP {branch} offers no non-thread-safe {_ARCH} build. Offered: {offered}."
        )

    return candidates[0]


def _looks_like_branch(key: str) -> bool:
    """`8.4` yes; `source`, `test_pack` no."""
    parts = key.split(".")

    return len(parts) == 2 and all(part.isdigit() for part in parts)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
