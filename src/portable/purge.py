"""
Finding everything this tool has left anywhere, so it can be taken back.

The README has promised from the first day that deleting one directory removes
the tool completely. That was true and quietly stopped being so: the data
directory can be moved elsewhere, `path add` writes to the registry, `trust`
puts a certificate in the user's store, and `upgrade` leaves the previous
version beside the new one.

Four things, three of which nobody would remember. So they are found and listed
rather than described in a document somebody would have to read at exactly the
right moment.

What is deliberately **not** removed here is the bundle itself, and that is not
squeamishness: this code is running from inside it. Windows will not delete a
directory holding a running executable, and a command that half-deleted itself
would be worse than one that says what is left. After this there is exactly one
thing left — the folder you are standing in — which is the promise the README
made, restored.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import paths, trust, userpath


@dataclass
class Trace:
    """One thing left outside the bundle, and how to take it back."""

    kind: str
    what: str
    detail: str
    remove: Callable[[], None]

    #: Bytes, when it is something on disk worth measuring.
    size: int = 0


def find(bundle: Path | None = None) -> list[Trace]:
    """
    Everything outside the bundle that this installation has put somewhere.

    Only what is really there. Listing a certificate that was never trusted, or
    a PATH entry that was never added, would make the answer to "what will this
    delete" longer than the truth and harder to trust.
    """
    bundle = bundle or paths.bundle()
    traces: list[Trace] = []

    home = paths.root()

    if home.is_dir():
        traces.append(
            Trace(
                kind="data",
                what=str(home),
                detail="runtimes, databases, logs, certificates",
                size=_size(home),
                remove=lambda: shutil.rmtree(home, ignore_errors=True),
            )
        )

    traces.extend(_certificate())
    traces.extend(_path_entry(bundle))
    traces.extend(_previous_versions(bundle))

    return traces


def _certificate() -> list[Trace]:
    """
    The local authority's root, if `trust` ever put it in the user's store.

    Detected by the file Caddy generates rather than by asking the store: the
    file existing means an authority was created, which is the only way anything
    could have been trusted, and asking the store costs a subprocess for a
    question usually answered "no".
    """
    if not trust.is_ready():
        return []

    return [
        Trace(
            kind="certificate",
            what="the local certificate authority",
            detail="removed from your certificate store, if it is in it",
            remove=_forget_certificate,
        )
    ]


def _forget_certificate() -> None:
    try:
        trust.forget()
    except trust.TrustFailed:
        # It was never trusted, or has already been removed by hand. Neither is
        # a reason to abandon the rest of the removal.
        pass


def _path_entry(bundle: Path | None) -> list[Trace]:
    if bundle is None or not userpath.WINDOWS:
        return []

    try:
        if not userpath.read().has(bundle):
            return []
    except userpath.PathError:
        return []

    return [
        Trace(
            kind="path",
            what=f"{bundle} on your PATH",
            detail="your own environment, not the machine's",
            remove=lambda: userpath.remove(bundle),
        )
    ]


def _previous_versions(bundle: Path | None) -> list[Trace]:
    """Copies `upgrade` kept beside the current one."""
    if bundle is None:
        return []

    found = []

    for candidate in sorted(bundle.parent.glob(f"{bundle.name}.*.old")):
        if candidate.is_dir():
            found.append(
                Trace(
                    kind="previous",
                    what=str(candidate),
                    detail="kept by an upgrade",
                    size=_size(candidate),
                    remove=lambda path=candidate: shutil.rmtree(path, ignore_errors=True),
                )
            )

    return found


def _size(directory: Path) -> int:
    total = 0

    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            # Something vanished or is unreadable while being counted. A size is
            # for the reader's benefit, and an approximate one still is.
            continue

    return total


def megabytes(size: int) -> str:
    return f"{size // 1048576} MB" if size else ""
