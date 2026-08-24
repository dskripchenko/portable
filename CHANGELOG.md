# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

The first pieces of M0: resolving a runtime version to a concrete artifact, and
getting that artifact onto disk without trusting it.

### Added

- **A catalog that resolves versions against the publisher's own index.** PHP
  from php.net's `releases.json`, Caddy from its GitHub releases. A branch
  (`8.4`) or `latest` resolves to an exact version, a URL and a digest.

  The compiler in a PHP build is read from the index rather than assumed. PHP
  7.4 is `vc15`, 8.1 is `vs16`, 8.4 is `vs17` — a hardcoded value would fail on
  the older branches, and fail as an extension that will not load, far from its
  cause.

  Non-thread-safe builds always: FastCGI serves one request per process, so
  thread safety costs performance and buys nothing here.

- **Acquisition that verifies before it trusts.** Archives are checked against
  the publisher's digest and a mismatch deletes the file rather than leaving it
  on disk — everything acquired here is executed afterwards. An archive with an
  entry that would be written outside its own directory is refused outright.

  Downloads are written to a `.part` file and moved into place, so an
  interrupted transfer never looks like a finished one.

- **`PORTABLE_CA_BUNDLE`** for machines behind a TLS-terminating proxy. A tool
  whose reason for existing is "installs without administrator rights" will
  spend its life on managed corporate networks, and refusing to work there
  would be as wrong as skipping verification.

### Notes

Caddy publishes **sha512** checksums in a file indistinguishable at a glance
from a sha256 listing. The digest algorithm is therefore carried per build and
inferred from the digest's length, rather than assumed.

### Added — the supervisor and the control API

- **A supervisor that treats an exiting process as normal.** `PHP_FCGI_MAX_REQUESTS`
  makes `php-cgi` terminate on purpose after N requests, so restarting is the
  main loop here rather than error handling. A process that exits five times
  within ten seconds is given up on instead, and says why — otherwise a
  misconfigured pool member is restarted thousands of times a second and buries
  the one log line explaining itself.

- **A control API on the loopback, token-authenticated.** Everything the tool
  does is done through it; the CLI is its first client and holds no logic of its
  own. An IDE plugin becomes the second client of an API that already exists.

  The token is not ceremony: this API starts processes and is reachable by
  anything running as the same user. It is compared in constant time, and
  authorisation is checked before routing so an unauthenticated caller cannot
  map the routes by reading which ones answer 404.

- **`portable up`, `down`, `status`**, each with `--json`. The daemon survives
  the terminal that started it — on POSIX by a new session, on Windows by
  detaching from the console, the process group and, where the job permits it,
  the job object.

### Fixed

- **A detached child that exited was reported as running forever.** It became a
  zombie: still this process's child, never waited on, and a zombie answers
  `kill(pid, 0)`. Invisible from a terminal, because the shell exits straight
  after launching and init reaps the daemon — it only appeared once the launcher
  outlived the child, and then `down` sat waiting ten seconds for a process that
  had already gone.

- **`down` promised more than it checked.** It reported success as soon as the
  discovery file disappeared, which the daemon removes *before* exiting — so the
  ports could still be held. It now waits for the process.

- **A leaked file handle per supervised restart.** The log was opened for the
  child and never closed in the parent; a pool member recycling once a minute
  would exhaust the process's handles days later, far from the cause.

### Added — the pool and the router

- **A pool of `php-cgi` workers**, which is what Windows has instead of php-fpm.
  Each worker binds its own port with `-b` and serves one request at a time;
  concurrency is the pool size. `PHP_FCGI_CHILDREN` is deliberately not set — it
  needs `fork()`, and setting it would suggest PHP manages the pool when this
  tool does.

- **A generated `php.ini` per version.** A fresh PHP archive contains
  `php.ini-development` and `php.ini-production` and no `php.ini` at all, so
  every extension is off until one exists. Written once and then never touched:
  it is meant to be edited.

