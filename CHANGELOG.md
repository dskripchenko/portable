# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- **The release no longer builds a macOS bundle.** It was justified as a check
  that the bundler still worked, and it was not much of one: everything in
  `bundle.py` except the shell launcher is exercised by the Windows job too, and
  that launcher exists only for a bundle deliberately never published — code
  tested to keep its own test passing. It also put a second, unusable target in
  front of anyone reading the release run.

  `scripts/bundle.py --target aarch64-apple-darwin` still works for anyone
  developing on a Mac who wants one.

## 0.1.1 — 2026-08-25

Three defects found by running several versions of everything side by side,
which is what the question "can PHP 8.2 and 8.4 serve at the same time" turned
into. The answer was yes; the trouble was next door.

### Fixed — MariaDB could not be created, and then could not be connected to

Both found by running several versions side by side, which is what the question
"can PHP 8.2 and 8.4 serve at the same time" turned into.

- **MariaDB read configuration from outside this installation.** `C:\my.ini`,
  `%WINDIR%\my.ini`, the one beside its own binaries — and a machine that has
  ever had Laragon, XAMPP or another MariaDB on it has one of those pointing at
  somebody else's data directory and socket. It failed as "Installation of
  system tables failed", followed by a suggestion to look for "conflicting
  information in an external my.cnf". Each database now gets its own `my.cnf`,
  passed as `--defaults-file` and first on the command line, since MariaDB takes
  the first option file it is given and a `--defaults-file` anywhere else is a
  suggestion rather than an instruction. The file is written once and then left
  alone, the same rule `php.ini` follows.

- **The root account could not be reached over TCP.** Where a unix socket
  exists, `mariadb-install-db` defaults to socket authentication and creates a
  root usable only as the operating system's root over a local socket. So the
  database started, this tool reported "as root", and connecting was refused
  with "Host '127.0.0.1' is not allowed to connect". A tool that says how to
  connect and is wrong about it is worse than one that says nothing.

### Fixed — an adopted runtime's version was read wrongly

`postgres --version` prints `16.13`: two components, not three. The pattern
demanded three, found nothing, and recorded "unknown" — and the registry keys on
name and version, so two adopted PostgreSQLs would both have been "unknown" and
the second would have replaced the first.

MariaDB prints the full path to its own binary before the version, and that path
has numbers in it. On the machine this was found on, `/mariadb/11.4/11.4.10/bin/`
happens to agree with the release — the kind of coincidence that hides a bug
rather than revealing one. The path is dropped before the version is looked for.

## 0.1.0 — 2026-08-25

The first release. A local development environment for Windows — PHP, Caddy,
PostgreSQL, MariaDB, Redis, Node — that installs beside the system rather than
into it: no administrator rights, no `hosts` file, no services, no registry, no
changes to PATH. Everything lives in one directory, and deleting it uninstalls
the tool completely.

Ships as a self-contained bundle carrying its own Python, because a tool that
installs runtimes on a machine which has none cannot sensibly require one first.

**Verified on real Windows:** PHP served through a supervised `php-cgi` pool
behind Caddy, a Laravel project reachable at `*.localhost`, runtimes downloading
and unpacking over a network that resets TLS connections.

**Not yet verified on real Windows:** binding port 80 without administrator
rights, and surviving the console, the process group and the job object when the
terminal or the IDE closes. CI exercises the logic on `windows-latest` and
neither of those.

The sections below record what was built and, more usefully, what turned out to
be wrong about it — most of it discovered by running the real thing rather than
by reading.

### The beginning

The first pieces: resolving a runtime version to a concrete artifact, and
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

### Added — databases

- **`portable service add postgres|mariadb`**, `service list`, `service remove`.
  A database is initialised once — `initdb`, `mariadb-install-db` — and then
  supervised like everything else.

- **Removing a service never removes its data.** `service remove` stops the
  server, forgets the declaration and says where the directory still is.
  Anything else turns a routine command into a way of losing work, and there is
  no undo for that. Verified by doing it: removed, re-added, and the row
  inserted before was still there.

