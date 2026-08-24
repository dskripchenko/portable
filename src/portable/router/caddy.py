"""
Caddy's configuration, as JSON.

JSON rather than a Caddyfile, and that is a deliberate trade. A Caddyfile reads
better and is what anyone would write by hand; JSON is what Caddy's admin API
speaks, and speaking it means adding a site is one HTTP call rather than
rewriting a file, reloading, and hoping the reload took.

It also removes the worst failure mode of generated configuration: there is no
file for a person to edit, so there is nothing for this tool to overwrite. When
hand-written configuration becomes necessary it will arrive as an explicit
per-site fragment, not as "we regenerate the whole thing and try to preserve
your edits".

The routes below mirror what the Caddyfile directive `php_fastcgi` expands to,
minus the parts a local development environment does not need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import paths

#: Caddy's own default admin address, and the reason this is not hardcoded:
#: anything else on the machine running Caddy — another tool, a project's own
#: stack — is already on it, and the second one to start simply fails. The port
#: is allocated like every other and passed in.
DEFAULT_ADMIN = "127.0.0.1:2019"

#: The server key inside Caddy's config. Everything this tool creates lives
#: under one server, so a site can be added or removed without touching the rest.
SERVER = "portable"


@dataclass(frozen=True)
class Site:
    """One thing served at one hostname."""

    name: str
    """`demo` — the label, and the first part of the hostname."""

    root: Path
    """The directory served. Absolute."""

    upstreams: list[str]
    """`127.0.0.1:9001` per PHP worker. The order is irrelevant; Caddy balances."""

    index: str = "index.php"

    @property
    def hostname(self) -> str:
        """
        `demo.localhost`.

        `.localhost` and not `.test`: Windows and macOS both resolve `*.localhost`
        to the loopback on their own, so nothing has to be written to the hosts
        file and no administrator is needed. `.test` resolves nowhere without
        either a hosts entry or a DNS server, and both cost privileges this tool
        has decided not to ask for.
        """
        return f"{self.name}.localhost"


def config(sites: list[Site], listen: int = 80, admin: str = DEFAULT_ADMIN) -> dict:
    """The whole configuration document."""
    return {
        "admin": {"listen": admin},
        "logging": {"logs": {"default": {"level": "INFO"}}},
        "apps": {
            "http": {
                "servers": {
                    SERVER: {
                        "listen": [f":{listen}"],
                        "routes": [*[route_for(site) for site in sites], _unmatched(sites)],
                        # Otherwise Caddy answers `Host: anything` with the first
                        # matching route, and a request meant for one site is
                        # quietly served by another.
                        "automatic_https": {"disable": True},
                    }
                }
            }
        },
    }


def route_for(site: Site) -> dict:
    """
    One site: match the hostname, serve files, hand `.php` to the pool.

    The order inside matters and is the same order `php_fastcgi` uses. A request
    for an existing file is served as a file; anything else becomes a request for
    the front controller. Reversing those two makes `style.css` arrive at PHP.
    """
    root = str(site.root)

    return {
        "@id": _route_id(site.name),
        "match": [{"host": [site.hostname]}],
        "handle": [
            {
                "handler": "subroute",
                "routes": [
                    # Every handler below needs to know where the site lives.
                    {"handle": [{"handler": "vars", "root": root}]},
                    # A directory: try its index file before anything else.
                    {
                        "match": [{"file": {"try_files": ["{http.request.uri.path}/" + site.index]}}],
                        "handle": [
                            {
                                "handler": "rewrite",
                                "uri": "{http.matchers.file.relative}",
                            }
                        ],
                    },
                    # Not a file on disk: the front controller answers for it.
                    # This is what makes clean URLs work without a rule per route.
                    {
                        "match": [
                            {
                                "not": [
                                    {"file": {"try_files": ["{http.request.uri.path}"]}}
                                ]
                            }
                        ],
                        "handle": [
                            {
                                "handler": "rewrite",
                                "uri": f"/{site.index}",
                            }
                        ],
                    },
                    # `.php` goes to the pool; everything else is a static file.
                    {
                        "match": [{"path": ["*.php"]}],
                        "handle": [
                            {
                                "handler": "reverse_proxy",
                                "transport": {
                                    "protocol": "fastcgi",
                                    "root": root,
                                    "split_path": [".php"],
                                },
                                "upstreams": [{"dial": upstream} for upstream in site.upstreams],
                                "load_balancing": {
                                    # Least connections, not round robin: a
                                    # `php-cgi` worker serves one request at a
                                    # time, so the useful question is which
                                    # worker is idle, not whose turn it is.
                                    "selection_policy": {"policy": "least_conn"},
                                    # Retry across the pool rather than failing.
                                    #
                                    # Not a nicety. A worker retires itself every
                                    # PHP_FCGI_MAX_REQUESTS and is gone for the
                                    # fraction of a second it takes to replace —
                                    # with four workers and a limit of 500 that
                                    # happens constantly. Without this the
                                    # request that arrives in that window gets a
                                    # 502 while three idle workers watch.
                                    #
                                    # Measured: a replacement binds its port in
                                    # about a quarter of a second, so five
                                    # seconds is generous rather than optimistic.
                                    "try_duration": "5s",
                                    "try_interval": "100ms",
                                },
                                "health_checks": {
                                    "passive": {
                                        # A worker that just refused a connection
                                        # is skipped for a moment instead of
                                        # being tried again immediately by every
                                        # request that arrives.
                                        "fail_duration": "2s",
                                        "max_fails": 1,
                                    }
                                },
                            }
                        ],
                    },
                    {"handle": [{"handler": "file_server", "root": root}]},
                ],
            }
        ],
        "terminal": True,
    }


def _unmatched(sites: list[Site]) -> dict:
    """
    The answer for a hostname no site claims.

    Without it Caddy replies with an empty `200`, which is the least helpful
    thing available: the browser shows a blank page, and the reader has no way
    to tell "the site is not configured" from "the site is broken". A 404 that
    names what *is* configured answers the question that was actually being
    asked.
    """
    known = ", ".join(sorted(site.hostname for site in sites)) or "nothing yet"

    return {
        "@id": "portable-unmatched",
        "handle": [
            {
                "handler": "static_response",
                "status_code": 404,
                "headers": {"Content-Type": ["text/plain; charset=utf-8"]},
                "body": (
                    "portable: no site is configured for this hostname.\n\n"
                    f"Configured: {known}\n"
                ),
            }
        ],
    }


def _route_id(name: str) -> str:
    """
    A stable handle for one site's route.

    Caddy's admin API can address a config node by `@id`, so a site can be
    replaced or deleted on its own — without this, changing one site means
    sending the whole document and racing anything else that is doing the same.
    """
    return f"portable-site-{name}"


def command(executable: Path, config_file: Path) -> list[str]:
    """
    How Caddy is started.

    `--adapter ""` because the file is already JSON — without it Caddy assumes a
    Caddyfile and fails on the first brace.
    """
    return [
        str(executable),
        "run",
        "--config",
        str(config_file),
        "--adapter",
        "",
    ]


def complaints(log: Path, lines: int = 8) -> str:
    """
    The end of Caddy's log, with the routine chatter dropped.

    Caddy logs structured JSON and most of it is `info`: which config file it
    read, that HTTP/3 needs TLS, that certificate maintenance started. A plain
    tail of twenty-five of those buries the one line that says what went wrong
    under a screenful of things that went right — which is how a failure message
    stops being read at all.

    Anything above `info` is kept. When there is nothing above it, the plain
    tail comes back: silence would be worse than noise, and a Caddy that failed
    without complaining is itself worth seeing.
    """
    if not log.exists():
        return paths.tail(log, lines)

    text = log.read_text(encoding="utf-8", errors="replace")
    notable = []

    for line in text.splitlines():
        try:
            level = json.loads(line).get("level")
        except (json.JSONDecodeError, AttributeError):
            # Not JSON — a crash, a panic, or something writing to the same
            # file. Exactly the sort of thing worth keeping.
            notable.append(line)
            continue

        if level not in (None, "info", "debug"):
            notable.append(line)

    if not notable:
        return paths.tail(log, lines)

    return f"From {log}:\n" + "\n".join(notable[-lines:])
