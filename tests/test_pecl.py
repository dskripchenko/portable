"""
Extensions PHP does not ship, matched to the interpreter that will load them.

A PHP extension is loaded into the running process, so the PHP branch, the
thread safety, the compiler and the architecture must all agree with the build.
Getting one wrong is not a warning: `php-cgi` declines the module and carries on
without it, so the report arrives later as a function that does not exist.

The listings here were captured from downloads.php.net rather than written,
because the naming is the whole problem and only the real thing settles it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portable import pecl
from portable.catalog import CatalogError

FIXTURES = Path(__file__).parent / "fixtures"


def listing(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestVersions:
    def test_release_candidates_are_not_offered(self):
        # `5.3.7RC1` is in the listing and is never what `latest` should mean.
        versions = pecl.versions("redis", listing("pecl-redis-versions.html"))

        assert versions
        assert not [version for version in versions if not version.replace(".", "").isdigit()]

    def test_they_sort_by_number_and_not_as_text(self):
        """
        Otherwise 3.10 sorts before 3.5.

        Invisible until an extension reaches its tenth minor, and then `latest`
        quietly starts handing out a build a year old.
        """
        ordered = pecl.versions("x", '<a href="3.5.0/">a</a><a href="3.10.0/">b</a>')

        assert ordered == ["3.10.0", "3.5.0"]

    def test_a_name_that_does_not_exist_says_so(self):
        # The raw 404 this used to raise named neither the extension nor
        # anywhere to find the right spelling.
        with pytest.raises(CatalogError) as excinfo:
            pecl.resolve("nosuchthing", "8.4", "nts-vs17-x64", listings={"": ""})

        assert "nosuchthing" in str(excinfo.value)


class TestResolution:
    def listings(self) -> dict[str, str]:
        return {
            "": listing("pecl-xdebug-versions.html"),
            "3.5.3": listing("pecl-xdebug-3.5.3.html"),
        }

    def test_it_matches_the_build_exactly(self):
        build = pecl.resolve(
            "xdebug", php="8.4", variant="nts-vs17-x64", version="3.5.3",
            listings=self.listings(),
        )

        assert build.filename == "php_xdebug-3.5.3-8.4-nts-vs17-x64.zip"
        assert build.url.endswith(f"/xdebug/3.5.3/{build.filename}")

    def test_the_variant_is_used_verbatim_rather_than_rebuilt(self):
        """
        The reason the match is reliable.

        `nts-vs17-x64` is what php.net's index called the build and what PECL
        puts in the filename. Both sides took it from the same place, so they
        agree — rather than this tool assembling a string that happens to look
        right for the versions somebody tested.
        """
        with pytest.raises(CatalogError) as excinfo:
            pecl.resolve(
                "xdebug", php="8.4", variant="ts-vs16-x86", version="3.5.3",
                listings=self.listings(),
            )

        assert "8.4 ts-vs16-x86" in str(excinfo.value)

    def test_a_php_nobody_built_for_says_what_was_looked_at(self):
        # An extension's newest release does not always cover the newest PHP;
        # the maintainer builds when they build. Naming the versions tried is
        # what distinguishes that from a broken tool.
        with pytest.raises(CatalogError) as excinfo:
            pecl.resolve(
                "xdebug", php="9.9", variant="nts-vs17-x64",
                listings={"": listing("pecl-xdebug-versions.html"),
                          **{version: "" for version in pecl.versions(
                              "xdebug", listing("pecl-xdebug-versions.html"))}},
            )

        message = str(excinfo.value)

        assert "9.9" in message
        assert "3.5.3" in message, "it should say which versions it examined"

    def test_it_stops_walking_back_rather_than_asking_forever(self, monkeypatch):
        # Each step is a request. An extension with two hundred releases and no
        # Windows build for this PHP should fail in a moment, not eventually.
        asked = []
        many = "".join(f'<a href="1.{index}.0/">x</a>' for index in range(200))

        def record(name, version):
            asked.append(version)

            return ""

        monkeypatch.setattr(pecl, "_listing", record)

        with pytest.raises(CatalogError):
            pecl.resolve("thing", "8.4", "nts-vs17-x64", listings={"": many})

        assert len(asked) <= pecl.DEPTH
