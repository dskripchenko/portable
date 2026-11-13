"""
Python applications, standing where the `php-cgi` pool stands for PHP.

PHP is served by handing each request to a pool of interpreters that know
nothing about the application. Python does not work that way: the application
*is* the server — Django's `runserver`, uvicorn, waitress, Flask's development
server — and it listens on a port of its own. So a Python site is one long-lived
process this tool starts in the project's directory, on a port it allocates,
with the router proxying the site's hostname to it.

What is decided here, and nowhere else:

- **Which interpreter.** The project's own virtual environment when it has one
  (`.venv` or `venv`), because that is where its dependencies are. Otherwise an
  installed Python — pinned per site, the newest by default, as with PHP.
- **What `{port}` becomes**, in a command the person wrote.
- **What the first word of that command means.** `python` is the interpreter
  chosen above; `uvicorn` is looked for in the environment's `Scripts` before
  anywhere else. On Windows that lookup is not a nicety: `CreateProcess`
  searches the *daemon's* PATH, not the one handed to the child, so a bare
  `uvicorn` would be found nowhere — or found in some other Python entirely.

Nothing here starts anything. Like `pool.build`, it describes a process, so the
arithmetic can be tested without a Python application to run.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import trust
from .runtimes import Installed
from .sites import Site
from .supervisor import Spec

#: Where a project keeps its virtual environment, most common first. The two
#: names `python -m venv` documentation and every tutorial use; anything else is
#: named by giving the full path to its interpreter in `--command`.
VENV_DIRECTORIES = (".venv", "venv")

#: First words of a command that mean "the interpreter", whichever it is.
INTERPRETER_WORDS = ("python", "python3", "python.exe", "python3.exe")

#: What goes where the port does.
PORT = "{port}"


class InvalidCommand(ValueError):
    """The command cannot be run as written."""


@dataclass(frozen=True)
class App:
    """One Python site's process, and where to reach it."""

    site: str
    port: int
    spec: Spec

    @property
    def upstream(self) -> str:
        return f"127.0.0.1:{self.port}"


def process_name(site: str) -> str:
    """`python-<site>`, so that `portable logs python` follows every one of them."""
    return f"python-{site}"


def venv_of(root: Path) -> Path | None:
    """The project's virtual environment, if it has one that holds an interpreter."""
    for name in VENV_DIRECTORIES:
        candidate = root / name

        if _interpreter_in(candidate) is not None:
            return candidate

    return None


def _interpreter_in(environment: Path) -> Path | None:
    for relative in ("Scripts/python.exe", "bin/python3", "bin/python"):
        candidate = environment / relative

        if candidate.is_file():
            return candidate

    return None


def _scripts_of(environment: Path) -> Path:
    """`Scripts` on Windows, `bin` elsewhere — where pip puts console scripts."""
    return environment / "Scripts" if (environment / "Scripts").is_dir() else environment / "bin"


def venv_home(environment: Path) -> Path | None:
    """
    The Python a virtual environment was made from, as `pyvenv.cfg` records it.

    Needed to answer "is anything using this Python" honestly: a venv does not
    contain an interpreter so much as point at one, and deleting that one breaks
    the venv without touching a file inside it.
    """
    try:
        text = (environment / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for line in text.splitlines():
        key, _, value = line.partition("=")

        if key.strip().lower() == "home" and value.strip():
            return Path(value.strip())

    return None


def default_command(root: Path) -> str | None:
    """
    The command for a project that did not give one, where there is no doubt.

    Only Django, because only Django announces itself unambiguously: a
    `manage.py` at the root, and one way to serve it. A `main.py` could be
    FastAPI, Flask, a script or a game, and guessing between them would mean
    guessing.

    The reloader is left on, as `runserver` has it by default: editing code and
    reloading the page is the point of a development server. Stopping it takes
    the reloader's child too — see `spawn.stop_tree`.
    """
    if (root / "manage.py").is_file():
        return f"python manage.py runserver 127.0.0.1:{PORT}"

    return None


def split(command: str) -> list[str]:
    """
    A command line, split the way the platform means it.

    The same rules as the CLI's own `split`: on Windows the backslash in
    `C:\\proj\\.venv` is a path separator rather than an escape, so POSIX rules
    would mangle it.
    """
    if os.name != "nt":
        return shlex.split(command)

    tokens = []

    for token in shlex.split(command, posix=False):
        if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]

        tokens.append(token)

    return tokens


