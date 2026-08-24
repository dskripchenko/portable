"""
Fetching things over HTTPS, in the networks this tool actually runs in.

Certificates are verified. The one accommodation is `PORTABLE_CA_BUNDLE`, and it
is not a nicety: a tool whose reason for existing is "installs without
administrator rights" will spend its life on managed corporate machines, and
those very often terminate TLS at a proxy with a private authority. On Windows
that authority is usually already in the system store and Python finds it; on a
machine where it is not, refusing to work at all would be the wrong answer, and
so would skipping verification.

There is no switch to disable verification, and there should not be. Everything
downloaded here is executed afterwards.
"""

from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "portable (+https://github.com/dskripchenko/portable)"

TIMEOUT = 30


def context() -> ssl.SSLContext:
    """
    The default verifying context, plus a private authority when one is named.

    `create_default_context()` loads the platform's own trust store, which on
    Windows means the certificates the machine's administrator installed —
    including the proxy's, when there is one. `PORTABLE_CA_BUNDLE` is for the
    case where it does not.
    """
    bundle = os.environ.get("PORTABLE_CA_BUNDLE")

    if bundle:
        path = Path(bundle).expanduser()

        if not path.exists():
            raise FileNotFoundError(
                f"PORTABLE_CA_BUNDLE points at {path}, which does not exist."
            )

        return ssl.create_default_context(cafile=str(path))

    return ssl.create_default_context()


class TrustError(RuntimeError):
    """The certificate could not be verified, with something to do about it."""


def open_url(url: str, timeout: int = TIMEOUT):
    """A GET with our user agent, verified. The caller closes it."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        return urllib.request.urlopen(request, timeout=timeout, context=context())
    except urllib.error.URLError as error:
        if not isinstance(error.reason, ssl.SSLCertVerificationError):
            raise

        raise TrustError(f"{_why(url)}\n\nUnderlying error: {error.reason}") from error


def _why(url: str) -> str:
    """
    A diagnosis, not a guess.

    These are two different failures wearing the same message. An empty trust
    store fails against every host on the internet, and being told to go and
    find the corporate proxy's root certificate — when there is no proxy —
    sends somebody looking for a thing that does not exist. The two are told
    apart by counting what was loaded, which costs nothing and is exact.
    """
    if not context().get_ca_certs():
        return (
            f"The certificate for {url} could not be verified, and this Python "
            f"has no trusted root certificates at all — not one. So this is not "
            f"about that host: nothing would verify.\n\n"
            f"That is the interpreter's own trust store being empty rather than "
            f"anything to do with the network. Point at a bundle of roots:\n\n"
            f"    PORTABLE_CA_BUNDLE=$(python -c 'import certifi; print(certifi.where())')\n\n"
            f"On Windows this does not happen: Python reads the system store."
        )

    return (
        f"The certificate for {url} could not be verified.\n\n"
        f"On a managed network this usually means TLS is being terminated by a "
        f"proxy whose authority this machine does not trust. Export the proxy's "
        f"root certificate and point at it:\n\n"
        f"    PORTABLE_CA_BUNDLE=C:\\path\\to\\corporate-root.pem"
    )


def read_text(url: str, timeout: int = TIMEOUT) -> str:
    with open_url(url, timeout=timeout) as response:
        return response.read().decode("utf-8")