- **Both bind `127.0.0.1` and nothing else.** Authentication is trusting,
  because a password prompt buys nothing when anything running as that user can
  read the data directory anyway — the protection is that nothing off the
  machine can reach it.

- **PostgreSQL is initialised with `--locale=C`.** A database whose collation
  follows the developer's regional settings sorts differently from production
  and finds out in a test that passes for one person.

- **The conventional port, when it is free.** 5432 and 3306 so a connection
  string copied from anywhere works — and the next one up when something already
  has it, reported rather than assumed. Once chosen it is remembered: a
  connection string that changed on every restart would be useless.

- **`.tar.gz` archives.** The portable PostgreSQL builds ship one where
  everything else here ships zip. Links inside a tarball are refused outright —
  a symlink can point anywhere once unpacked, and nothing downloaded here has a
  reason to contain one.

### Notes on the publishers

MariaDB's API advertises its downloads over plain **http**, and files the digest
under `sha256sum` rather than `sha256`. The URL is upgraded to TLS — it
redirects to a community mirror, so the bytes come from a third party and the
digest is what makes that acceptable — and reading the wrong key would have been
a silent downgrade from verified to unverified.

The portable PostgreSQL builds publish no digests at all. Recorded as absent
rather than glossed over.

### Fixed

- **A service that could not start left its declaration behind**, so `list`
  reported it as merely "stopped" — inviting somebody to start it and be
  confused a second time. A failed `add` now withdraws it.

### Added — Node and Redis

- **Redis, as a service.** `portable service add redis`. No preparation step:
  it writes its dump into whatever directory it is given, and inventing an
  initialisation for it would be inventing work. Started in the foreground —
  a server that daemonises itself becomes a process nothing here can stop, and
  `portable down` would leave it holding its port.

- **Node, as a toolchain** — which is a different thing, and needed a different
  answer. It is not a service and not a router; it is used from a terminal, and
  this tool does not touch the system PATH. So the daemon answers *what a shell
  would need* and the client applies it:

  - `portable env [--shell powershell|cmd|posix]` prints settings to evaluate;
  - `portable run npm install` runs a command with the runtimes reachable.

  The knowledge stays in the API. An IDE plugin offering a terminal gets the
  same answer from the same place.

- **`latest` for Node means the newest LTS.** An environment that installs an
  odd-numbered Node by default produces bug reports about a runtime the project
  never meant to support. The actual newest is still reachable by naming it.

### Notes on the publishers

Redis does not support Windows and publishes nothing for it. The archive used
here is a third-party rebuild — current, tracking upstream within days, built
through msys2 — and it publishes no digests, which `install` reports. The fork
most often recommended, `tporadowski/redis`, last released in February 2022 at
Redis 5.

Anyone who would rather not: `portable install redis --from <path>` takes a
binary they chose themselves, and Microsoft's Garnet is a native RESP-compatible
alternative worth knowing about.

Node is the one publisher here with nothing to work around: an official index,
official archives, and a `SHASUMS256.txt` beside every version.

### Fixed

- **`portable run` took a runtime from the machine without saying so.** Asked
  for `node` with nothing installed, it ran the system's — the exact confusion
  the command exists to prevent. Falling back is still deliberate, because
  `portable run composer install` should work; doing it silently for a *runtime*
  is not, and it is now named on stderr.

- **An empty entry could reach PATH.** On POSIX that means the current
  directory, so with nothing installed a command run through `portable run`
  could pick up something from whichever directory it was typed in.

### Added — the bundle

- **`scripts/bundle.py`** builds a self-contained directory: this tool plus the
  interpreter it runs on, from `python-build-standalone`. Unzip it anywhere and
  run the launcher; nothing is installed, no PATH is changed, no registry key is
  written.

  It exists because of the tool's own premise. `portable` installs runtimes on a
  machine that has none, and requiring Python first would be the same problem
  one level down — on Windows there is no Python at all by default, only an App
  Execution Alias that opens the Microsoft Store. So the tool bootstraps itself
  the way it bootstraps PHP.

  The interpreter version is pinned rather than tracking the newest: the bundle
  is the one place where the Python running this is decided, and letting it
  drift is how a build that worked in March fails in April with nothing changed.

