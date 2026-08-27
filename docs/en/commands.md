# Commands

Every command takes `--json`, and `--home PATH` to work against a different
installation for that one command. `portable help` prints a shorter version of
this page.

## The supervisor

| | |
|---|---|
| `portable up` | Start the supervisor. Everything else talks to it. |
| `portable down` | Stop it, and everything it runs. |
| `portable status` | What is running, on which port, and why nothing is being served if nothing is. |
| `portable version` | This build, the interpreter behind it, where things are kept, and the running daemon's version. |
| `portable help` | Every command, grouped, with an example each. |
| `portable dash` | A full-screen view: processes, sites, databases and live logs together. |
| `portable shell` | Run commands one after another without retyping `portable`. |
| `portable logs [name] [-f]` | What the supervised processes are writing. A name, or the start of one. |
| `portable upgrade [--check]` | Replace this tool with the newest release. |

`status` and `version` both work with nothing running — which is the state of a
machine where the question tends to come up.

`upgrade` downloads the newest release, verifies it against the digest published
beside it, and runs it once before touching anything that already exists. Only
then are the old files moved aside and the new ones put in their place.

The folder itself is never renamed, and that is deliberate: Windows refuses to
rename a directory that is any process's current directory, and when you are
upgrading it usually is one — the shell you typed the command into is standing
in it. Only the contents move. Anything in the folder that is not part of the
bundle stays exactly where it is, including the data directory when
`home set --beside` put it there.

The previous version is kept beside the new one until you delete it, and if the
exchange fails everything is put back. A tool that is merely out of date is a
great deal better than one that is not there.

`logs php -f` follows every worker of every PHP version at once, because a name
matches the start of a log as well as the whole of it — which is how somebody
thinks about a pool, rather than as `php-8.4.24-1` through `-4`. Lines are
labelled with who wrote them and coloured by how alarming they sound.

It reads the files directly, so it works with the daemon stopped, which is when
the question tends to be asked.

`shell` has no tab completion. `readline` is a Unix extension that Windows
builds of CPython do not carry, so completion would need a library, and the
bundle deliberately has no dependencies. The console provides history and arrow
keys.

`dash` shows everything at once because the answers are usually wanted together:
which worker died is a question about the log, whether it came back is a
question about the process table, and reading them in turn means alternating
between two commands while the thing you are watching moves.

You type into it as well. Commands go in at the bottom, without `portable` in
front, and what you typed and what it answered join the services' own output in
the order it happened — which is what makes "I added a site and then this
appeared in the log" something you can read rather than reconstruct. Arrows
recall what you typed before; there are inline suggestions as you go.

`F10` quits, `F5` refreshes, `F2` pauses the log. Function keys because the
letters belong to the command line now. `dash php` follows only PHP below.
Selecting a database in its table and pressing Enter opens a prompt at it.

The top-right corner carries a face, and it answers the same question the block
beside it does — is anything wrong — from further away: a shape changing is
visible across a room, a line of text is not.

```
|\---/|      |\---/|      |\---/|      |\---/|
| o o |      | o o |      | ^ ^ |      | x x |
|  >_ |      |  .. |      | \_/ |      |  >! |
'-----'      '-----'      '-----'      '-----'
 working      waiting       done        error

|\---/|      |\---/|      |\---/|      |\---/|
| o o |      | - - |      | O O |      | - - |
|  >_ |      |  >_ |      |  >o |      |  zZ |
'-----'      '-----'      '-----'      '-----'
 ready        blink      it vanished    stopped
```

It reports states rather than activities. Which command is running is already
on the bar below, by name and with the seconds counted; a face repeating that
would say the same thing less precisely, and one that changed on every action
would stop meaning "look at this".

- **waiting** — a command has said nothing for five seconds. Most of an install
  is spent waiting on somebody else's host, and the spinner turns the same
  either way: this is the difference between a slow download and a thirty
  second connect timeout that looks like a hang.
- **done** and **error** — what the command you typed came back with. A failure
  stays until something goes right, rather than fading on a timer, because a
  face that cheers up by itself reports the passage of time and not the state
  of anything.
- **it vanished** — the supervisor was there a second ago and nobody asked it to
  leave. That is news; being down when you opened the screen is not.
- **blink** — every twenty-five seconds, for a moment. The same argument the
  busy indicator makes: a still picture cannot tell attention from a wedged
  process.

