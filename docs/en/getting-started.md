# Getting started

## Install

Download `portable-x86_64-pc-windows-msvc.zip` from the
[releases](https://github.com/dskripchenko/portable/releases) and unzip it
anywhere — a folder on your desktop, a second drive, a flash drive. There is
nothing to install and no installer to run.

The bundle carries its own Python. A tool whose job is putting runtimes on a
machine that has none cannot sensibly require one first, and on Windows there is
no Python by default at all: what looks like one is a shortcut that opens the
Microsoft Store.

```powershell
cd C:\portable
.\portable.cmd version
```

Run it from an ordinary PowerShell window. If anything ever asks for
administrator rights, that is a bug — please report it.

## Choose where it keeps things

```powershell
.\portable.cmd home                    # where, and what decided that
.\portable.cmd home set D:\portable    # somewhere else, from now on
.\portable.cmd home set --beside       # next to the launcher, so it travels with it
```

The default is `%LOCALAPPDATA%\portable`. On a managed machine that default can
be unusable rather than merely unwelcome: AppLocker is commonly configured to
deny execution from under a user's profile — that is where software installed
without administrator rights ends up, which is the point of the rule — and
everything downloaded here is an executable. Where that applies, nothing starts
until this is pointed somewhere execution is allowed.

`--beside` is for a flash drive. It records the word rather than today's path,
so the bundle keeps working when the drive letter changes.

## Start it

```powershell
.\portable.cmd up
```

This starts the supervisor, which owns everything else. It survives closing the
terminal and the IDE. It does not survive a reboot — that is deliberate, since
surviving one would mean an autostart entry, and this tool does not make those.

## Serve a site

```powershell
.\portable.cmd install php
.\portable.cmd install caddy
.\portable.cmd site add demo C:\projects\demo
```

Open `http://demo.localhost`. No hosts file was edited and no DNS server is
involved: Windows resolves anything under `.localhost` to the loopback by
itself.

If the project keeps its front controller in `public/` — Laravel, Symfony and
most others — that is what gets served, and the tool says so. Pointing a site at
a repository root would otherwise serve the application's source over HTTP,
`.env` included, while appearing to merely not work. Pass `--exact` to take the
path literally.

Pin a PHP version per site with `--php 8.2`; without it, a site follows whatever
is newest. Several versions run side by side, each with its own pool of workers.

## HTTPS

```powershell
.\portable.cmd trust
```

Sites are served over TLS as well, from a certificate authority Caddy runs
locally. `trust` puts that authority's root into **your** certificate store —
not the machine's, which would need administrator rights.

Windows will raise a confirmation dialog. That is Windows asking, not this tool,
and there is no way around it that should exist.

Firefox keeps its own store and will still warn. It reads the Windows store only
when `security.enterprise_roots.enabled` is switched on in `about:config`, which
is a setting in your profile and not this tool's business to change.

## Add a database

```powershell
.\portable.cmd install postgres
.\portable.cmd service add postgres
```

It starts on `127.0.0.1:5432`, user `postgres`, no password — bound to the
loopback, because trust authentication on a network-reachable port is how a
laptop on conference wifi becomes somebody else's.

`service remove` stops it and **keeps the data**. Adding it again picks up where
it left off.

`mariadb` and `redis` work the same way, on 3306 and 6379.

## Node, and other tools

```powershell
.\portable.cmd install node
.\portable.cmd run npm install
```

`portable run` puts the installed runtimes on PATH for that one command only.
Nothing about the machine's PATH is changed. If you want the settings for a
whole shell session instead, `portable env` prints them.

## Stop

```powershell
.\portable.cmd down
```

Everything stops: the router, the PHP workers, the databases. Ports are free
when the command returns.

To remove the tool entirely, delete the directory it keeps things in — `portable
home` says where that is — and the folder you unzipped. There is nothing else:
no registry keys, no services, no changes to PATH.
