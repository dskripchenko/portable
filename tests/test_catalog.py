"""
Resolving a version to something concrete enough to download.

Every test here runs against an index captured from the publisher, not a
hand-written one. A fixture invented by the author only proves the parser agrees
with the author's idea of the format — which is exactly the mistake that cost an
hour when Caddy's checksums turned out to be sha512 while looking like sha256.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from portable.catalog import CatalogError, caddy, php

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def php_index() -> dict:
    return json.loads((FIXTURES / "php-releases.json").read_text(encoding="utf-8"))


@pytest.fixture
def caddy_release() -> dict:
    return json.loads((FIXTURES / "caddy-release.json").read_text(encoding="utf-8"))


@pytest.fixture
def caddy_checksums() -> str:
    return (FIXTURES / "caddy-checksums.txt").read_text(encoding="utf-8")


class TestPhp:
    def test_it_resolves_a_branch_to_a_concrete_version(self, php_index):
        build = php.resolve("8.4", php_index)

        assert build.version.startswith("8.4.")
        assert build.version != "8.4", "a branch is not a version — it has to be resolved"
        assert build.url.endswith(".zip")

    def test_latest_is_the_newest_branch_not_the_first_key(self, php_index):
        # The index is a JSON object; relying on key order would work until the
        # day it does not.
        assert php.resolve("latest", php_index).version == php.resolve(
            php.branches(php_index)[0], php_index
        ).version

    def test_it_always_picks_the_non_thread_safe_build(self, php_index):
        # FastCGI runs one request per process, so thread safety costs
        # performance and buys nothing. Picking `ts` here would be invisible
        # until someone measured.
        for branch in php.branches(php_index):
            assert php.resolve(branch, php_index).variant.startswith("nts-")

    def test_it_reads_the_compiler_from_the_index_rather_than_assuming(self, php_index):
        # PHP 7.4 is vc15, 8.1 is vs16, 8.4 is vs17. A hardcoded `vs17` would
        # silently fail on the older branches — and the failure would surface as
        # an extension that will not load, far from its cause.
        compilers = {php.resolve(branch, php_index).variant for branch in php.branches(php_index)}

        assert len(compilers) > 1, "the fixture no longer covers more than one compiler"

    def test_every_build_carries_a_checksum(self, php_index):
        for branch in php.branches(php_index):
            build = php.resolve(branch, php_index)

            assert build.checksum, f"PHP {branch} resolved without a checksum"
            assert build.algorithm == "sha256"
            assert len(build.checksum) == 64

    def test_an_exact_version_resolves_to_itself(self, php_index):
        latest = php.resolve("latest", php_index)

        assert php.resolve(latest.version, php_index) == latest

    def test_a_superseded_version_comes_from_the_archive(self, php_index):
        """
        `8.3.20` is not in the index and is still downloadable.

        The index carries the current release of each branch and nothing else,
        so without this the versions a project is actually pinned to — always
        superseded, that being what pinning means — could not be installed at
        all.
        """
        listing = (FIXTURES / "php-archives.html").read_text(encoding="utf-8")
        build = php.resolve("8.3.20", php_index, archive=listing)

        assert build.version == "8.3.20"
        assert build.url.endswith("php-8.3.20-nts-Win32-vs16-x64.zip")

        # php.net publishes digests for current releases and none for archived
        # Windows builds. Said rather than quietly left empty: this is an
        # interpreter about to run everything on the machine.
        assert build.checksum is None

    def test_the_compilers_two_spellings_are_one_thing(self, php_index):
        """
        The listing has both `vc15` and `VC15`, for different releases.

        The variant is what PECL matches an extension against, so two spellings
        would mean `ext install xdebug` finding nothing for half the versions on
        offer — while the filename must still be used exactly as published or
        the download 404s.
        """
        listing = (FIXTURES / "php-archives.html").read_text(encoding="utf-8")

        upper = php.resolve("7.2.34", php_index, archive=listing)
        lower = php.resolve("7.4.30", php_index, archive=listing)

        assert upper.variant == "nts-vc15-x64"
        assert lower.variant == "nts-vc15-x64"
        assert "VC15" in upper.filename, "the published name must survive verbatim"
        assert "vc15" in lower.filename

    def test_a_version_that_never_existed_says_what_the_branch_has(self, php_index):
        listing = (FIXTURES / "php-archives.html").read_text(encoding="utf-8")

        with pytest.raises(CatalogError) as excinfo:
            php.resolve("8.3.999", php_index, archive=listing)

        message = str(excinfo.value)

        assert "8.3.999" in message
        # What that branch does have, newest first — the useful reply to a typo
        # or a version somebody half-remembers.
        assert "Archived in 8.3" in message
        assert message.count("8.3.") > 5

    def test_archived_patches_are_offered_per_branch(self, php_index):
        # Not all at once: the archive reaches back to 5.2 and holds three
        # hundred-odd builds, which answers nobody's question.
        listing = (FIXTURES / "php-archives.html").read_text(encoding="utf-8")
        offers = php.available(php_index, line="8.3", archive=listing)
        versions = [offer.version for offer in offers]

        assert versions[0] == "8.3.33", "the current release comes first"
        assert "8.3.20" in versions
        assert all(version.startswith("8.3.") for version in versions)

    def test_the_slug_distinguishes_variants(self, php_index):
        build = php.resolve("latest", php_index)

        assert build.version in build.slug
        assert build.variant in build.slug


class TestCaddy:
    def test_it_resolves_the_windows_archive(self, caddy_release):
        build = caddy.resolve(release=caddy_release)

        assert build.filename.endswith("_windows_amd64.zip")
        assert build.url.startswith("https://github.com/caddyserver/caddy/releases/download/")

    def test_the_version_is_the_tag_without_its_v(self, caddy_release):
        assert not caddy.resolve(release=caddy_release).version.startswith("v")

    def test_checksums_are_sha512_not_sha256(self, caddy_release, caddy_checksums):
        # The listing is indistinguishable from a sha256 one at a glance. It is
        # not one, and assuming otherwise produces a verification that never
        # matches and an error that reads like a corrupt download.
        build = caddy.resolve(release=caddy_release)
        found = caddy.checksum_for(build.filename, caddy_checksums)

        assert found is not None, "the published archive is absent from its own checksum file"

        checksum, algorithm = found

        assert algorithm == "sha512"
        assert len(checksum) == 128
        assert build.algorithm == algorithm, "the build and its digest disagree about the algorithm"

    def test_a_file_absent_from_the_listing_is_none_not_an_error(self, caddy_checksums):
        # "Cannot be verified" is a decision for the caller, not a crash here.
        assert caddy.checksum_for("caddy_9.9.9_windows_amd64.zip", caddy_checksums) is None

    def test_a_weak_digest_is_refused(self):
        listing = "da39a3ee5e6b4b0d3255bfef95601890afd80709  thing.zip"

        with pytest.raises(CatalogError):
            caddy.checksum_for("thing.zip", listing)

    def test_a_missing_windows_archive_names_what_was_offered(self):
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": "caddy_9.9.9_linux_amd64.tar.gz", "browser_download_url": "https://x/"},
            ],
        }

        with pytest.raises(CatalogError) as excinfo:
            caddy.resolve(release=release)

        assert "linux_amd64" in str(excinfo.value) or "no caddy_9.9.9_windows" in str(excinfo.value)


class TestOneListForEverybody:
    """
    The command line and the daemon must offer the same runtimes.

    They did not. `portable install postgres` was accepted by the parser and
    refused by the daemon, which knew only PHP and Caddy — so the databases
    could not be installed at all, and `service add postgres` then failed on a
    runtime there was no way to obtain. Two lists, edited at different times.
    """

    def test_everything_the_cli_offers_has_a_catalog(self):
        from portable import catalog, cli

        parser = cli._parser()
        commands = parser._subparsers._group_actions[0].choices

        for command in ("install", "available"):
            offered = next(
                action.choices
                for action in commands[command]._actions
                if action.dest == "runtime"
            )

            assert sorted(offered) == catalog.names(), f"`{command}` offers something else"

    def test_every_catalog_can_resolve_and_list(self):
        # The two things the daemon asks of a module. A catalog missing either
        # is one that fails at the moment somebody uses it rather than here.
        from portable import catalog

        for name, module in catalog.modules().items():
            assert callable(getattr(module, "resolve", None)), f"{name} cannot resolve"
            assert callable(getattr(module, "available", None)), f"{name} cannot list"

    def test_a_separately_published_digest_comes_back_the_same_shape(self):
        # Caddy and Node both publish digests in a second file, and the daemon
        # has one code path for that. A module returning a bare string instead
        # of (digest, algorithm) unpacks into two characters and verification
        # then fails against a checksum of "a".
        from portable import catalog

        for name, module in catalog.modules().items():
            if not hasattr(module, "checksum_url"):
                continue

            digest = "b" * 64
            found = module.checksum_for("thefile.zip", f"{digest}  thefile.zip\n")

            assert found is not None, f"{name} did not find a digest it published"
            assert isinstance(found, tuple) and len(found) == 2, f"{name} returned {found!r}"

    def test_an_unknown_name_lists_what_there_is(self):
        from portable import catalog
        from portable.catalog import CatalogError

        with pytest.raises(CatalogError) as excinfo:
            catalog.module("oracle")

        assert "postgres" in str(excinfo.value)


class TestGithubsRateLimit:
    """
    Three catalogs read release listings from GitHub's API.

    Anonymous requests are limited to sixty an hour **per address**, so behind a
    corporate NAT that is sixty for the building — and can be exhausted by
    people who have never run this. It took out a bundle build on CI, where the
    runner's address is shared with everybody else using GitHub Actions.
    """

    def test_a_token_is_offered_to_the_api(self, monkeypatch):
        from portable import net

        monkeypatch.setenv("PORTABLE_GITHUB_TOKEN", "ghp_example")

        assert net._headers("https://api.github.com/repos/x/y/releases")["Authorization"] == (
            "Bearer ghp_example"
        )

    def test_it_is_offered_to_nobody_else(self, monkeypatch):
        # php.net and nodejs.org have no use for a GitHub token and no business
        # being handed one.
        from portable import net

        monkeypatch.setenv("PORTABLE_GITHUB_TOKEN", "ghp_example")

        for url in ("https://downloads.php.net/x", "https://nodejs.org/dist/index.json"):
            assert "Authorization" not in net._headers(url)

    def test_a_redirect_to_another_host_loses_the_token(self, monkeypatch):
        """
        The reason this needs a handler rather than a header.

        GitHub's API answers a release asset with a redirect to
        `objects.githubusercontent.com`, and urllib repeats every header it was
        given — so the token would be handed, verbatim, to a different host on
        the very first download.
        """
        import urllib.request

        from portable import net

        original = urllib.request.Request(
            "https://api.github.com/repos/x/y/releases/assets/1",
            headers={"Authorization": "Bearer ghp_example", "User-Agent": "x"},
        )

        monkeypatch.setattr(
            urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, req, fp, code, msg, headers, newurl: urllib.request.Request(
                newurl, headers=dict(req.headers)
            ),
        )

        redirected = net._DropAuthOnRedirect().redirect_request(
            original, None, 302, "Found", {}, "https://objects.githubusercontent.com/thing"
        )

        assert not any(name.lower() == "authorization" for name in redirected.headers)

    def test_the_same_host_keeps_it(self, monkeypatch):
        import urllib.request

        from portable import net

        original = urllib.request.Request(
            "https://api.github.com/a", headers={"Authorization": "Bearer ghp_example"}
        )

        monkeypatch.setattr(
            urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, req, fp, code, msg, headers, newurl: urllib.request.Request(
                newurl, headers=dict(req.headers)
            ),
        )

        redirected = net._DropAuthOnRedirect().redirect_request(
            original, None, 302, "Found", {}, "https://api.github.com/b"
        )

        assert any(name.lower() == "authorization" for name in redirected.headers)

    def test_the_refusal_says_what_it_means(self):
        # "rate limit exceeded" invites the reading that this tool is asking too
        # often. Usually it is not asking at all.
        import urllib.error

        from portable import net

        message = net._rate_limit_message(
            urllib.error.HTTPError(
                "https://api.github.com/x", 403, "rate limit exceeded",
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "60"}, None,
            )
        )

        assert "per address" in message
        assert "PORTABLE_GITHUB_TOKEN" in message
        assert "0 of 60" in message
