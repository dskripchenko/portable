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
