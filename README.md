# portable

A local development environment for Windows — PHP, Caddy, PostgreSQL, MariaDB,
Node, Redis — that installs **beside** the system rather than into it.

> Status: **works, unreleased.** PHP, Caddy, PostgreSQL, MariaDB, Redis and Node
> install and run; sites are served at `*.localhost`. Not yet verified on a real
> Windows machine — see the note at the end.

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
- **No registry, no PATH, no system directories.** Everything lives under one
  directory. Deleting it uninstalls the tool completely.
- **That directory is yours to choose.** `%LOCALAPPDATA%\portable` is only the
  default — see below.

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

## Using it

Download the bundle for your platform from the
[releases](https://github.com/dskripchenko/portable/releases), unzip it
anywhere, and run the launcher beside it. There is nothing to install — the
interpreter ships with the tool, because a program that installs runtimes on a
machine which has none cannot sensibly require one first.

```
portable up                          # start the daemon
portable available php               # what the publisher currently offers
portable install php                 # or: --from C:\your\own\php
portable install caddy
portable site add demo C:\projects\demo
                                     # -> http://demo.localhost

portable service add postgres        # 127.0.0.1:5432, user postgres
portable service add redis
portable install node
portable run npm install             # with the installed runtimes reachable

portable status
portable down
```

Every command takes `--json`. The CLI holds no logic of its own: it asks the
daemon and prints the answer, which is why an IDE plugin will be a second client
rather than a second implementation.

### Where it keeps things

```
portable home                        # where, and what decided that
portable home set D:\dev\portable    # somewhere else, from now on
portable home set --beside           # next to the launcher; travels with it
portable home clear                  # back to the default
portable --home E:\tmp status        # just this once
```

The default is `%LOCALAPPDATA%\portable`, and on a managed machine that default
can be unusable rather than merely unwelcome: AppLocker is commonly configured
to deny execution from under a user's profile — that is where software installed
without administrator rights lives, which is the point of the rule — and
everything downloaded here is an executable. Where that applies, nothing starts
until this is pointed somewhere execution is allowed.

`--beside` is the case an absolute path cannot express. It records the word, not
the path it resolves to today, so a bundle on a flash drive keeps working when
the drive letter changes.

Changing this moves nothing. Copying hundreds of megabytes of runtimes would be
a surprising thing for a settings command to do, and a copy that failed halfway
would leave two half-installations — so the old location is reported instead,
with what is still in it.

## Development

```bash
python -m pytest
python scripts/bundle.py --target x86_64-pc-windows-msvc
```

The tests run on any platform. Anything Windows-specific — process detachment,
the `php-cgi` pool — is exercised on CI, because the parts of this that cannot be
tested from a developer's Mac are exactly the parts most likely to be wrong.

Fixtures are captured from the publishers rather than written by hand. A
hand-made fixture only proves the parser agrees with its author: Caddy publishes
**sha512** checksums in a file that looks exactly like a sha256 listing, and
only a real one catches that.

## What has not been verified

Everything here runs, and most of it has been run against the real thing: a
`php-cgi` pool behind Caddy serving PHP, PostgreSQL initialised and queried,
Node reached through `portable run`, and the bundle started on a machine with
its `PATH` pointing at nothing.

All of that was on macOS, which shares `php-cgi`, the FastCGI protocol and the
archive formats with the target but not everything else. Three things can only
be answered on Windows, and are not yet:

- `php-cgi.exe`, `initdb.exe` and the rest, as actual Windows binaries;
- port 80 without administrator rights, and whether `http.sys` has it;
- detaching from the console, the process group and the job object.

CI runs the whole suite on `windows-latest`, which covers the logic and none of
those.

## License

MIT.