- **Ports allocated by binding, not by scanning.** A socket in `TIME_WAIT` is
  not listening and cannot be bound either, and a port reserved by Windows'
  dynamic range is invisible to any listing but refuses a bind. The pool range
  stops below 49152 so an outgoing connection cannot take a port from under a
  worker that is restarting.

- **A registry of runtimes, downloaded and discovered alike.** The supervisor
  cannot tell them apart and does not need to. Not a fallback: statically built
  PHP cannot load extensions at runtime, so pointing at your own build is the
  only way to stay unblocked when a prebuilt binary lacks one.

- **Caddy configured as JSON**, which is what its admin API speaks — adding a
  site becomes one HTTP call instead of rewriting a file and hoping the reload
  took. There is also no generated file for anyone to edit, so there is nothing
  for this tool to overwrite.

### Fixed — found by running the whole stack

- **One dead worker returned 502 while the rest of the pool sat idle.** A worker
  retires itself every `PHP_FCGI_MAX_REQUESTS`, so with four workers and a limit
  of 500 this happens constantly. Retries across the pool and passive health
  checks were added; verified against a running stack — 15 requests during a
  worker replacement, all 200, none failed.

  Measured while diagnosing it: a replacement binds its port in about a quarter
  of a second, so the suspicion that `TIME_WAIT` was to blame was wrong.

- **A hostname no site claimed got an empty `200`.** Caddy's default, and the
  least useful answer available: a blank page that cannot be told apart from a
  broken site. It is now a 404 that names what *is* configured.

### Added — sites, and the commands that make them

- **`portable install php|caddy [version]`** — resolved against the publisher's
  index, verified against the publisher's digest.

- **`portable install <runtime> --from <path>`** — adopt one already on the
  machine. Used and never modified: this tool will not update or delete a PHP
  that Homebrew or another program installed. Not a fallback for a failed
  download — statically built PHP cannot load extensions at runtime, so this is
  the only way to stay unblocked when a prebuilt binary lacks one.

- **`portable site add <name> [dir]`**, `site list`, `site remove`. A site is
  served at `<name>.localhost`, which Windows and macOS both resolve to the
  loopback with nothing written to a hosts file and no administrator involved.

- **A reconcile, rather than start and stop commands.** Adding a site, removing
  one and recovering after a crash all take the same path: work out which pools
  should exist for the sites that are declared, start what is missing, retire
  what nothing points at. Calling it twice changes nothing, which is what makes
  it safe to run from every route that modifies anything.

- **Caddy is reconfigured live** through its admin API rather than restarted.
  Verified: adding a second site leaves the first one's connections alone and
  Caddy keeps the same pid.

### Fixed — found by running the thing

- **The check after starting Caddy asked the wrong question.** It confirmed
  Caddy's admin endpoint was answering, which it does on its own port and keeps
  doing when the site listener fails to bind. A port held by IIS or `http.sys`
  would therefore be reported as ours, and the fallback to 8080 would never
  happen.

  Found on a machine where another tool's nginx already held port 80: both
  processes were listening, on different address families, and only asking the
  port itself could tell which one would answer. The probe now sends a hostname
  no site can have and looks for the signature of our own unmatched-host route.

- **Caddy's admin port was hardcoded to 2019**, which is Caddy's own default —
  so any other Caddy on the machine already had it, and the second to start
  would fail with a message about a port rather than about a conflict. It is
  allocated now, like every other port here.

- **Locating an executable matched directories.** A PHP tree contains a
  *directory* called `php`, `rglob` matched it happily, and the runtime was
  recorded with version "unknown" because asking a directory for its version
  does not go well.

- **A TLS failure printed the raw error.** On a managed network with a
  terminating proxy — the machines this tool exists for — that is the likeliest
  failure of all, and the message said nothing about `PORTABLE_CA_BUNDLE`.

