#!/usr/bin/env python3
"""
Build a self-contained bundle: this tool plus the interpreter it runs on.

The reason it exists is the tool's own premise. `portable` installs runtimes on
a machine that has none — and needing Python already present in order to do that
would be the same problem it promises to solve, one level down. On Windows there
is no Python by default at all: what looks like one is an App Execution Alias
that opens the Microsoft Store.

So the interpreter travels with it, from `python-build-standalone` — the same
kind of self-contained, relocatable build the tool hands out for everything
else. The tool bootstraps itself the way it bootstraps PHP.

    python scripts/bundle.py --target x86_64-pc-windows-msvc

The result is a directory that can be unzipped anywhere and a launcher beside
it. Nothing is installed, no PATH is changed, no registry key is written —
deleting the directory removes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The package's own fetching, rather than a second copy here. It verifies
# certificates and honours `PORTABLE_CA_BUNDLE`, which matters for exactly the
# same reason it matters in the tool: this is built on managed networks too.
sys.path.insert(0, str(ROOT / "src"))

from portable import net

RELEASES = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

#: The libraries the dashboard is drawn with, pinned exactly.
#:
#: Only four, and that is measured rather than declared. `textual` names six more
#: dependencies — pygments, markdown-it-py and its plugins among them — and none
#: of those is imported by anything this tool does: the dashboard was run under
#: the test harness with `sys.modules` inspected afterwards, and they were absent.
#: Carrying them would add four and a half megabytes of syntax lexers for a
#: screen that highlights nothing.
#:
#: A test blocks those imports and runs the dashboard anyway, so that the day
#: something does reach for one, it is a failing test rather than a bundle that
#: crashes on somebody's machine.
#:
#: Pinned, and not by a range: this is a directory copied into an archive that
#: gets a checksum, and "whatever was newest that morning" is not something a
#: checksum can mean.
VENDORED = {
    "textual": "8.2.8",
    "rich": "15.0.0",
    "platformdirs": "4.11.4",
    "typing_extensions": "4.16.0",
}

#: The interpreter version that ships. Pinned rather than "newest": the bundle
#: is the one place where the Python running this tool is decided, and letting
#: it drift with an upstream release is how a build that worked in March fails
#: in April for reasons nobody changed.
PYTHON = "3.13"

TARGETS = {
    "x86_64-pc-windows-msvc": "windows",
    "aarch64-apple-darwin": "posix",
    "x86_64-apple-darwin": "posix",
}

#: `portable.cmd`, not an executable. Building a real `.exe` needs a compiler and
#: a code-signing story; a batch file needs neither and is honest about what it
#: does — anyone can read it and see exactly which interpreter runs.
WINDOWS_LAUNCHER = """@echo off
rem Runs portable with the interpreter that shipped beside it. Nothing here
rem depends on a Python being installed on this machine, and `%~dp0` is expanded
rem by the command processor itself — no external command is involved, for the
rem same reason the shell launcher avoids `dirname`.
"%~dp0python\\python.exe" -m portable.cli %*
"""

POSIX_LAUNCHER = """#!/bin/sh
# Runs portable with the interpreter that shipped beside it.
#
# Nothing here calls an external command. `dirname` was the obvious way to find
# this directory and the wrong one: it lives on PATH, and a launcher that needs
# PATH to work cannot be relied on where PATH is the thing that is wrong. Found
# by running the bundle with PATH pointing at nothing, which is a fair
# approximation of a locked-down machine.
#
# `cd`, `pwd` and parameter expansion are all builtins.
case "$0" in
    */*) here=${0%/*} ;;
    *)   here=. ;;
esac

CDPATH= cd -- "$here" || exit 1
exec "$PWD/python/bin/python3" -m portable.cli "$@"
"""

README = """portable
========

A development environment that installs beside the system, not into it.

Nothing here needs installing. Unzip it anywhere and run the launcher:

    portable up
    portable install php
    portable site add demo C:\\projects\\demo

Everything it downloads goes under %LOCALAPPDATA%\\portable (or ~/.portable).
Deleting that directory and this one removes it completely — no registry keys,
no services, no changes to PATH.

The interpreter in `python/` ships with the tool so that a machine without
Python can still run it. It is a standard CPython build from
python-build-standalone; nothing about it is modified.

https://github.com/dskripchenko/portable
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--python", default=PYTHON)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--keep-tree",
        action="store_true",
        help="Leave the unzipped bundle behind, for inspection or a local run.",
    )
    args = parser.parse_args(argv)

    version = _version()
    staging = args.output / f"portable-{version}-{args.target}"

    if staging.exists():
        shutil.rmtree(staging)

    staging.mkdir(parents=True)

    print(f"portable {version} for {args.target}")

    interpreter = _fetch_python(args.python, args.target, args.output)
    _unpack_python(interpreter, staging)
    _install_package(staging, args.target)
    _vendor(staging, args.target, args.output)
    _write_launcher(staging, args.target)
    (staging / "README.txt").write_text(README, encoding="utf-8")

    archive = _zip(staging, args.output)

    if not args.keep_tree:
        shutil.rmtree(staging)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (archive.parent / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )

    print(f"  {archive}  ({archive.stat().st_size // 1048576} MB)")
    print(f"  sha256 {digest}")

    return 0


def _version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)

    if not found:
        raise SystemExit("pyproject.toml carries no version.")

    return found.group(1)


def _fetch_python(python: str, target: str, into: Path) -> Path:
    """
    Download the standalone interpreter, cached by name.

    `install_only` rather than the full build: the debug symbols and the static
    library triple the size and nothing here links against them.

    A cached archive is used without asking anything: a rebuild should not need
    the network to discover the name of a file already on disk. Which also means
    an air-gapped or proxied machine can build once the interpreter is there.
    """
    suffix = f"-{target}-install_only.tar.gz"
    cached = sorted(into.glob(f"cpython-{python}.*{suffix}"))

    if cached:
        newest = max(cached, key=lambda path: path.name)
        print(f"  interpreter: {newest.name} (already here)")

        return newest

    release = json.loads(net.read_text(RELEASES, timeout=60))

    candidates = [
        asset
        for asset in release.get("assets", [])
        if asset["name"].endswith(suffix) and asset["name"].startswith(f"cpython-{python}.")
    ]

    if not candidates:
        offered = sorted(
            {
                name.split("+")[0]
                for name in (asset["name"] for asset in release.get("assets", []))
                if name.endswith(suffix)
            }
        )
        raise SystemExit(f"No CPython {python} for {target}. Offered: {', '.join(offered)}")

    # Newest patch of the pinned minor.
    asset = max(candidates, key=lambda item: item["name"])
    into.mkdir(parents=True, exist_ok=True)
    archive = into / asset["name"].replace("%2B", "+")
    print(f"  interpreter: {asset['name']}")

    with net.open_url(asset["browser_download_url"], timeout=900) as response:
        archive.write_bytes(response.read())

    return archive


def _unpack_python(archive: Path, staging: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(staging)

    if not (staging / "python").is_dir():
        raise SystemExit(f"{archive.name} did not contain a `python` directory.")


def _site_packages(staging: Path, target: str) -> Path:
    """
    Where the interpreter looks for packages — different on each platform.

    Windows keeps `Lib/site-packages` beside `python.exe`; the unix builds use
    `lib/python3.x/site-packages`. Discovered rather than assumed, because the
    minor version is in the path and pinning it here would make the bundle break
    quietly on the next interpreter bump.
    """
    if TARGETS[target] == "windows":
        return staging / "python" / "Lib" / "site-packages"

    found = sorted((staging / "python" / "lib").glob("python3.*/site-packages"))

    if not found:
        raise SystemExit("The interpreter has no site-packages directory.")

    return found[0]