- **CI builds one bundle per target on a tag** and runs each of them with the
  build machine's Python removed from the environment. A bundle that quietly
  imports the builder's interpreter works exactly once, on the machine that made
  it.

### Fixed

- **The launcher called `dirname`.** An external command, found on PATH — so a
  launcher whose whole job is to work on an unprepared machine could not run
  where PATH was the thing that was wrong. It uses shell builtins now. Found by
  starting the bundle with `PATH` pointing at nothing, which is a fair
  approximation of a locked-down machine.

- **The bundler asked the network before looking in its own cache**, so a
  rebuild needed connectivity to learn the name of a file already on disk.

### Added — choosing where it all lives

- **`portable home`** reports the installation directory and what decided on it;
  **`home set <path>`**, **`home set --beside`** and **`home clear`** change it;
  **`--home PATH`** overrides it for one command, on either side of the verb.

  `%LOCALAPPDATA%\portable` remains the default, and on a managed machine that
  default can be unusable rather than merely unwelcome. AppLocker is commonly
  configured to deny execution from under a user's profile — that is where
  software installed without administrator rights lives, which is exactly why
  the rule exists — and everything this tool downloads is an executable. Where
  that applies, nothing starts at all until the location is moved.

  The source is reported alongside the path because two of the three ways it can
  be set are invisible from the outside: a variable exported in another shell, a
  pointer file written months ago.

  `--beside` records the word rather than the path it resolves to today. A flash
  drive is `E:` on one machine and `F:` on the next, and freezing the letter in
  would produce a bundle that works on exactly one computer — the opposite of
  what choosing that option asks for.

  Nothing is moved when the setting changes: copying hundreds of megabytes is a
  surprising thing for a settings command to do, and a copy that failed halfway
  would leave two half-installations. The old location and its contents are
  reported instead, so it is neither silently re-downloaded nor silently kept.

  Two refusals worth naming. A directory that cannot be written to is rejected
  at `home set` rather than halfway through the first download, and is not
  recorded — otherwise the failure leaves the tool pointed somewhere unusable
  and every later command fails, including the one that would fix it. And the
  setting cannot change while the daemon is running: its discovery file is the
  only way any client finds it, so moving the location out from under it would
  leave a daemon still holding ports 80 and 5432 that nothing can reach, `down`
  included.

### Fixed — the databases could not be installed at all

`portable install postgres`, `mariadb`, `node` and `redis` were accepted by the
command line and refused by the daemon, which knew only PHP and Caddy. Since
`service add postgres` requires its runtime to be installed already, and the
only command that could install it rejected the name, the databases and Redis
were unreachable by any documented route.

Two lists, written at different times, that quietly stopped agreeing. There is
one now — `catalog.modules()` — read by the parser for what it offers and by the
daemon for what it accepts, and a test asserts the two sides still match. Adding
a runtime is one entry.

