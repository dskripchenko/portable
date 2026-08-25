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
| `portable shell` | Run commands one after another without retyping `portable`. |
| `portable logs [name] [-f]` | What the supervised processes are writing. A name, or the start of one. |
| `portable upgrade [--check]` | Replace this tool with the newest release. |

`status` and `version` both work with nothing running — which is the state of a
machine where the question tends to come up.

`upgrade` downloads the newest release, verifies it against the digest published
beside it, and runs it once before touching anything that already exists. Only
then is the old installation moved aside and the new one put in its place — by
the system's own shell, from a script outside both, because Windows will not
rename a directory containing a running program.

The previous version is kept beside the new one until you delete it, and if the
exchange fails the old one is put back. A tool that is merely out of date is a
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
| `portable service remove <name>` | Stop it. **The data is kept.** |

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
| `portable home clear` | Back to the default. |
| `portable path` | Whether this is on your PATH. |
| `portable path add` | Put it there, for **you** — no administrator. `remove` undoes it. |

`path add` writes to `HKEY_CURRENT_USER` — your own environment, which needs no
administrator. The machine's PATH lives elsewhere and is never touched; there is
no option to touch it.

It is the only thing this tool writes outside its own directory, which is why it
is something you run rather than something an install does, and why `path
remove` puts it back exactly as it was.

`portable run node --version` will tell you if it is running a Node this tool
does not manage — the machine's own, found on PATH — because that is otherwise
an easy thing to spend twenty minutes on.
