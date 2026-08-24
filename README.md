# portable

A local development environment for Windows — PHP, Caddy, PostgreSQL, MariaDB,
Node, Redis — that installs **beside** the system rather than into it.

> Status: **early**. The catalog and acquisition layer work and are tested. The
> supervisor, the control API and the CLI are being built. Nothing here is
> usable yet.

## Why

Laragon solves this problem on Windows and solves it well. This exists because
its distribution terms changed, and because a tool you depend on daily is worth
being able to read, fork and keep.

macOS and Linux are not the target: there you can assemble the same stack by
hand in an afternoon, and Homebrew or apt will keep it fed. On Windows you
cannot, which is why every tool in this category exists there.

## What "beside the system" means

Every one of these is a design constraint, not an aspiration:

- **No administrator rights.** Not at install, not at runtime, not ever.
- **No `hosts` file.** Sites are reached at `*.localhost`, which Windows and
  macOS both resolve to the loopback on their own — verified on both, in the
  browser and from the shell.
- **No services, no autostart.** The supervisor is a process you start. It
  survives closing the terminal and the IDE; it does not survive a reboot, and
  that is the trade being made deliberately.
- **No registry, no PATH, no system directories.** Everything lives under
  `%LOCALAPPDATA%\portable`. Deleting that directory uninstalls the tool
  completely.

The result runs on a locked-down corporate machine, which is precisely where
this class of tool usually cannot be installed at all.

## Design

**The CLI is a client, not the program.** A supervisor daemon owns the runtimes
and the processes and exposes a token-authenticated control API on the loopback;
`portable` is the first client of that API. An IDE plugin, when it comes, will
be the second — with nothing to retrofit, because there is no functionality
reachable only from the command line.

**Runtimes come from their publishers, verified.** Versions resolve against the
publisher's own index — php.net's `releases.json`, Caddy's GitHub releases — and
archives are checked against the digests those publishers list. A mismatch
deletes the file: everything downloaded here is executed afterwards.

**Both downloaded and discovered runtimes are managed.** A PHP the tool
installed and a PHP already on the machine are the same kind of thing to the
supervisor. That is not a fallback — it is how anyone needing an extension the
prebuilt binaries lack stays unblocked.

### Two facts that shaped it

**There is no php-fpm on Windows.** FPM is a Unix-only SAPI; the Windows build
ships `php-cgi.exe` and nothing else. One process serves one request at a time,
and `PHP_FCGI_CHILDREN` needs `fork()`. So the supervisor runs a *pool* of
`php-cgi.exe` processes on separate ports and the router balances across them.
Process supervision is therefore the core of this tool, not plumbing around it.

**nginx disclaims its own Windows build.** From nginx's documentation: it uses
"only the `select()` and `poll()` connection processing methods, so high
performance and scalability should not be expected", and is "considered to be a
beta version". Caddy is used instead — a maintained native binary, with an admin
API that makes adding a site one HTTP call, and a local CA that solves HTTPS
without touching the machine's trust store.

## Development

```bash
python -m pytest
```

The tests run on any platform. Anything Windows-specific — process detachment,
the `php-cgi` pool — is exercised on CI, because the parts of this that cannot be
tested from a developer's Mac are exactly the parts most likely to be wrong.

Fixtures are captured from the publishers rather than written by hand. A
hand-made fixture only proves the parser agrees with its author: Caddy publishes
**sha512** checksums in a file that looks exactly like a sha256 listing, and
only a real one catches that.

## License

MIT.
