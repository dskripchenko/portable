"""
PHP extensions: what a build ships, what is switched on, and switching it.

Windows PHP is not built the way Linux distributions build it. The archive from
php.net carries every extension it supports as a separate `php_<name>.dll` in
`ext/`, all of them present and none of them loaded — there is no package to
install, only a line to write. So `ext enable gd` is not a download; it is one
line in a file, and this module is mostly about editing that file carefully.

Carefully, because the file is not ours. `php.ini` is generated once when a PHP
is installed and then left alone forever, on the grounds that somebody will edit
it — that is the point of having it on disk. Regenerating it to toggle a switch
would silently discard their work, so every operation here is a surgical edit
that preserves everything it did not come to change, including comments,
ordering and whitespace.

Not covered here: extensions that do not ship with PHP — xdebug, redis, imagick.
Those are real downloads, matched against the build's compiler and thread
safety, and they live in `pecl.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Extensions loaded with `zend_extension` rather than `extension`.
#:
#: The distinction is not cosmetic: a Zend extension hooks the engine itself
#: rather than registering functions, and loading one with the wrong directive
#: fails at startup with a message about the *other* directive, which is a
#: memorable half hour. The list is short and stable — these are the Zend
#: extensions anybody runs locally.
ZEND = frozenset({"opcache", "xdebug", "ioncube_loader"})


class UnknownExtension(Exception):
    """Asked for an extension this build does not carry."""


def shipped(runtime_directory: Path) -> list[str]:
    """
    Every extension the build carries, whether loaded or not.

    Read from `ext/` rather than from a list kept here. Which extensions ship
    varies by PHP version — and by whoever built it, since an adopted PHP may
    have been compiled by somebody else entirely.
    """
    ext = runtime_directory / "ext"

    if not ext.is_dir():
        return []

    names = set()

    for entry in ext.iterdir():
        if not entry.is_file():
            continue

        # `php_gd.dll` on Windows, `gd.so` where this is developed. Both, so
        # that the tests exercise the real function rather than a stand-in.
        if entry.suffix.lower() == ".dll" and entry.stem.lower().startswith("php_"):
            names.add(entry.stem[4:].lower())
        elif entry.suffix.lower() == ".so":
            names.add(entry.stem.lower())

    return sorted(names)


def enabled(ini: Path) -> list[str]:
    """
    Extensions the ini currently loads, in the order it loads them.

    Commented-out lines do not count — the commonest way to disable one by hand
    is to put a `;` in front of it, and reading those as enabled would report
    the opposite of the truth.
    """
    found = []

    for line in _lines(ini):
        name = _directive(line)

        if name is not None and name not in found:
            found.append(name)

    return found


def enable(ini: Path, name: str, runtime_directory: Path) -> bool:
    """
    Switch an extension on. Returns whether anything changed.

    Refuses names the build does not carry. `extension = xdebug` in an ini with
    no `php_xdebug.dll` beside it is not an error PHP raises loudly: it prints a
    startup warning to a log nobody is reading and carries on without it, so the
    extension is simply, quietly, absent.
    """
    name = name.lower()
    available = shipped(runtime_directory)

    if name not in available:
        raise UnknownExtension(
            f"This PHP does not ship {name!r}. It carries: {', '.join(available)}.\n"
            f"Extensions that are not part of the build — xdebug, redis, imagick — "
            f"are installed rather than enabled."
        )

    if name in enabled(ini):
        return False

    directive = "zend_extension" if name in ZEND else "extension"
    text = ini.read_text(encoding="utf-8")

    # A commented-out line for this extension is uncommented in place, rather
    # than appending a second one. Somebody who wrote `;extension = gd` put it
    # where they wanted it, often under a comment explaining why, and a
    # duplicate at the bottom of the file makes the two disagree about which is
    # in force — the answer being "the last one", which is not what either of
    # them looks like.
    revived = re.sub(
        rf"^[ \t]*;+[ \t]*({directive}[ \t]*=[ \t]*{re.escape(name)}[ \t]*)$",
        r"\1",
        text,
        count=1,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    if revived != text:
        ini.write_text(revived, encoding="utf-8")

        return True

    addition = f"\n; Added by `portable ext enable {name}`.\n{directive} = {name}\n"
    ini.write_text(text.rstrip("\n") + "\n" + addition, encoding="utf-8")

    return True


def disable(ini: Path, name: str) -> bool:
    """
    Switch an extension off by commenting it out. Returns whether anything
    changed.

    Commented rather than deleted. The line frequently carries settings around
    it that belong to the same extension, and a person turning something off for
    an afternoon should find it where they left it.
    """
    name = name.lower()

    if name not in enabled(ini):
        return False

    lines = _lines(ini)
    changed = [
        f"; {line}  ; disabled by `portable ext disable {name}`"
        if _directive(line) == name
        else line
        for line in lines
    ]

    ini.write_text("\n".join(changed) + "\n", encoding="utf-8")

    return True


def report(ini: Path, runtime_directory: Path) -> list[dict]:
    """
    Every extension of this build, with its state.

    `missing` is the state worth having a name for: an ini that loads something
    the build does not carry. PHP does not fail on it — it warns at startup, to
    a log, and runs without the extension — so the symptom is a function that
    does not exist, reported nowhere near the cause.
    """
    on = enabled(ini)
    available = shipped(runtime_directory)

    entries = [
        {"name": name, "enabled": name in on, "shipped": True, "zend": name in ZEND}
        for name in available
    ]
    entries += [
        {"name": name, "enabled": True, "shipped": False, "zend": name in ZEND}
        for name in on
        if name not in available
    ]

    return sorted(entries, key=lambda entry: entry["name"])


def _lines(ini: Path) -> list[str]:
    if not ini.is_file():
        return []

    return ini.read_text(encoding="utf-8").splitlines()


def _directive(line: str) -> str | None:
    """The extension a line loads, or None if it loads nothing."""
    match = re.match(
        r"^[ \t]*(?:zend_)?extension[ \t]*=[ \t]*[\"']?([^\"';\s]+)",
        line,
        flags=re.IGNORECASE,
    )

    if match is None:
        return None

    value = match.group(1)

    # `extension = php_gd.dll` is the older spelling and still valid. Reduced to
    # the bare name so that the two forms are one thing here rather than two
    # that fail to notice each other.
    stem = Path(value).stem.lower()

    return stem.removeprefix("php_")