def _install_package(staging: Path, target: str) -> None:
    """
    Copy the package in. No pip, no wheel, no dependencies.

    `portable` depends on nothing outside the standard library, which is what
    makes this a copy rather than an install — and is worth keeping true. Every
    dependency added here becomes a wheel that has to exist for this exact
    interpreter and platform.
    """
    destination = _site_packages(staging, target) / "portable"
    shutil.copytree(
        ROOT / "src" / "portable",
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    print(f"  package -> {destination.relative_to(staging)}")


def _vendor(staging: Path, target: str, cache: Path) -> None:
    """
    Put the dashboard's libraries beside our own package.

    Wheels from PyPI, verified against the digest PyPI publishes with them, and
    unpacked by hand. No pip: it would have to be present, would resolve
    versions of its own choosing, and would turn a copied directory into an
    install — which is the property that makes this bundle what it is.

    Pure Python only, checked rather than hoped for. A wheel with a compiled
    extension is built for one interpreter and one platform, and copying it into
    a bundle for another is a crash at import time on somebody else's machine.
    """
    destination = _site_packages(staging, target)

    for name, version in VENDORED.items():
        wheel = _fetch_wheel(name, version, cache)

        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            compiled = [entry for entry in names if entry.endswith((".so", ".pyd", ".dll"))]

            if compiled:
                raise SystemExit(
                    f"{name} {version} carries compiled files ({compiled[0]}), so it is "
                    f"built for one interpreter and platform. It cannot be vendored."
                )

            archive.extractall(destination)

        print(f"  vendored {name} {version}")


def _fetch_wheel(name: str, version: str, cache: Path) -> Path:
    """The `py3-none-any` wheel for one pinned version, verified and cached."""
    cached = cache / f"{name}-{version}-py3-none-any.whl"

    if cached.exists():
        return cached

    index = json.loads(net.read_text(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60))
    wheels = [
        entry
        for entry in index.get("urls", [])
        if entry.get("packagetype") == "bdist_wheel" and entry["filename"].endswith("-any.whl")
    ]

    if not wheels:
        raise SystemExit(f"{name} {version} publishes no platform-independent wheel.")

    wheel = wheels[0]
    cache.mkdir(parents=True, exist_ok=True)

    with net.open_url(wheel["url"], timeout=300) as response:
        payload = response.read()

    expected = wheel["digests"]["sha256"]
    actual = hashlib.sha256(payload).hexdigest()

    if actual != expected:
        raise SystemExit(
            f"{wheel['filename']} does not match the digest PyPI publishes.\n"
            f"  expected: {expected}\n  received: {actual}"
        )

    cached.write_bytes(payload)

    return cached


def _write_launcher(staging: Path, target: str) -> None:
    if TARGETS[target] == "windows":
        (staging / "portable.cmd").write_text(WINDOWS_LAUNCHER, encoding="utf-8")

        return

    launcher = staging / "portable"
    launcher.write_text(POSIX_LAUNCHER, encoding="utf-8")
    launcher.chmod(0o755)


def _zip(staging: Path, output: Path) -> Path:
    archive = output / f"{staging.name}.zip"
    archive.unlink(missing_ok=True)

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.rglob("*")):
            if path.is_dir():
                continue

            entry = zipfile.ZipInfo.from_file(path, path.relative_to(staging.parent))
            # The executable bit survives the round trip. Without it the POSIX
            # launcher unzips unrunnable, and the failure — "permission denied"
            # on a file that is plainly there — reads like something else.
            entry.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED

            with path.open("rb") as handle:
                bundle.writestr(entry, handle.read())

    return archive


def verify(bundle: Path, target: str) -> None:
    """
    Run the bundled tool once, with nothing of this machine's Python around it.

    The only check that means anything: a bundle that imports the developer's
    interpreter, or finds the package through an inherited `PYTHONPATH`, works
    on the machine that built it and nowhere else.
    """
    launcher = bundle / ("portable.cmd" if TARGETS[target] == "windows" else "portable")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME")
    }

    result = subprocess.run(
        [str(launcher), "status", "--json"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )

    # `status` with no daemon running exits 1 and says so in JSON. That is a
    # successful run: the interpreter started, the package imported, the CLI
    # parsed its arguments and reached the daemon check.
    if "errorKey" not in result.stdout:
        raise SystemExit(
            f"The bundle did not run.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


if __name__ == "__main__":
    sys.exit(main())
