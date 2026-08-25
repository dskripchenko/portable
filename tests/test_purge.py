"""
Taking back everything this tool has left outside its own folder.

The README has promised from the first day that deleting one directory removes
it completely. That was true and quietly stopped being so: the data directory
can be moved elsewhere, `path add` writes to the registry, `trust` puts a
certificate in the user's store, and `upgrade` leaves the previous version
beside the new one.

Four things, three of which nobody would remember — so they are found rather
than described in a document somebody would have to read at exactly the right
moment.
"""

from __future__ import annotations

import json
from pathlib import Path

from portable import cli, paths, purge


def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "python" / "bin").mkdir(parents=True)
    (root / "portable").write_text("", encoding="utf-8")

    return root


class TestFinding:
    def test_the_data_directory_is_found_wherever_it_is(self):
        # Which is the point: `home set D:\portable` puts it on another drive,
        # and nobody deleting a folder on their desktop would think of it.
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)
        (paths.root() / "runtimes" / "php.exe").write_text("x" * 2048, encoding="utf-8")

        found = purge.find(bundle=None)

        assert [trace.kind for trace in found] == ["data"]
        assert found[0].what == str(paths.root())
        assert found[0].size > 0

    def test_a_certificate_that_was_never_created_is_not_listed(self):
        """
        Only what is really there.

        Listing a certificate that was never trusted would make the answer to
        "what will this delete" longer than the truth, and a list you have to
        discount parts of is one nobody reads.
        """
        paths.root().mkdir(parents=True, exist_ok=True)

        assert [trace.kind for trace in purge.find(bundle=None)] == ["data"]

    def test_a_certificate_that_was_is(self):
        from portable import trust

        trust.root_certificate().parent.mkdir(parents=True, exist_ok=True)
        trust.root_certificate().write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

        assert "certificate" in [trace.kind for trace in purge.find(bundle=None)]

    def test_versions_an_upgrade_left_behind_are_found(self, tmp_path):
        # `upgrade` keeps the previous one deliberately, so that a bad release
        # is one rename away from being undone. It should not keep it forever.
        root = bundle(tmp_path)
        (root.parent / f"{root.name}.0.1.2.old").mkdir()
        (root.parent / f"{root.name}.0.1.3.old").mkdir()

        found = [trace for trace in purge.find(root) if trace.kind == "previous"]

        assert len(found) == 2

    def test_nothing_at_all_is_a_valid_answer(self, tmp_path):
        # A machine where the tool was unpacked and never run: no data
        # directory, nothing trusted, nothing on the PATH.
        import shutil

        shutil.rmtree(paths.root(), ignore_errors=True)

        assert purge.find(bundle(tmp_path)) == []


class TestRemoving:
    def test_each_thing_is_taken_back(self, tmp_path):
        root = bundle(tmp_path)
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)
        old = root.parent / f"{root.name}.0.1.2.old"
        old.mkdir()

        for trace in purge.find(root):
            trace.remove()

        assert not paths.root().exists()
        assert not old.exists()

    def test_one_refusing_does_not_leave_the_others(self, tmp_path, capsys, monkeypatch):
        """
        A file locked by something else should cost that one thing, not all of
        them — and the reader needs to know which one it was.
        """
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)
        old = tmp_path / "stubborn"
        old.mkdir()

        traces = [
            purge.Trace(
                kind="stubborn",
                what=str(old),
                detail="",
                remove=lambda: (_ for _ in ()).throw(OSError("in use by another program")),
            ),
            *purge.find(bundle=None),
        ]

        monkeypatch.setattr(purge, "find", lambda bundle=None: traces)

        assert cli.main(["purge", "--yes"]) == 0

        printed = capsys.readouterr()

        assert "in use by another program" in printed.err
        assert not paths.root().exists(), "it stopped at the first failure"


class TestTheCommand:
    def test_declining_removes_nothing(self, monkeypatch, capsys):
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

        assert cli.main(["purge"]) == 1
        assert paths.root().exists()
        assert "Nothing was removed" in capsys.readouterr().err

    def test_answering_nothing_is_declining(self, monkeypatch):
        # The default on a prompt reading `[y/N]` has to be the safe one.
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        assert cli.main(["purge"]) == 1
        assert paths.root().exists()

    def test_json_lists_without_removing(self, capsys):
        # So a plugin can show what would go before anybody commits to it.
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)

        assert cli.main(["purge", "--json"]) == 0

        reported = json.loads(capsys.readouterr().out)

        assert reported["removed"] is False
        assert [trace["kind"] for trace in reported["traces"]] == ["data"]
        assert paths.root().exists()

    def test_it_says_what_is_left(self, monkeypatch, capsys, tmp_path):
        """
        The bundle itself, which this is running from.

        Windows will not delete a directory holding a running executable, and a
        command that half-deleted itself would be worse than one that says what
        remains. After this there is exactly one thing left, which is the
        promise the README made.
        """
        monkeypatch.setattr(paths, "bundle", lambda: bundle(tmp_path))
        (paths.root() / "runtimes").mkdir(parents=True, exist_ok=True)

        assert cli.main(["purge", "--yes"]) == 0
        assert "Only" in capsys.readouterr().out
