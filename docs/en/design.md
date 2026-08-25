# How it works

## The supervisor owns everything; the command line is a client

A daemon holds the runtimes, the processes and the configuration, and exposes a
token-authenticated API on the loopback. `portable` is the first client of that
API. An IDE plugin will be the second, with nothing to retrofit, because there
is no functionality reachable only from the command line.

That rule is worth defending against the recurring temptation to "just do this
bit directly" because a round trip seems excessive for something small. Every
exception becomes a gap in the plugin later.

The token is not ceremony. The API starts processes and is reachable by anything
running as this user, including an `npm install` postinstall script. Binding the
loopback keeps other machines out and does nothing about local ones, so
possession of the discovery file is what authorises a caller.

## There is no php-fpm on Windows

FPM is a Unix-only SAPI. The Windows build of PHP ships `php-cgi.exe`, which
speaks FastCGI when given an address and **serves one request at a time**. It
cannot fork children of its own: `PHP_FCGI_CHILDREN` needs `fork()`, which
Windows does not have.

So concurrency is N separate processes on N separate ports, and the router
balances across them. Process supervision is therefore the core of this tool
rather than plumbing around it.

Two consequences that look like bugs otherwise:

- **A worker exiting is normal.** `PHP_FCGI_MAX_REQUESTS` makes it retire
  deliberately after a set number of requests, as memory hygiene. Restarting it
  is the design, not error recovery.
- **The pool size is the concurrency limit.** With four workers, a fifth
  simultaneous request waits. There is no queue inside `php-cgi` to absorb it,
  so a request is retried across the pool rather than failing when it lands on a
  worker that is retiring.

## Caddy, not nginx

nginx's own documentation says its Windows build uses "only the `select()` and
`poll()` connection processing methods, so high performance and scalability
should not be expected", and is "considered to be a beta version".

Caddy is a maintained native binary with an admin API, so adding a site is one
HTTP call to a running server rather than a restart that drops every connection
in flight. It also brings a local certificate authority, which is what makes
HTTPS possible without touching the machine's trust store.

## `*.localhost`, not `.test`

Windows and macOS both resolve anything under `.localhost` to the loopback on
their own. Nothing is written to the hosts file and no DNS server is involved,
which is what keeps the "no administrator" promise intact.

`.test` resolves nowhere without a hosts entry or a DNS server, and both cost
privileges this tool does not have.

## Runtimes come from their publishers, verified

Versions resolve against the publisher's own index — php.net's `releases.json`,
GitHub release listings — and archives are checked against the digests those
publishers list. A mismatch deletes the file: everything downloaded here is
executed afterwards.

Where a publisher offers no digest, that is recorded and reported rather than
glossed over. PostgreSQL's Windows builds, the Redis rebuild, PECL's Windows
extensions and php.net's archived releases all arrive unverified, and the tool
says so each time.

Fixtures for the tests are captured from the publishers rather than written by
hand. A hand-made fixture only proves the parser agrees with its author, and the
real ones have repeatedly disagreed: Caddy publishes **sha512** in a file that
looks exactly like a sha256 listing; MariaDB advertises plain HTTP and files its
digest under `sha256sum`; php.net's archive spells the same compiler `vc15` and
`VC15` for different releases of the same branch.

## Both downloaded and discovered runtimes are managed

A PHP this tool installed and a PHP already on the machine are the same kind of
thing to the supervisor. That is not a fallback — it is how anyone needing an
extension the prebuilt binaries lack stays unblocked, and how a machine with a
working PHP avoids fetching a second one.

An adopted runtime is read and never written to. This tool will not update it,
delete it, or install extensions into it.

## One directory, and it is yours to choose

Runtimes, configuration, logs, database files, Caddy's certificate authority —
all of it under one directory. Deleting that directory uninstalls the tool
completely.

Keeping that true takes work rather than tidiness. Caddy's own default is to
write its authority and its autosaved configuration under `%AppData%`, outside
the installation entirely; both are redirected, and the autosave is switched off
because it is also how a stale configuration comes back.

The location is settable because the default is not always usable: AppLocker is
commonly configured to deny execution from under a user's profile, and
everything downloaded here is an executable.

## HTTPS without elevation

Caddy issues certificates from a local authority. Given the chance it installs
that authority's root into the machine's trust store itself, warning that it
"might prompt for password" — a system change needing administrator rights.

That is switched off. `portable trust` puts the root into the **current user's**
store instead, which needs no elevation and is enough: a certificate trusted for
the person at the machine is exactly the scope this deserves.

HTTPS never takes HTTP down. The TLS port is chosen from what is free, and when
nothing is, TLS is simply not configured — a TLS listener on a taken port makes
Caddy fail to start entirely, and HTTP is the product while HTTPS is a
convenience.

## The network is assumed to be bad

Every network operation is retried through transient failures, and interrupted
downloads resume where they stopped. This is not defensive decoration: on a
network where TLS handshakes are reset partway through, a single attempt fails
most of the time, and restarting a ninety-megabyte archive from nothing means
never finishing it.

A body that stops early is not taken for a finished download either. The socket
closes, the read returns nothing, and without a length check the loop ends
contentedly on a file missing its last thirty megabytes — which nothing
downstream would notice for the three runtimes whose publishers offer no
checksum.