It is the same figure as the logo, and it is ASCII on purpose — a terminal
without the font for box-drawing shows squares, and a mascot made of squares
makes the screen look broken at the moment it is meant to reassure. Below
twenty-four rows it goes away along with the three lines beside it: rows are
the scarce thing here, and a picture that costs a table row is a bad trade.


A few commands are declined from inside it, each with the reason: `upgrade`
replaces the folder the screen is running from, `purge` asks a question the
screen cannot put to you, and `logs -f` is what the pane already does.

It is the one part of the tool with libraries that are not the standard one.
Four of them, vendored into the bundle — `textual` names six more that nothing
here reaches for, and carrying those would add four and a half megabytes of
syntax lexers to a screen that highlights nothing. A test blocks their imports
and runs the dashboard anyway, so that stays true.

## Runtimes

| | |
|---|---|
| `portable available <runtime>` | What the publisher currently offers, with what is installed marked. |
| `portable available php 8.3` | That branch, including superseded patches from php.net's archive. |
| `portable install <runtime> [version]` | A branch (`8.4`), an exact version (`8.4.24`), or `latest`. |
| `portable install php --from C:\php` | Adopt a PHP already on this machine. |
| `portable runtimes` | What is installed. |
| `portable update [--install]` | Newer releases on the same line as what is installed. |
| `portable uninstall <runtime> <version>` | Delete one and reclaim its disk. |

Installable: `php`, `caddy`, `node`, `postgres`, `mariadb`, `redis`.

**Versions install alongside, never over.** Anything pinned to the old one keeps
working, which is why `uninstall` exists.

**Updates stay on their own line** — the newest `8.4.x` for an 8.4, never 8.5. A
PHP branch change brings deprecations to every site that pinned nothing, and a
PostgreSQL data directory belongs to the major that created it: 17 will not
start on 18's files. Crossing a line is done by naming the version.

**An adopted runtime is read and never written to.** The tool will not update it,
delete it, or add extensions to it. Removing a PHP that another installer
manages would be a surprising thing for this to do.

## Sites

| | |
|---|---|
| `portable site add <name> [path]` | Serve a directory at `<name>.localhost`. |
| `portable site add <name> <path> --exact` | Take the path literally, without looking for `public/`. |
| `portable site add <name> <path> --php 8.2` | Pin a version. The newest installed is the default. |
| `portable site list` | Sites and their addresses. |
| `portable site remove <name>` | Stop serving one. |
| `portable port 8888` | The port sites are served on. `auto` returns to trying 80, then 8080. |
| `portable trust` | Trust the local certificate authority, so `https://` stops warning. `--forget` undoes it. |

`trust` puts the local authority's root into **your** certificate store, which
is the only one reachable without administrator rights, and writes
`conf/ca-bundle.pem` — this machine's own trusted roots together with that root
— and points every installed PHP at it.

PHP, curl and Node read their own lists rather than the system store, which is
how a site opens green in Chrome while `file_get_contents('https://api.localhost')`
from that same site's code fails on the certificate. `portable run` and
`portable env` pass the same file to curl and Node.

The machine's own roots are in there deliberately: a bundle holding only the
local authority would make PHP trust `api.localhost` and reject every public
certificate. If those roots cannot be collected, nothing is written and `trust`
says so rather than leaving PHP trusting nobody.

If 443 and 8443 are both taken, no HTTPS listener is started at all — HTTP is
unaffected. `status` says so, and names what is holding them.

A chosen port is the **only** one tried. Falling back to 8080 after you asked
for 8888 would put the site at an address you did not pick and were not told
about — and the reason to choose one is that the defaults were not usable.

## Several versions at once

Every PHP version installed can serve at the same time. Each gets its own pool
of workers and its own `php.ini`; sites choose between them with `--php`, and a
site that pins nothing follows whichever is newest.

Databases work the same way but per instance rather than per version:
`--name` gives a second one its own data directory and port, and `--version`
pins which installed build it runs.

`purge` is what makes "delete the folder and it is gone" true again. Four things
can end up outside it — the data directory, a PATH entry, a trusted
certificate, and the copy an upgrade kept — and three of them nobody would
remember. It finds what is really there, lists it with sizes, asks, and removes
it.

It does not remove the folder itself: this is running from inside it, and
Windows will not delete a directory holding a running program. After it, that
folder is the only thing left.

## PHP extensions

| | |
|---|---|
| `portable ext list` | What this build ships, and which are loaded. |
| `portable ext enable <name>` | Load one the build already carries. |
| `portable ext disable <name>` | Stop loading it. |
| `portable ext install <name> [version]` | Fetch one the build does not carry — `xdebug`, `redis`, `imagick`. |

