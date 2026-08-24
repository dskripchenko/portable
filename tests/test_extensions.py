"""
Switching PHP extensions on and off.

The file being edited is not ours. `php.ini` is written once when a PHP is
installed and then left alone forever, because somebody will edit it — that is
why it is on disk rather than generated at every start. So the property most of
these tests are really about is that an edit changes the one thing it came to
change and nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portable import extensions


def build(tmp_path: Path, *shipped: str) -> Path:
    """A PHP directory shaped like the real archive: every extension present."""
    ext = tmp_path / "php" / "ext"
    ext.mkdir(parents=True)

    for name in shipped:
        (ext / f"php_{name}.dll").write_text("", encoding="utf-8")

    return tmp_path / "php"


def ini_with(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "php.ini"
    target.write_text(body, encoding="utf-8")

    return target


class TestReading:
    def test_it_lists_what_the_build_carries(self, tmp_path):
        # From `ext/` rather than a list kept in this tool: which extensions
        # ship varies by version, and an adopted PHP was built by somebody else.
        directory = build(tmp_path, "gd", "intl", "curl")

        assert extensions.shipped(directory) == ["curl", "gd", "intl"]

    def test_a_commented_line_is_not_enabled(self, tmp_path):
        # Putting a `;` in front is how everybody disables one by hand. Reading
        # those as enabled reports the exact opposite of the truth.
        ini = ini_with(tmp_path, "extension = gd\n;extension = intl\n; extension = curl\n")

        assert extensions.enabled(ini) == ["gd"]

    def test_the_older_dll_spelling_is_the_same_extension(self, tmp_path):
        # `extension = php_gd.dll` is still valid and still common in inis
        # copied from the internet. Treating it as a different extension makes
        # `enable gd` add a second line that quietly fights the first.
        ini = ini_with(tmp_path, "extension = php_gd.dll\n")

        assert extensions.enabled(ini) == ["gd"]

    def test_zend_extensions_are_read_too(self, tmp_path):
        ini = ini_with(tmp_path, "zend_extension = opcache\n")

        assert extensions.enabled(ini) == ["opcache"]


class TestEnabling:
    def test_it_uses_the_directive_the_extension_needs(self, tmp_path):
        """
        A Zend extension loaded with `extension =` does not work.

        And the failure is memorable in the wrong way: PHP complains about the
        directive it was not given, so the message names `zend_extension` while
        the file plainly says `extension` — which reads like the message is
        about somebody else's problem.
        """
        directory = build(tmp_path, "opcache", "gd")
        ini = ini_with(tmp_path, "")

        extensions.enable(ini, "opcache", directory)
        extensions.enable(ini, "gd", directory)
        text = ini.read_text(encoding="utf-8")

        assert "zend_extension = opcache" in text
        assert "\nextension = gd" in text

    def test_it_revives_a_commented_line_where_it_stands(self, tmp_path):
        """
        Rather than appending a second one.

        Somebody who wrote `;extension = gd` put it where they wanted it, often
        under a comment saying why. A duplicate at the bottom leaves two lines
        disagreeing about which is in force — the answer being "the last one",
        which is what neither of them looks like.
        """
        directory = build(tmp_path, "gd")
        ini = ini_with(tmp_path, "; graphics, off for now\n;extension = gd\n\n[opcache]\n")

        assert extensions.enable(ini, "gd", directory) is True

        text = ini.read_text(encoding="utf-8")

        assert text.count("extension = gd") == 1
        assert text.index("extension = gd") < text.index("[opcache]"), "it moved to the end"
        assert "; graphics, off for now" in text

    def test_everything_else_in_the_file_survives(self, tmp_path):
        # The whole reason these are edits rather than a regeneration.
        directory = build(tmp_path, "gd")
        original = "; hand written\nmemory_limit = 1024M\n\n[opcache]\nopcache.jit = tracing\n"
        ini = ini_with(tmp_path, original)

        extensions.enable(ini, "gd", directory)
        text = ini.read_text(encoding="utf-8")

        for line in original.splitlines():
            assert line in text

    def test_enabling_twice_changes_nothing(self, tmp_path):
        directory = build(tmp_path, "gd")
        ini = ini_with(tmp_path, "extension = gd\n")

        assert extensions.enable(ini, "gd", directory) is False
        assert ini.read_text(encoding="utf-8") == "extension = gd\n"

    def test_an_extension_the_build_lacks_is_refused_with_what_it_has(self, tmp_path):
        """
        PHP does not fail loudly on this, which is the problem.

        `extension = xdebug` with no `php_xdebug.dll` beside it prints a startup
        warning to a log nobody is reading and carries on without it. The
        symptom arrives later as a function that does not exist, nowhere near
        the cause.
        """
        directory = build(tmp_path, "gd", "intl")
        ini = ini_with(tmp_path, "")

        with pytest.raises(extensions.UnknownExtension) as excinfo:
            extensions.enable(ini, "xdebug", directory)

        assert "gd" in str(excinfo.value)
        assert "intl" in str(excinfo.value)


class TestDisabling:
    def test_it_comments_the_line_rather_than_deleting_it(self, tmp_path):
        # The line often sits among settings belonging to the same extension,
        # and somebody turning something off for an afternoon should find it
        # where they left it.
        ini = ini_with(tmp_path, "extension = gd\ngd.jpeg_ignore_warning = 1\n")

        assert extensions.disable(ini, "gd") is True

        text = ini.read_text(encoding="utf-8")

        assert extensions.enabled(ini) == []
        assert "extension = gd" in text
        assert "gd.jpeg_ignore_warning = 1" in text

    def test_disabling_something_already_off_changes_nothing(self, tmp_path):
        ini = ini_with(tmp_path, ";extension = gd\n")

        assert extensions.disable(ini, "gd") is False


class TestReport:
    def test_it_names_an_extension_that_is_loaded_but_not_there(self, tmp_path):
        """
        The state worth having a name for.

        An ini loading something the build does not carry produces a startup
        warning and a PHP that runs without it. Without this, the only evidence
        is a missing function reported by an application, hours away from the
        ini line that caused it.
        """
        directory = build(tmp_path, "gd")
        ini = ini_with(tmp_path, "extension = gd\nextension = imagick\n")

        report = {entry["name"]: entry for entry in extensions.report(ini, directory)}

        assert report["gd"] == {"name": "gd", "enabled": True, "shipped": True, "zend": False}
        assert report["imagick"]["shipped"] is False
        assert report["imagick"]["enabled"] is True

    def test_it_lists_what_is_available_but_off(self, tmp_path):
        directory = build(tmp_path, "gd", "intl")
        ini = ini_with(tmp_path, "extension = gd\n")

        report = {entry["name"]: entry["enabled"] for entry in extensions.report(ini, directory)}

        assert report == {"gd": True, "intl": False}
