# portable

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

Released and verified on real Windows: serving PHP through the pool, binding
port 80 as an ordinary user, surviving the console being closed, and replacing
itself with `upgrade`.

One limitation is measured rather than promised. A terminal that puts what it
starts into a **job object forbidding breakaway** takes the supervisor with it
when it closes — nothing at the process level can escape such a job, so
`portable up` says when it is in one instead of leaving it to be discovered.
See [troubleshooting](troubleshooting.md).

macOS and Linux are not targets. Every catalog resolves Windows archives, so the
tool runs elsewhere but installs binaries that machine cannot execute.