def interpreter_for(site: Site, runtime: Installed | None) -> tuple[Path, Path | None]:
    """The interpreter a site runs under, and its virtual environment if any."""
    environment = venv_of(site.root)

    if environment is not None:
        return _interpreter_in(environment), environment

    if runtime is None:
        raise InvalidCommand(f"{site.name} has no virtual environment and no Python to use.")

    return runtime.executable("python"), None


def build(
    site: Site,
    port: int,
    runtime: Installed | None,
    log: Path,
) -> App:
    """
    Describe one site's application process. Nothing is started here.

    [runtime] may be None when the project has a virtual environment: the venv
    knows its own interpreter, and requiring an installed Python as well would
    refuse a project that works.
    """
    interpreter, environment = interpreter_for(site, runtime)
    argv = [token.replace(PORT, str(port)) for token in split(site.command or "")]

    if not argv:
        raise InvalidCommand(f"{site.name} has an empty command.")

    directories = _path_for(interpreter, environment, runtime)
    env = _environment(port, directories, environment)
    argv[0] = _resolve_first(argv[0], interpreter, directories, env["PATH"], site.root)

    return App(
        site=site.name,
        port=port,
        spec=Spec(
            name=process_name(site.name),
            argv=argv,
            # The project's own directory, unlike every other process here: a
            # `manage.py` or a `main:app` is found relative to it, and so are the
            # `.env` files frameworks read.
            cwd=site.root,
            env=env,
            log=log,
            restart=True,
            tree=True,
        ),
    )


def _path_for(
    interpreter: Path, environment: Path | None, runtime: Installed | None
) -> list[Path]:
    """Where commands are looked for, in order: the venv, then the Python under it."""
    directories = []

    if environment is not None:
        directories.append(_scripts_of(environment))

    home = interpreter.parent

    if environment is None:
        directories += [home, home / "Scripts"]
    elif runtime is not None:
        root = runtime.executable("python").parent
        directories += [root, root / "Scripts"]

    return directories


def _environment(port: int, directories: list[Path], venv: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    separator = os.pathsep
    inherited = [entry for entry in environment.get("PATH", "").split(separator) if entry]

    environment["PATH"] = separator.join([*(str(entry) for entry in directories), *inherited])

    # For applications that read their port rather than being told it — the
    # convention most hosting platforms set, and what `{port}`-less commands
    # rely on.
    environment["PORT"] = str(port)

    # Otherwise Python buffers its output when it is not a terminal, and the log
    # stays empty until a buffer fills — which, for a server that has just
    # failed to import something, is never.
    environment["PYTHONUNBUFFERED"] = "1"

    if venv is not None:
        environment["VIRTUAL_ENV"] = str(venv)

    # A venv from somewhere else must not leak in through the daemon's own
    # environment and change which packages are imported.
    else:
        environment.pop("VIRTUAL_ENV", None)

    environment.pop("PYTHONHOME", None)

    bundle = trust.bundle()

    if bundle.is_file():
        # Python's `ssl` reads the first, `requests` — and so pip — the second.
        # The file holds the machine's roots plus the local authority, so this
        # widens trust to the sites served here rather than narrowing it.
        environment["SSL_CERT_FILE"] = str(bundle)
        environment["REQUESTS_CA_BUNDLE"] = str(bundle)

    return environment


def _resolve_first(
    word: str, interpreter: Path, directories: list[Path], path: str, root: Path
) -> str:
    """The first word of the command, as a path the operating system can start."""
    if word.lower() in INTERPRETER_WORDS:
        return str(interpreter)

    if Path(word).is_absolute():
        return word

    if "/" in word or "\\" in word:
        # `scripts/serve.cmd` means the project's, not whatever is relative to
        # the daemon's own directory.
        return str((root / word).resolve())

    for directory in directories:
        for candidate in (directory / f"{word}.exe", directory / word):
            if candidate.is_file():
                return str(candidate)

    found = shutil.which(word, path=path)

    if found is None:
        raise InvalidCommand(
            f"{word!r} was not found in the project's environment or on PATH. "
            f"Install it into the environment — `python -m pip install {word}` — "
            f"or start the command with `python -m {word}`."
        )

    return found