The same generalisation fixed a second thing on the way past. Fetching a digest
published in a separate file was written for Caddy and hardcoded to it; Node
does the same and was silently installed unverified. It is now asked of the
module rather than of the name, and Node installs verified. Node's
`checksum_for` returned a bare string where Caddy's returned `(digest,
algorithm)` — one shape now, since two would have meant a branch per publisher
and a third when the next one joins.

### Added — `portable available <runtime>`

What each publisher currently offers, with what is already installed marked, so
choosing a version does not mean cross-referencing a second command.

Real listings from the publishers found two defects that hand-written fixtures
would not have:

- **PostgreSQL** cuts a release for every supported major on the same day, and
  GitHub returns them in that order — so the raw list read `18.6, 17.11, 16.15,
  15.19, 14.24, 18.4, 17.10`, each major appearing again a few rows down at an
  older patch. Offering the same major twice, out of order, invites picking the
  older one by accident.
- **Node** ships every couple of weeks, so a newest-first list was a screenful
  of one current major, with the LTS lines — the ones most people want — off the
  end and their labels unread. That is the exact mistake the listing exists to
  prevent.

Both now list the newest release of each major line. Node's index reaches back
to 2015, so it shows the newest eight, which covers every LTS anybody still
starts a project on.

MariaDB's unstable series are listed and marked rather than hidden: they install
perfectly well, and a listing that silently omits what is plainly on the
publisher's download page reads as out of date.

### Changed

- **GET routes take query parameters.** A read that needs an argument — which
  versions of PHP exist — had nowhere to put one, and a GET carrying a body is
  something servers and proxies may drop. Merged into the same dictionary
  handlers already read, so none of them needs to know which way it arrived.

- **An empty trust store is diagnosed as itself.** Verification failing because
  the interpreter has no root certificates at all is a different failure from a
  corporate proxy, and the message that names the proxy sends somebody looking
  for a thing that does not exist. The two are told apart by counting what was
  loaded. Windows is unaffected — Python reads the system store there — but the
  macOS bundle can land in exactly this state.

### Added — `portable ext`

`ext list`, `ext enable <name>`, `ext disable <name>`, against any installed PHP
(`--php 8.3`).

Windows PHP is not built the way a Linux distribution builds it: the archive
from php.net carries every extension it supports as a separate `php_<name>.dll`
in `ext/`, all present and none loaded. So enabling one is not a download — it
is a line in a file, and this is mostly about editing that file carefully.

Carefully, because the file is not ours. `php.ini` is written once when a PHP is
installed and then left alone forever, because somebody will edit it; that is
the point of having it on disk. So each operation is a surgical edit that
preserves comments, ordering and whitespace:

- A commented-out `;extension = gd` is uncommented **where it stands** rather
  than appended again at the bottom. Somebody who commented it put it there,
  often under a note saying why, and two lines disagreeing about what is in
  force resolve to "the last one" — which is what neither of them looks like.
- Disabling comments the line rather than deleting it. Extension settings
  usually sit around it, and somebody switching something off for an afternoon
  should find it where they left it.
- Zend extensions get `zend_extension`. Loading one with `extension` fails at
  startup with a message naming the *other* directive, while the file plainly
  says the first — which reads like the message is about somebody else.

Two states are surfaced that PHP itself reports only into a log nobody reads:

- **MISSING** — the ini loads something this build does not carry. PHP warns at
  startup and runs without it, so the symptom arrives later as a function that
  does not exist, hours from the line that caused it.
- Enabling something the build lacks is refused outright, with what it does
  carry, for the same reason.

**Changing an extension replaces the workers.** Each `php-cgi` reads the ini
once, at startup, so without this the command would report success and nothing
whatsoever would happen — inviting the conclusion that the extension is broken
rather than that it is not loaded yet. Replaced rather than reloaded because
there is no reload: the CGI SAPI has no `php-fpm reload`, which is the same
absence the pool exists to work around in the first place.

### Added — `portable ext install`

Extensions PHP does not ship — xdebug, redis, imagick and the rest of PECL —
downloaded, put where PHP will find them, switched on, and the workers replaced.

An extension is loaded into the running process, so four things must agree with
the build or it does not load at all: the PHP branch, thread safety, compiler
and architecture. None of them is guessed. All four come from the installed
build's own recorded variant — `nts-vs17-x64` — which is the same string PECL
puts in the filename, because both sides took it from php.net's index. They
match because they have a common source, not because this tool assembles
something that looks right.

**Xdebug comes from PECL, not from xdebug.org.** Xdebug publishes Windows builds
itself, and the filenames changed shape between its own releases: 3.4.0 is
`php_xdebug-3.4.0-8.4-vs17-nts-x86_64.dll`, 3.4.1 is
`php_xdebug-3.4.1-8.4-nts-vs17-x86_64.dll` — the compiler and thread-safety
tokens swapped places, and the architecture is spelled unlike anywhere else. A
parser written against either silently finds nothing for the other. The same
builds are on PECL under the uniform name, so the whole class of problem does
not arise.

Details that came out of the real listings:

- Pre-releases are excluded. `5.3.7RC1` is in the directory and is never what
  `latest` should mean.
- Versions sort numerically. As text, 3.10 sorts before 3.5 — invisible until an
  extension reaches its tenth minor, then `latest` quietly hands out a build a
  year old.
- If the newest release has no build for this PHP, it walks back, bounded. The
  maintainer builds when they build, and the failure names which versions were
  examined so it reads as "nobody built this yet" rather than as a broken tool.
- Dependencies travel with the module: imagick brings some 180 ImageMagick DLLs
  and does not load without them.

**An adopted PHP is refused.** A runtime found on this machine rather than
installed here is read and never written to — dropping a DLL into a PHP that
Homebrew, ServBay or a colleague's installer manages would be a surprising thing
to do, and their next update would remove it with neither side knowing why.

PECL publishes no digests for these, and this is a library about to be loaded
into every PHP process, so the install says so plainly rather than staying quiet
about it.

### Fixed — a daemon that was alive, listening, and unreachable

The discovery file — the only way any client finds the daemon — was written by
truncating it and then filling it in. Between those two steps the file exists
and is empty.

`read` treats a file it cannot parse as debris and deletes it, which is right
for one truncated by a crash. But `portable up` polls ten times a second during
exactly the period the daemon is starting and writing, so it landed in that
window, deleted the note the daemon had just written and would never write
again, and then polled an empty directory until it gave up. The daemon stayed
running the whole time, holding its port, reachable by nothing — including
`portable down`.

It failed on Windows CI about one run in four, as a timeout with nothing in any
log to explain it. Two rounds of looking at it blamed the timeout, which was
wrong: raising it from 15 to 60 seconds changed nothing, because there was
nothing left to find.

Written beside and renamed into place now. `os.replace` is atomic on Windows as
well as POSIX, so a reader sees either the previous file or the complete new
one. The test races a reader against a writer for two seconds; against the old
implementation every single read saw an unparseable file.

### Added — superseded PHP versions, and 7.x

php.net's index lists the current release of each branch and nothing else.
Everything it supersedes moves to an archive, which this now reads — so
`portable install php 8.3.20` works eighteen months after 8.3.20 stopped being
current, which is the only kind of version a pinned project ever names.

`portable available php 8.3` lists that branch's current release and every
archived patch of it. Per branch on purpose: the archive reaches back to 5.2 and
holds three hundred-odd builds, and listing them all answers nobody's question.

7.4.33 is still in the index and installs verified. 7.0 through 7.3 come from
the archive.

Three things the real listing settled, none of which a hand-written fixture
would have:

- **The compiler token is spelled in two cases.** `php-7.4.30-nts-Win32-vc15-x64.zip`
  and `php-7.2.34-nts-Win32-VC15-x64.zip` — the same compiler, in the same
  archive. The filename is used exactly as published, or the download 404s;
  the variant is lowercased, or PECL matches nothing for half the versions on
  offer. Both, and they are not the same operation.
- **Archived builds carry no digest.** `archives/sha1sum.txt` covers
  twenty-six files from the 5.2 era and the sha256 list covers only what is
  current. So these install unverified and say so — this is an interpreter about
  to run everything on the machine, and quietly reporting it as checked would be
  worse than not checking.
- **Versions sort numerically or the answer is wrong.** As text, `7.0.9` is the
  newest 7.0 and `7.0.33` is not.

### Fixed

- **`extension = curl` is only understood from PHP 7.2.** Before that the
  directive wants a filename, and a bare name is not an error anybody sees: PHP
  warns at startup, into a log, and runs without the extension — so the symptom
  is a missing function, hours from the cause. It went unnoticed while nothing
  older than 8.0 could be installed. The generated ini now writes
  `extension = php_curl.dll` for 7.0 and 7.1, which is exactly the case the
  archive introduced.

- **"No build for this PHP" named only one of the two reasons.** A PHP newer
  than the extension's last build looks identical, from inside, to a PHP so old
  the extension dropped it — and the second is the common one, since installing
  an archived PHP is what people do when something old has to keep working.
  Xdebug 3 does not build for PHP 7.2 and never will; xdebug 2.9.8 does. The
  failure now says so and shows the command that names a version.

### Added — `portable update` and `portable uninstall`

`update` reports what has a newer release on the same line as what is installed;
`--install` fetches them. `uninstall <runtime> <version>` deletes one and
reclaims its disk.

The two belong together. An update installs **alongside** rather than replacing,
so anything pinned to the old version keeps working — which is right, and means
something eventually has to take the old one away.

**Updates stay on their own line**, and the line is what each catalog says it
is: a branch for PHP (`8.4.24` looks for `8.4.x`), a series for MariaDB, a major
for the rest. Crossing one is not an update. A PHP branch change brings
deprecations to every site that pinned nothing, and a PostgreSQL data directory
belongs to the major that created it — 17 will not start on 18's files. Those
are installed by name, deliberately, or not at all.

Asking is done through `available(line=...)` rather than `resolve()`. Each
publisher's `resolve` takes what that publisher accepts — a branch for PHP, an
exact tag for the GitHub-published ones — so asking all six for "the newest 8"
returned 404s from Redis and PostgreSQL, and nothing at all for an archived PHP
whose branch php.net's index no longer lists. Listing a line is the one question
all six answer.

Removal refuses to take away the last runtime a site or database is relying on:
a site pinned to `8.4` follows whatever `8.4.x` is newest, so removing the only
one leaves it failing to start with a message about a version nobody typed. An
adopted runtime is forgotten, never deleted — it belongs to whoever put it
there. An unreachable publisher is reported per runtime rather than hiding what
the others said, and an adopted runtime is listed as not ours to update rather
than omitted, since silence would read as "current".

### Fixed

- **A failed `site add` left the site declared.** `service add` already
  withdrew its declaration on failure; sites did not, so a PHP that would not
  start left a site `list` reported cheerfully — inviting the conclusion that it
  exists and is merely stopped, then a second confusion when starting it does
  nothing. Withdrawn now, but only when the site is new: a failed *edit* of an
  existing site must not delete the declaration that was working.

- **A pool that would not start raised a bare `OSError`** naming a path and an
  error number and nothing about PHP. It now says which PHP, shows the end of
  that worker's log, and names the cause this has on Windows — php.net's builds
  link against the Visual C++ runtime and report its absence as
  `VCRUNTIME140.dll not found`. Workers already started are stopped rather than
  left behind, and named as they are created so the cleanup reaches for exactly
  what it made.

### Fixed — GitHub's rate limit

Caddy, PostgreSQL and Redis are resolved from GitHub's release API, which allows
sixty anonymous requests an hour **per address**. Behind a corporate NAT that is
sixty for the building, exhaustible by people who have never run this — and it
took out a bundle build on CI, where the runner's address is shared with
everybody using Actions.

A token is now offered when one is present in `PORTABLE_GITHUB_TOKEN` or
`GITHUB_TOKEN`, raising the limit to five thousand. Any token with no scopes at
all will do: it needs no permissions, only an identity.

Only to `api.github.com`, and dropped on a redirect that leaves it. That is not
a precaution against a hypothetical — GitHub answers a release-asset request
with a redirect to `objects.githubusercontent.com`, and urllib repeats every
header it was given, so the token would be handed verbatim to a different host
on the very first download.

The refusal itself now explains what it means. "Rate limit exceeded" invites the
reading that this tool is asking too often, when usually it is not asking at all.

### Added — `portable port`

`portable port 8888` chooses the port sites are served on; `port auto` goes back
to trying 80 and then 8080. Until now those two were the only candidates, so a
machine where both were taken had no way forward at all.

A chosen port is the **only** candidate. Falling back to 8080 after somebody
asked for 8888 would put the site at an address they did not pick and were not
told about — and the reason for choosing is that the defaults were not usable.

Refused: the ranges this tool hands out to PHP workers and the router's admin
endpoint, since a site there takes a number from under a worker about to ask for
it, intermittently and under load; and 49152 and above, which Windows uses for
outgoing connections, so the port can be taken between being chosen and being
bound.

Applied immediately rather than at the next start. The router is replaced rather
than reconfigured — Caddy's admin API can change routes on a running server and
cannot move it to a different socket.

### Added — the document root is found rather than assumed

`portable site add app C:\projects\app` serves `app\public` when the front
controller is there. `--exact` takes the path exactly as given.

Pointing a site at a repository root instead of `public/` serves the source of
the application over HTTP, `.env` included — and does it while appearing to
merely not work, since the framework's own router never runs.

Two rules keep it from ever being clever at somebody's expense. A directory that
holds the index file is used as given, so WordPress and anything with a front
controller at its root are answered correctly by doing nothing. And a
subdirectory is only taken when it really holds the index — the existence of a
`public` full of images is not evidence, and taking it would be the same mistake
from the other side.

The list is directory names, not frameworks: `public` is Laravel, Symfony and
Laminas, `web` is Craft and older Symfony, `webroot` is CakePHP — and the next
framework to appear will use one of these without this tool having heard of it.
Nothing here identifies a framework, so nothing here can identify one wrongly.

It is always reported when it happens. Right far more often than not, and still
somebody's business to know — otherwise the first surprise is editing an
`index.php` that changes nothing.

### Fixed

- **A port that could not be bound was stored anyway.** `port 8899` was taken by
  another program, the change failed — and the value stayed, so every later
  start tried it, failed, and served nothing, for a reason recorded only in the
  daemon's log. The previous port is put back and the sites returned to it.

- **`status` now says why nothing is being served.** A restore that fails leaves
  a daemon that is up, lists its sites, runs its workers and answers every
  question except the one that matters. Worse company than a daemon that is
  down.

- **Caddy's log is filtered before it is shown.** It logs structured JSON and
  most of it is `info` — the config file it read, that HTTP/3 needs TLS, that
  certificate maintenance began. Tailing twenty-five of those buried the one
  line that said what went wrong under a screenful of things that went right,
  which is how a failure message stops being read.

- **A truncated answer reached the person as a traceback.**
  `http.client.IncompleteRead` is an `HTTPException`, not a `URLError`, so it
  escaped both the client's error handling and the retry loop in `portable up` —
  during startup, which is precisely when a client should simply look again. It
  showed up once on CI as `IncompleteRead(0 bytes read, 18 more expected)` and
  did not reproduce in thirty local runs; why the response broke off is still
  unexplained, but nothing about it should ever have ended a wait. Truncated
  bodies, reset connections and read timeouts are now all "not answering",
  which is what they are.

### Added — HTTPS

Sites are served over TLS as well as plain HTTP, from a certificate authority
Caddy runs locally. `portable trust` puts that authority's root into the trust
store so browsers stop warning.

**Into the current user's store, never the machine's.** `certutil -user` on
Windows and the login keychain on macOS — the only ones reachable without
elevation, and enough: a certificate trusted for the person at the machine is
exactly the scope this deserves. Caddy installs its root by itself given the
chance, warning that it "might prompt for password", and that is switched off in
the configuration: a system change is a thing to be asked for, not a side effect
of adding a site.

**HTTPS never takes HTTP down.** The TLS port is chosen from 443 and 8443 by
what is free, and when neither is, TLS is simply not configured. A TLS listener
on a port something else holds makes Caddy fail to start *entirely* — plain HTTP
with it — and HTTP is the product while HTTPS is a convenience.

Three things the real binary settled that the configuration alone would not:

- **`@id` must be unique across the whole document.** Giving the TLS server the
  same route objects as the plain one produced `duplicate ID` and Caddy refused
  to load anything at all — not the TLS half, all of it. There is now a test
  that walks the document and checks.
- **With `automatic_https` disabled, nothing tells Caddy which names to issue
  for.** The TLS listener comes up holding no certificates, which looks like a
  broken TLS setup and is really an empty one. The names are listed explicitly.
- **Caddy writes outside its configured storage.** `autosave.json` and
  `instance.uuid` go to `%AppData%\Caddy` regardless — so deleting this
  installation would have left files behind describing it. Autosave is turned
  off (it is also how a stale configuration comes back, via `--resume`) and the
  rest is redirected by the variables each platform reads.

Firefox keeps its own trust store and will still warn. It reads the Windows
store only when `security.enterprise_roots.enabled` is on, which is a setting in
somebody's profile and not this tool's business to change — so it is said rather
than worked around.

### Fixed — one attempt was the wrong model

From a real Windows session: `install php 8.3` failing three times with
`[SSL] record layer failure` and `WinError 10054`, while `install php 8.4` went
through in between, on the same machine within the same minute. Every network
operation made exactly one attempt and reported the socket error verbatim.

A TLS handshake reset mid-record is what traffic inspection does to traffic it
dislikes, and it is intermittent by nature. The failure is not "the host is
down", it is "ask again".

- **Transient failures are retried**, five times with doubling backoff.
  `ssl.SSLError` is not a `URLError` and used to escape every handler there was,
  arriving as `[SSL] record layer failure (_ssl.c:2660)`.
- **A 404 is not.** `HTTPError` subclasses `URLError`, so retrying transient
  `URLError`s nearly meant retrying every missing file five times over fifteen
  seconds to say what was already known after the first. Only 408, 425, 429 and
  5xx are asked again.
- **Interrupted transfers resume.** Reconnecting alone does not help with a
  ninety-megabyte archive: a connection that keeps dropping will drop partway
  through, so starting again from nothing means never finishing however many
  attempts are allowed. `Range` turns a bad network into a slow one. A server
  that ignores it and answers 200 makes the partial file get thrown away rather
  than appended to, which would put the first bytes in twice.
- **A body that stops early is no longer taken for a finished download.** The
  socket closes, `read` returns nothing, and the loop used to end contentedly on
  a file missing its last thirty megabytes. Nothing downstream would have
  noticed for PostgreSQL, Redis or an archived PHP — the three the publisher
  gives no checksum for — and the short archive would simply have been unpacked.
  Found by a test written for resuming, which failed for this reason instead.
- **Giving up lists the attempts** rather than summarising them: five identical
  resets and five different errors mean different things. When they are all
  resets it says so, and mentions `HTTPS_PROXY`, which is the one setting that
  can help.

Verified against php.net: a real download cut off at five megabytes resumed and
finished, and the completed file matched the publisher's sha256.

### Added — `portable version` and `portable help`

`version` reports the build, the interpreter behind it, where it keeps things
and which decided that, and the daemon's version beside its own — saying so when
they differ, which after an upgrade explains every other oddity. It works with
nothing running, which is the state of a machine where the question comes up.

The client and the daemon now take that number from one place. Two constants can
drift, and then a mismatch means nothing.

`help` prints the overview on its own: every command grouped by what it is for,
with a worked example each. `--help` prints it too, after the generated list —
which no longer spills seventeen command names across the usage line. A test
asserts the overview mentions every command there is, and every runtime that can
be installed; `install` had advertised "php, caddy" for some time after four more
were added.

### Fixed — MariaDB when its download host cannot be reached

Reported from Windows in Russia: `WinError 10060` against
`downloads.mariadb.org`, a connect timeout rather than a reset, so retrying only
takes longer to fail.

`archive.mariadb.org` is a different host serving the same releases as a
directory listing, with `sha256sums.txt` beside each one — so the way round is
verified rather than merely available. It is used when the API host is
unreachable, and `PORTABLE_MARIADB_ARCHIVE` points at a mirror instead.

The archive says nothing about stability while the API marks each series Stable,
RC or Preview, so something has to stand in for that mark when choosing
`latest`. Maintenance history does: a series reaches its fifth patch release
after about a year of being looked after. That excludes a preview with one
release and a release candidate with two, today and in a year, and errs towards
an older series than the API would name — the right direction for something
chosen without being asked.

Naming a series that does not exist lists the maintained ones first rather than
the newest ten. The archive keeps every preview ever cut, and those crowd out
exactly the long-lived series somebody typing a wrong number is looking for.

