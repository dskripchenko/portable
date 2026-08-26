<img src="../logo.svg" alt="portable" width="240">

[![tests](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/tests.yml?branch=main&label=tests)](https://github.com/dskripchenko/portable/actions/workflows/tests.yml)
[![locked-down install](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/install.yml?branch=main&label=locked-down%20install)](https://github.com/dskripchenko/portable/actions/workflows/install.yml)
[![tag](https://img.shields.io/github/v/tag/dskripchenko/portable?label=tag&sort=semver)](https://github.com/dskripchenko/portable/tags)
[![release](https://img.shields.io/github/v/release/dskripchenko/portable?label=release)](https://github.com/dskripchenko/portable/releases/latest)
[![license](https://img.shields.io/github/license/dskripchenko/portable?label=license)](https://github.com/dskripchenko/portable/blob/main/LICENSE)

A local development environment for Windows — PHP, Caddy, PostgreSQL, MariaDB,
Redis, Node — that installs beside the system rather than into it.

- [Getting started](getting-started.md) — install it, serve a site, add a database
- [Commands](commands.md) — every command, with what each one is for
- [How it works](design.md) — the decisions worth knowing about
- [When something goes wrong](troubleshooting.md) — the failures you are most
  likely to meet, and what they mean

## What "beside the system" means

Every one of these is a constraint the tool is built under, not an aspiration:

- **No administrator rights.** Not at install, not at runtime, not ever.
- **No `hosts` file.** Sites are reached at `*.localhost`, which Windows
  resolves to the loopback on its own.
- **No services, no autostart.** The supervisor is a process you start. It
  survives closing the terminal and the IDE; it does not survive a reboot.
- **No registry, no PATH, no system directories.** Everything lives in one
  directory. Deleting it uninstalls the tool completely.

The result runs on a locked-down corporate machine, which is precisely where
this class of tool usually cannot be installed at all.

## Status

In daily use on real Windows: serving PHP through the pool, binding port 80 as
an ordinary user, surviving the console being closed, the dashboard, and
replacing itself with `upgrade` — that last one only since 1.3.2, having passed
its tests for months while never once finishing on a real machine. See the
[note at the end of the project README](../../README.md) for what that was.

One limitation is measured rather than promised. A terminal that puts what it
starts into a **job object forbidding breakaway** takes the supervisor with it
when it closes — nothing at the process level can escape such a job, so
`portable up` says when it is in one instead of leaving it to be discovered.
See [troubleshooting](troubleshooting.md).

macOS and Linux are not targets. Every catalog resolves Windows archives, so the
tool runs elsewhere but installs binaries that machine cannot execute.
