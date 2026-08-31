# Getting started

## Install

In PowerShell, from an ordinary window:

```powershell
irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
```

To choose where it goes, or which version:

```powershell
$env:PORTABLE_INSTALL_DIR = 'D:\portable'
$env:PORTABLE_VERSION = '0.1.1'
irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
```

Piped into `iex` rather than saved and run, and that is not a stylistic
preference: under the `Restricted` execution policy — the default on a machine
nobody has changed, and the setting most often enforced on a managed one — a
`.ps1` file on disk will not run, while a string does.

It needs nothing that a stock Windows does not already have, checks the bundle
against the checksum published beside it, runs it once before saying it worked,
and refuses to install over an existing copy — for which there is
`portable upgrade`.

### Running it from anywhere

Putting the install directory on your PATH works, and nothing else is needed:
the launcher finds its own interpreter and its own settings from where it sits,
not from where you are standing. `portable site add demo .` then means the
directory you are in, and `portable run` runs there too.

Nothing takes that directory hostage, either. The daemon and everything it
supervises stand in the installation's own folder — a process holds its working
directory open, and on Windows a folder held open cannot be deleted or renamed,
with Explorer saying only that it is "open in another program".

**Your PATH is not touched.** The tool's promise is that deleting its directory
removes it completely, and an entry in PATH would be an exception to that. Set
`$env:PORTABLE_ADD_TO_PATH = '1'` if you want one anyway; it is the only thing
the installer writes outside the install directory.

### If PowerShell is blocked

Some machines lock PowerShell down rather than the tool — AppLocker and WDAC put
it into Constrained Language Mode, where even `Get-FileHash` and
`Expand-Archive` refuse to run. Nothing above is required. From `cmd`:

```bat
curl -fsSL -o portable.zip https://github.com/dskripchenko/portable/releases/latest/download/portable-windows-x64.zip
curl -fsSL -o portable.zip.sha256 https://github.com/dskripchenko/portable/releases/latest/download/portable-windows-x64.zip.sha256

certutil -hashfile portable.zip SHA256
type portable.zip.sha256

tar -xf portable.zip
```

Compare the two hashes yourself, then run the `portable.cmd` inside the folder
that appeared. No script is executed at any point, so no execution policy, no
language mode and no script rule applies. `curl.exe`, `tar.exe` and
`certutil.exe` have all been in `System32` since Windows 10 1803.

The `-f` matters: without it curl saves the "not found" page as the archive and
reports success, and `tar` then complains about the archive rather than the URL.

### Or download it by hand

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

### Checking what you downloaded

Three things can be checked, answering three different questions.

**The checksum — did the file arrive whole.** The `.sha256` file beside each
archive, compared with `certutil` as above. The PowerShell installer does this
by itself and refuses a file that does not match. What it cannot tell you is
where the archive came from: the checksum sits on the same page as the download,
so whoever could replace one could replace the other.

**The attestation — where it came from.** Every release from 1.9.0 on is signed
through Sigstore as it is built, on a GitHub runner, with a short-lived identity
rather than a key anybody keeps:

```powershell
gh attestation verify portable.zip -R dskripchenko/portable
```

The answer names the repository, the workflow and the commit the archive was
built from. It needs the [GitHub CLI](https://cli.github.com), which is a
separate download and may itself be blocked here — the checksum stays available
in that case.

**The VirusTotal report — what the scanners say.** Linked from each release's
notes, across some seventy engines.

Expect a few detections rather than none. This tool downloads executables,
unpacks them, runs a pool of detached processes and binds port 80, and it ships
an interpreter inside a zip — which is the shape of a packer. Heuristics are
built to notice precisely that. The report is there to be read, not to prove
anything clean.

**The archive carries no code-signing certificate.** Those are an annual cost
and this project takes no money, so SmartScreen will warn about the download.
Nothing here changes that, and turning protection off would be a worse trade
than the warning.

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

To remove it entirely:

```powershell
.\portable.cmd purge
```

That takes back everything outside this folder — the data directory wherever you
put it, the PATH entry if you added one, the certificate if you trusted it, and
any copy an upgrade kept. It lists what it found, with sizes, and asks first.

Then delete the folder you unzipped, and none of it was ever there. No registry
keys, no services, nothing left behind.
