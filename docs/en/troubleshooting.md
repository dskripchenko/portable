# When something goes wrong

Start with these two. Between them they answer most questions.

```powershell
.\portable.cmd status        # what is running, and why nothing is served if nothing is
.\portable.cmd version       # this build, the interpreter, the running daemon
```

The logs are in the `logs` folder of wherever `portable home` says things are
kept — one file per process.

## `php-cgi.exe` will not start

The message names PHP and shows the end of that worker's log. On Windows the
usual cause is the **Visual C++ Redistributable**: php.net's builds link against
it, and its absence is reported as `VCRUNTIME140.dll not found`.

Installing it needs administrator rights, which this tool does not ask for. If
you cannot install it, `portable install php --from C:\some\other\php` will
adopt a PHP that already works on the machine.

## Nothing is being served

`portable status` says why. A supervisor that is up, lists its sites, runs its
workers and answers everything except that question is worse company than one
that is down, so the reason is kept and reported there.

Most often it is the port.

## Caddy will not start on port 80 or 8080

In order of how often it turns out to be true:

1. **Another local stack is running** — Laragon, XAMPP, Docker Desktop. Those
   take 80 and 8080 together, which is what this looks like.
2. **IIS or the World Wide Web Publishing Service** holds 80.
3. **The port is inside a range Windows has reserved.** Nothing is listening and
   binding still fails. `netsh interface ipv4 show excludedportrange protocol=tcp`
   lists them — Hyper-V and WSL reserve ranges dynamically.

```powershell
netstat -ano | findstr ":80 "     # names the process holding it
.\portable.cmd port 8888          # or simply move
```

A port that cannot be bound is not stored: the previous one is put back and your
sites return to it.

### It says the port is taken and something else is answering on it

Worth knowing about: a program can hold a port on IPv4 while Caddy takes IPv6,
or the other way round. Both "succeed", and requests to `127.0.0.1` reach the
other program. The tool checks by asking the port whether the answer is its own,
which is why it sometimes refuses a port that appeared to bind.

## Downloads fail — `10054`, `10060`, `record layer failure`

```
SSLError: [SSL] record layer failure (_ssl.c:2660)
URLError: <urlopen error [WinError 10054] ...>
```

A TLS handshake reset partway through, repeatedly, is usually something between
your machine and the host rather than either end — traffic inspection, or a
filtering proxy. It is intermittent by nature: the same command often succeeds
on the next attempt.

Everything is retried five times with growing pauses, and interrupted transfers
resume where they stopped rather than starting again — which is what makes a
ninety-megabyte archive arrive at all on a connection that keeps dropping.

Five in total, counted once. Until 1.3.0 the download retried the connection
that had already given up after five, so a host that could not be reached was
asked twenty-five times, each waiting out its own timeout.

If it still fails, the message lists every attempt. Five identical resets and
five different errors mean different things.

If this machine reaches the internet only through a proxy, tell it so:
`portable proxy set http://proxy.corp:3128`. `HTTPS_PROXY` is honoured as it
always was, and a proxy set here overrides it. `portable version` shows which
one is in force, without the password.

**`WinError 10060` against `downloads.mariadb.org`** is a connect timeout rather
than a reset — that host is simply unreachable from some networks, and retrying
only takes longer to fail. MariaDB comes from `archive.mariadb.org` instead,
which serves the same releases with checksums beside them.

Both the version list and the download fall back to it, which they did not
always: the list is a few kilobytes and often gets through where a hundred
megabytes does not, so an install could resolve a version perfectly well and
then fail to fetch it. `PORTABLE_MARIADB_ARCHIVE` points at a mirror of your
own.

## `SSLCertVerificationError`, or "the certificate could not be verified"

On a managed network this usually means TLS is terminated by a proxy whose
authority this machine does not trust. Export that authority's root certificate
and point at it:

```powershell
$env:PORTABLE_CA_BUNDLE = "C:\path\to\corporate-root.pem"
```

There is no switch to skip verification, and there should not be: everything
downloaded here is executed afterwards.

If the message says this Python has **no** trusted roots at all, that is a
different problem — the interpreter's own store being empty, which does not
happen on Windows, where Python reads the system store.

## GitHub says the rate limit is exceeded

Caddy, PostgreSQL and Redis are resolved through GitHub's API, which allows
sixty anonymous requests an hour **per address**. Behind a corporate NAT that is
sixty for the building, and can be used up by people who have never run this.

```powershell
$env:PORTABLE_GITHUB_TOKEN = "ghp_..."
```

Any token with no scopes at all will do. It needs no permissions, only an
identity. PHP is unaffected — it is published elsewhere.

## `https://` still warns in Firefox

Firefox keeps its own certificate store and reads neither the Windows store nor
anything this tool can reach. It will read the Windows store if
`security.enterprise_roots.enabled` is switched on in `about:config`, which is a
setting in your profile and not this tool's business to change.

Chrome, Edge and anything else reading the system store are covered by
`portable trust`.

## The supervisor died when I closed the terminal

Then that terminal put it in a **job object** it was not allowed to leave, and
`portable up` will have said so when it started:

> This terminal put it in a job it could not leave, so closing this window may
> stop it.

A job object is how a launcher makes sure everything it started is cleaned up
when it quits, and some editors use one for their run configurations. Windows
lets a process step out of a job only if the job permits it — a flag the job's
creator sets — and against one that does not, nothing at the process level can
escape. Measured on Windows both ways, and tested on every run.

Start it from a plain PowerShell window and it survives that window closing.

Everything else about detaching is handled: the console and the process group
are left behind in all cases, which is what an ordinary terminal closing takes
with it.

## An extension is enabled and PHP does not have it

`portable ext list` marks it **MISSING**: the `php.ini` loads something this
build does not carry. PHP does not fail on that — it warns at startup, into a
log, and runs without the extension — so the symptom otherwise arrives hours
later as a function that does not exist.

`portable ext install <name>` fetches the real thing, matched to this build.

## I moved the installation and everything disappeared

Changing where things are kept moves nothing. The old directory is reported when
you change it, with what is still in it — copy it across, or reinstall and delete
it. Nothing was lost.

## Reporting something

`portable status --json`, `portable version --json`, and the relevant file from
the `logs` folder. If the tool showed you a log tail in an error message, that
tail is usually the whole of it.
