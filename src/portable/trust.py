"""
Making the browser believe the certificates.

Caddy issues them from a certificate authority of its own, which is the right
answer for local development — no public name, no ACME, nothing outside the
machine. The catch is that nobody trusts that authority until told to, so every
site is a warning page until its root certificate is in the store the browser
reads.

Caddy will install it itself and warns that it "might prompt for password",
which is a system change requiring administrator rights on Windows. That is
switched off in the configuration, and installing the root happens here instead:
deliberately, into the **current user's** store, which needs no elevation.

Two things this cannot do, and says so rather than pretending:

- **Firefox keeps its own store.** It reads neither the Windows store nor the
  macOS keychain, so a certificate trusted everywhere else is still a warning
  page there. Firefox does read enterprise roots from the Windows store when
  `security.enterprise_roots.enabled` is on, which is a per-profile setting
  nothing here is entitled to change.
- **The trust dialog is Windows asking, not us.** Adding to the user's root
  store raises a confirmation the person has to accept. There is no flag that
  removes it, and a tool that found one would be doing something it should not.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from . import paths

#: Where Caddy's local authority keeps its root, under the storage it was given.
ROOT_CERTIFICATE = Path("pki") / "authorities" / "local" / "root.crt"

#: What the certificate is called in the user's store, so it can be found again.
FRIENDLY_NAME = "Caddy Local Authority - portable"


class TrustFailed(RuntimeError):
    """The certificate could not be trusted, with what happened."""


def storage() -> Path:
    """Where Caddy has been told to keep its authority."""
    return paths.root() / "caddy"


def root_certificate() -> Path:
    return storage() / ROOT_CERTIFICATE


def is_ready() -> bool:
    """
    Whether there is a root to trust yet.

    It does not exist until Caddy has run with TLS configured — the authority is
    created on first use, not on installation — so `trust` before the first site
    is added has nothing to work with, and should say that rather than fail
    obscurely.
    """
    return root_certificate().is_file()


def install() -> str:
    """
    Put the root into the current user's trust store. Returns what was run.

    The user's store rather than the machine's, everywhere. It is the only one
    reachable without elevation, and it is enough: browsers read it, and a
    certificate trusted for the person sitting at the machine is exactly the
    scope this deserves.
    """
    certificate = root_certificate()

    if not certificate.is_file():
        raise TrustFailed(
            f"There is no root certificate yet at {certificate}.\n"
            f"Caddy creates its authority the first time it serves a site over "
            f"HTTPS. Add a site, then trust it."
        )

    return _run(_install_command(certificate), certificate)


def forget() -> str:
    """Take it back out again."""
    certificate = root_certificate()

    return _run(_forget_command(certificate), certificate)


def _install_command(certificate: Path) -> list[str]:
    if platform.system() == "Windows":
        # `-user` is the whole point: without it this writes to the machine
        # store and fails without administrator rights.
        return ["certutil", "-user", "-addstore", "-f", "Root", str(certificate)]

    if platform.system() == "Darwin":
        return [
            "security",
            "add-trusted-cert",
            # The login keychain, not the system one. `-d` would mean the
            # system's, and would ask for a password this tool has no business
            # asking for.
            "-k",
            str(Path.home() / "Library" / "Keychains" / "login.keychain-db"),
            "-p",
            "ssl",
            str(certificate),
        ]

    # Linux has no single answer — `update-ca-certificates` needs root, and the
    # browsers read NSS databases that vary by distribution and profile.
    raise TrustFailed(
        f"Trusting a certificate automatically is not implemented for "
        f"{platform.system()}. The root is at {certificate}; add it to your "
        f"browser's authorities by hand."
    )


def _forget_command(certificate: Path) -> list[str]:
    if platform.system() == "Windows":
        return ["certutil", "-user", "-delstore", "Root", FRIENDLY_NAME]

    if platform.system() == "Darwin":
        return ["security", "remove-trusted-cert", str(certificate)]

    raise TrustFailed(f"Not implemented for {platform.system()}.")


def _run(command: list[str], certificate: Path) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError as error:
        raise TrustFailed(
            f"{command[0]} is not on PATH, so the certificate cannot be trusted "
            f"automatically. It is at {certificate}."
        ) from error
    except subprocess.TimeoutExpired as error:
        # Almost always the confirmation, waiting for somebody who is not
        # looking at it. Said plainly and by the right name for this platform,
        # because "timed out" alone sends people hunting for a network problem —
        # and naming the wrong operating system's dialog sends them further.
        asks = (
            "Windows raises a confirmation when a certificate is added to the "
            "root store"
            if platform.system() == "Windows"
            else "macOS asks for your login keychain password"
        )
        raise TrustFailed(
            f"{command[0]} did not finish. {asks} — check for a dialog waiting for "
            f"an answer."
        ) from error

    if result.returncode != 0:
        raise TrustFailed(
            f"{' '.join(command)} failed ({result.returncode}).\n"
            f"{(result.stderr or result.stdout).strip()}"
        )

    return " ".join(command)
