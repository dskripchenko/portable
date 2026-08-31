# Security

## Reporting something

Use [private vulnerability
reporting](https://github.com/dskripchenko/portable/security/advisories/new), or
write to denskrp90@gmail.com if that form is not available to you. Please do not
open a public issue for anything that would tell somebody how to attack an
installation before it can be fixed.

This is one person's project. Expect an answer in days rather than hours, and
expect to be told plainly if something is not going to be fixed and why.

## Which versions get fixes

The newest release, and only that one. Every version is a self-contained
archive and `portable upgrade` replaces one with the next, so there are no
supported lines to backport to.

## What is checked, and by what

**The archive you download is signed at the moment it is built.** From 1.9.0 on,
`gh attestation verify portable.zip -R dskripchenko/portable` reports the
repository, the workflow and the commit it came out of. The digest is published
beside every release as well, which proves delivery but not origin — the
checksum sits on the same page as the download.

**Everything the tool fetches afterwards is checked against its publisher's own
digest.** PHP against php.net, Caddy against its GitHub release, and so on. A
mismatch deletes the file rather than warning about it: everything downloaded
here is executed afterwards.

**The bundle's four vendored libraries are pinned by version and verified
against the digest PyPI publishes**, and audited weekly against the advisory
database by `.github/workflows/audit.yml`. The tool itself imports nothing
outside the standard library, which is why `pyproject.toml` declares no
dependencies — a scanner pointed at it would find an empty list and say nothing
useful about a forty-six megabyte archive.

**Every GitHub Action is named by commit rather than by tag.** A tag is a name
its owner can move, and the release workflow holds a token that writes releases.
Dependabot moves those commits, so a pin nobody updates does not quietly become
an old version with a tidy name.

**Each release is submitted to VirusTotal** and the report is linked from its
notes. A few engines out of some seventy will report something: this tool
downloads executables, unpacks them, runs a pool of detached processes and binds
port 80, and it ships an interpreter inside a zip. The report is published so
that can be read rather than argued about.

## What is not done

**The archive carries no code-signing certificate.** They are an annual cost and
this project takes no money, so SmartScreen will warn about the download and
Defender may hold it briefly. Nothing here removes that, and this documentation
will not tell anybody to switch protection off.

**`install.ps1` is fetched from the `main` branch**, which is the one link in
the chain with no digest in front of it: a commit to that file reaches everyone
who copies the one-liner out of the README. It is short and readable, and the
release archive it fetches is checked — but the script itself is trusted on the
strength of the branch it comes from.

**The tool binds a port and runs a control API on the loopback.** It is
token-authenticated and reachable only from the machine it runs on. It is not
built to be exposed, and nothing in it should be put in front of a network.
