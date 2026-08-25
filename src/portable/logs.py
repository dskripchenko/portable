"""
Watching what the supervised processes are saying.

Every process this tool starts writes to its own file under `logs/`, and until
now the only way to read one was to know its name and open it. That is a poor
answer to "what is it doing", which is the question actually being asked when
something is slow, or wrong, or merely interesting.

Following a file rather than streaming from the daemon, deliberately. The daemon
does not read these — it hands each child a file descriptor and steps out of the
way, which is why a worker that dies mid-sentence still leaves the sentence. Any
route through the daemon would be this same reading, with a socket in the middle
and the daemon obliged to stay alive for it.

Rotation is not handled because there is none: a `php-cgi` worker writes a few
lines per restart and Caddy writes JSON per configuration change, so these are
files that grow slowly and are deleted when the installation is.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import paths

#: How often a followed file is checked for new bytes.
#:
#: A tenth of a second reads as immediate and costs nothing measurable. Anything
#: shorter is a busy loop dressed up as attentiveness.
INTERVAL = 0.1

#: Words that mark a line as worth noticing, in the output of things this tool
#: starts. Caddy writes structured JSON with a `level`; PHP and the databases
#: write prose that varies by version and by locale.
#:
#: Matched loosely on purpose. Getting it wrong colours a line that did not need
#: it, which costs nothing; a scheme precise enough to never do that would miss
#: the failures worth seeing.
PATTERNS = (
    (re.compile(r'"level":"(error|fatal)"|\b(error|fatal|panic|failed|refused)\b', re.IGNORECASE), "error"),
    (re.compile(r'"level":"warn"|\b(warn|warning|deprecated)\b', re.IGNORECASE), "warn"),
)

COLOURS = {"error": "\033[31m", "warn": "\033[33m", "": ""}
RESET = "\033[0m"


@dataclass(frozen=True)
class Source:
    """One log file, and the name it is known by."""

    name: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.exists else 0


def available() -> list[Source]:
    """
    Every log there is, newest activity first.

    Discovered from the directory rather than derived from what is running. A
    process that died an hour ago is exactly what somebody wants to read, and it
    is no longer in `status` to be asked about.
    """
    directory = paths.logs()

    if not directory.is_dir():
        return []

    found = [
        Source(name=entry.stem, path=entry)
        for entry in directory.iterdir()
        if entry.is_file() and entry.suffix == ".log"
    ]

    return sorted(found, key=lambda source: source.path.stat().st_mtime, reverse=True)


def resolve(name: str | None) -> list[Source]:
    """
    The logs a name refers to, or all of them.

    A name matches a whole log or the start of one, so `php` follows every
    worker of every version at once — which is how somebody thinks about it,
    rather than as `php-8.4.24-1` through `php-8.4.24-4`.
    """
    everything = available()

    if not name:
        return everything

    exact = [source for source in everything if source.name == name]

    if exact:
        return exact

    return [source for source in everything if source.name.startswith(name)]


def tail(source: Source, lines: int) -> list[str]:
    """The last [lines] of a file, without reading all of it."""
    if not source.exists:
        return []

    # Reading from the end in blocks. These files are small, but "small" is a
    # property of today's usage rather than a guarantee, and a log big enough to
    # matter is exactly the one somebody is trying to read.
    with source.path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        block = 8192
        found: list[bytes] = []
        position = end

        while position > 0 and len(found) <= lines:
            step = min(block, position)
            position -= step
            handle.seek(position)
            found = handle.read(end - position).splitlines()

    return [line.decode("utf-8", errors="replace") for line in found[-lines:]]


def follow(sources: list[Source], stop: object = None) -> Iterator[tuple[str, str]]:
    """
    Yield `(name, line)` as it is written, for as long as the caller reads.

    Files that do not exist yet are watched anyway. A pool being restarted takes
    its logs away and puts them back, and a follower that gave up on the gap
    would stop precisely when something interesting was happening.
    """
    positions = {source.name: source.size for source in sources}

    while stop is None or not stop.is_set():
        for source in sources:
            if not source.exists:
                continue

            size = source.size
            seen = positions.get(source.name, 0)

            if size < seen:
                # Truncated or replaced. Starting from the beginning is right:
                # what is there now is new.
                seen = 0

            if size == seen:
                continue

            with source.path.open("rb") as handle:
                handle.seek(seen)
                data = handle.read(size - seen)

            positions[source.name] = size

            for line in data.decode("utf-8", errors="replace").splitlines():
                yield source.name, line

        time.sleep(INTERVAL)


def severity(line: str) -> str:
    for pattern, level in PATTERNS:
        if pattern.search(line):
            return level

    return ""


def render(name: str, line: str, width: int = 0, colour: bool = True) -> str:
    """One line, labelled with which process said it."""
    label = f"{name:<{width}} | " if width else f"{name} | "

    if not colour:
        return f"{label}{line}"

    return f"\033[2m{label}{RESET}{COLOURS[severity(line)]}{line}{RESET}"
