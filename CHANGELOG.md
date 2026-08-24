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