All of these take `--php 8.3` to pick which installed PHP they apply to.

Windows PHP ships every extension it supports as a separate DLL, all present and
none loaded — so `enable` is a line in `php.ini`, not a download. `install` is a
download, matched to the build's PHP branch, thread safety, compiler and
architecture, all four of which must agree or the extension silently does not
load.

Changing an extension replaces the workers, because each `php-cgi` reads
`php.ini` once at startup. `php.ini` itself is written when a PHP is installed
and never regenerated: edits to it survive everything here.

If the newest release of an extension has no build for your PHP, name an older
one. Xdebug 3 does not build for PHP 7.2 and never will; xdebug 2.9.8 does.

## Databases

| | |
|---|---|
| `portable service add <kind>` | Start `postgres`, `mariadb` or `redis`. |
| `portable service list` | What is running, and how to reach it. |
| `portable service cli [name]` | A prompt at one — `psql`, `mariadb`, `redis-cli`. |
| `portable service remove <name>` | Stop it. **The data is kept.** |

Several versions of the same database run side by side: each is a service with
its own name, version and port, and `service cli <name>` picks by name rather
than by version. `service list` shows the version each one is actually on —
which is not always the one it asked for, since a service declared without a
version follows the newest installed.

```powershell
portable service add postgres --name pg16 --version 16 --port 5432
portable service add postgres --name pg17 --version 17 --port 5433
portable service cli pg17
```

`service cli` runs the client that shipped in the same archive as the server,
pointed at the right port over TCP — and the client of the version that is
running, not whichever is newest on the machine. The three spell the same three facts —
host, port, user — three different ways, and MariaDB's client additionally
prefers a named pipe on Windows when the host looks local, which this server
does not offer. The name can be left out when there is only one.

In the dashboard, select a database in its table and press Enter: the screen
steps aside, the client gets the real terminal, and the screen comes back when
you leave it. `--json` prints the command instead of running it, which is what
an editor plugin wants.

Each binds the loopback only. `--port` and `--name` are there when you want a
second instance or a non-conventional port.

The data directory is initialised once, with a fixed `C` collation for
PostgreSQL — a database whose collation follows the machine's regional settings
sorts differently from production and finds out in a test that passes for one
person.

## Everything else

| | |
|---|---|
| `portable run <command>` | Run something with the installed runtimes on PATH, for that command only. |
| `portable env` | Print the settings a shell would need, instead of changing anything. |
| `portable home` | Where everything is kept, and what decided that. |
| `portable home set <path>` | Keep it somewhere else. `--beside` keeps it next to the launcher. |
| `portable proxy` | What everything outbound goes through, and who decided. |
| `portable proxy set <url>` | Send downloads, catalogues and update checks through it. |
| `portable proxy clear` | Follow the environment again. |
| `portable home clear` | Back to the default. |
| `portable path` | Whether this is on your PATH. |
| `portable purge` | Remove everything this has put outside its own folder. |
| `portable path add` | Put it there, for **you** — no administrator. `remove` undoes it. |

`proxy` is for the machine that needs one and has never had it exported.
`HTTPS_PROXY` is honoured as it always was; setting one here overrides it,
because two sources for one answer means the wrong one is in force half the
time and nothing says which.

```powershell
portable proxy set http://proxy.corp:3128
portable proxy set http://bob:secret@proxy.corp:3128    # if it wants a password
```

It covers everything this fetches: runtimes, PHP extensions, the publishers'
catalogues, GitHub's API and the update check. The scheme is `http://` even for
a proxy that fetches `https` — that address is how this reaches the proxy, not
how it reaches what is behind it. SOCKS is refused rather than accepted and
failed later: this speaks to proxies through Python's standard library, which
handles HTTP proxies only.

A password is stored as given and never printed: `proxy` and `version` show
`bob:***@`, so a pasted bug report does not carry it.


`path add` writes to `HKEY_CURRENT_USER` — your own environment, which needs no
administrator. The machine's PATH lives elsewhere and is never touched; there is
no option to touch it.

It is the only thing this tool writes outside its own directory, which is why it
is something you run rather than something an install does, and why `path
remove` puts it back exactly as it was.

`portable run node --version` will tell you if it is running a Node this tool
does not manage — the machine's own, found on PATH — because that is otherwise
an easy thing to spend twenty minutes on.
