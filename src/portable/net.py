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
import urllib.parse
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


class RateLimited(RuntimeError):
    """GitHub is refusing to answer for now, with something to do about it."""


#: Where a token may be offered, in order.
#:
#: `GITHUB_TOKEN` last and deliberately: it is the name CI sets, so honouring it
#: means the build authenticates without being told to, while somebody wanting a
#: different one locally can say so without touching what CI relies on.
_TOKEN_NAMES = ("PORTABLE_GITHUB_TOKEN", "GITHUB_TOKEN")

_API_HOST = "api.github.com"


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """
    Strip the token when a redirect leaves the host it was meant for.

    Not a precaution against a hypothetical. GitHub's API answers a release
    asset with a redirect to `objects.githubusercontent.com`, and urllib repeats
    every header it was given — so an `Authorization` header added for
    `api.github.com` would be handed, verbatim, to a different host on the very
    first download.
    """

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)

        if redirected is not None and _host_of(newurl) != _host_of(request.full_url):
            redirected.headers = {
                name: value
                for name, value in redirected.headers.items()
                if name.lower() != "authorization"
            }

        return redirected


def _host_of(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or ""


def _headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}

    # Only for GitHub's API, and only when one is offered. Three of the catalogs
    # read release listings from there, and anonymous requests are limited to
    # sixty an hour **per address** — which behind a corporate NAT is sixty for
    # the building, not for the person. A token raises it to five thousand.
    if _host_of(url) == _API_HOST:
        token = next((os.environ[name] for name in _TOKEN_NAMES if os.environ.get(name)), None)

        if token:
            headers["Authorization"] = f"Bearer {token}"

    return headers


def open_url(url: str, timeout: int = TIMEOUT):
    """A GET with our user agent, verified. The caller closes it."""
    request = urllib.request.Request(url, headers=_headers(url))

    opener = urllib.request.build_opener(
        _DropAuthOnRedirect,
        urllib.request.HTTPSHandler(context=context()),
    )

    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code in (403, 429) and _host_of(url) == _API_HOST:
            raise RateLimited(_rate_limit_message(error)) from error

        raise
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


def _rate_limit_message(error: urllib.error.HTTPError) -> str:
    """
    What a 403 from GitHub's API actually means, and what to do.

    The raw message is "rate limit exceeded", which invites the reading that
    something here is asking too often. Usually it is not: the limit is sixty an
    hour per address, so behind a shared network it is sixty for everybody, and
    can be exhausted by people who have never run this.
    """
    remaining = error.headers.get("X-RateLimit-Remaining")
    limit = error.headers.get("X-RateLimit-Limit")
    counted = f" ({remaining} of {limit} left)" if remaining is not None else ""

    return (
        f"GitHub's API is refusing further requests{counted}.\n\n"
        f"Anonymous requests are limited to sixty an hour per address — behind a "
        f"shared or corporate network that is sixty for everyone on it, and it can "
        f"be used up by people who have never run this.\n\n"
        f"A token raises it to five thousand. Any GitHub token with no scopes at "
        f"all will do; it needs no permissions, only an identity:\n\n"
        f"    set PORTABLE_GITHUB_TOKEN=ghp_...\n\n"
        f"PHP itself is unaffected — it is published elsewhere. This limits Caddy, "
        f"PostgreSQL and Redis.\n"
        f"Underlying error: {error}"
    )


def read_text(url: str, timeout: int = TIMEOUT) -> str:
    with open_url(url, timeout=timeout) as response:
        return response.read().decode("utf-8")
