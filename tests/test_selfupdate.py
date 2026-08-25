"""
Replacing this tool with a newer one, from inside it.

The download is the easy half. The hard half is that a running program's files
cannot be replaced on Windows, and this command is executed by the very
interpreter inside the bundle being replaced.

The property every test here defends is the same one: an upgrade that fails must
leave a working tool. Half-swapped is the state to avoid at any cost.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from portable import selfupdate


class TestFindingARelease:
    def release(self, **overrides) -> dict:
        payload = {
            "tag_name": "v0.2.0",
            "assets": [
                {
                    "name": "portable-0.2.0-x86_64-pc-windows-msvc.zip",
                    "browser_download_url": "https://example.invalid/bundle.zip",
                },
                {
                    "name": "portable-0.2.0-x86_64-pc-windows-msvc.zip.sha256",
                    "browser_download_url": "https://example.invalid/bundle.zip.sha256",
                },
            ],
        }
        payload.update(overrides)

        return payload

    def test_it_reads_the_version_and_the_bundle(self, monkeypatch):
        monkeypatch.setattr(
            selfupdate.net, "read_text", lambda *a, **k: json.dumps(self.release())
        )
        found = selfupdate.latest()

        assert found.version == "0.2.0"
        assert found.url.endswith("bundle.zip")
        assert found.digest_url.endswith(".sha256")

    def test_a_release_without_a_bundle_says_what_it_has(self, monkeypatch):
        # A tag cut without the workflow finishing, or with the asset upload
        # having failed. "Nothing happened" would be a poor account of it.
        monkeypatch.setattr(
            selfupdate.net,
            "read_text",
            lambda *a, **k: json.dumps(self.release(assets=[{"name": "notes.txt"}])),
        )

        with pytest.raises(selfupdate.UpgradeFailed) as excinfo:
            selfupdate.latest()

        assert "notes.txt" in str(excinfo.value)

    def test_versions_compare_as_numbers(self):
        assert selfupdate.newer("0.10.0", "0.9.0")
        assert not selfupdate.newer("0.1.1", "0.1.1")
        assert not selfupdate.newer("0.1.0", "0.1.1")


class TestRefusingToProceed:
    def test_a_release_with_no_digest_is_refused(self, monkeypatch):
        """
        This replaces the program itself.

        Elsewhere "the publisher listed no checksum" is recorded and accepted —
        PostgreSQL and Redis arrive that way. Here it is not: everything else
        this tool downloads is run by it, and this *is* it.
        """
        release = selfupdate.Release(version="9.9.9", url="https://x.invalid/b.zip", digest_url="")

        with pytest.raises(selfupdate.UpgradeFailed) as excinfo:
            selfupdate._published_digest(release)

        assert "by hand" in str(excinfo.value)

    def test_a_digest_that_does_not_match_discards_the_download(self, monkeypatch, tmp_path):
        release = selfupdate.Release(
            version="9.9.9",
            url="https://x.invalid/b.zip",
            digest_url="https://x.invalid/b.zip.sha256",
        )

        monkeypatch.setattr(
            selfupdate.net, "read_text", lambda *a, **k: f"{'a' * 64}  b.zip\n"
        )

        class Response:
            def read(self):
                return b"not what was promised"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(selfupdate.net, "open_url", lambda *a, **k: Response())

        with pytest.raises(selfupdate.UpgradeFailed) as excinfo:
            selfupdate.fetch(release, tmp_path)

        assert "nothing was changed" in str(excinfo.value).lower()
        assert not (tmp_path / "b.zip").exists(), "the bad download was kept"

    def test_a_source_checkout_has_nothing_to_replace(self, monkeypatch):
        monkeypatch.setattr(selfupdate.paths, "bundle", lambda: None)

        with pytest.raises(selfupdate.UpgradeFailed) as excinfo:
            selfupdate.can_upgrade()

        assert "git" in str(excinfo.value)

    def test_the_workspace_is_beside_the_bundle_and_not_inside_it(self, tmp_path):
        # Renaming a directory into its own subdirectory is not a thing that
        # works, and unpacking the replacement inside the thing being replaced
        # would ask for exactly that.
        bundle = tmp_path / "portable-0.1.1"
        workspace = selfupdate.workspace(bundle)

        assert workspace.parent == bundle.parent
        assert bundle not in workspace.parents


class TestTheSwap:
    """
    Two renames, in a script run by the system shell from outside both bundles.

    It has to be outside both. Windows refuses to rename a directory containing
    a running executable, so a helper started from the new bundle cannot move
    the new bundle, and one started from the old cannot move the old.
    """

    def installation(self, root: Path, marker: str) -> Path:
        root.mkdir(parents=True)
        (root / "who").write_text(marker, encoding="utf-8")

        return root

    def start_swap(self, current: Path, replacement: Path, keep: Path, seconds: int = 60) -> None:
        """
        Ask for the swap from a process that then exits.

        The helper waits for whoever asked to go away before it renames
        anything — so calling it from the test process, which stays alive for
        the rest of the suite, waits out the whole deadline and proves nothing.
        """
        source = str(Path(__file__).resolve().parent.parent / "src")
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from pathlib import Path; "
                    "from portable import selfupdate; "
                    "selfupdate.RENAME_SECONDS = int(sys.argv[5]); "
                    "selfupdate.swap(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))"
                ),
                source,
                str(current),
                str(replacement),
                str(keep),
                str(seconds),
            ],
            check=True,
            timeout=60,
            env={**os.environ, "PORTABLE_HOME": str(current.parent / "home")},
        )

    def wait_for(self, condition, seconds: float = 25) -> bool:
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            if condition():
                return True

            time.sleep(0.2)

        return False

    def test_the_directories_are_exchanged(self, tmp_path):
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "replacement", "new")
        keep = tmp_path / "kept"

        self.start_swap(current, replacement, keep)

        assert self.wait_for(lambda: (current / "who").read_text() == "new"), (
            f"still {(current / 'who').read_text()}"
        )
        assert keep.is_dir() and (keep / "who").read_text() == "old"

    def test_a_failure_puts_the_working_installation_back(self, tmp_path):
        """
        The property that makes this safe to attempt at all.

        A tool that is merely out of date is a great deal better than one that
        is not there.
        """
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "replacement", "new")
        keep = tmp_path / "kept"

        self.start_swap(current, replacement, keep, seconds=8)

        # Taken away in the window before the helper acts, so its second rename
        # cannot succeed.
        time.sleep(1.2)

        import shutil

        shutil.rmtree(replacement, ignore_errors=True)

        assert self.wait_for(lambda: current.is_dir() and (current / "who").read_text() == "old")
        assert not keep.exists(), "the original was left under the wrong name"

    def test_the_helper_lives_outside_both_bundles(self, tmp_path, monkeypatch):
        written: list[Path] = []
        def remember(argv, **_kwargs):
            written.append(Path(argv[-1]))

            return 1

        monkeypatch.setattr(selfupdate.spawn, "start_detached", remember)

        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "replacement", "new")

        selfupdate.swap(current, replacement, tmp_path / "kept")

        helper = written[0]

        assert helper.parent == tmp_path
        assert current not in helper.parents
        assert replacement not in helper.parents


@pytest.mark.skipif(os.name != "nt", reason="the restriction being documented is Windows-only")
class TestWhyTheHelperIsOutside:
    def test_windows_will_not_rename_a_directory_a_process_is_running_from(self, tmp_path):
        """
        The reason for the whole arrangement, asserted rather than assumed.

        POSIX permits this and would hide the problem entirely — it did, until
        the consequence was thought through rather than observed. If this ever
        starts passing, the helper could live inside the new bundle and all of
        this could be simpler.
        """
        import shutil

        home = tmp_path / "inside"
        home.mkdir()
        copied = shutil.copy(sys.executable, home / Path(sys.executable).name)

        running = subprocess.Popen([str(copied), "-c", "import time; time.sleep(20)"])

        try:
            with pytest.raises(OSError):
                os.rename(home, tmp_path / "moved")
        finally:
            running.kill()
            running.wait(timeout=10)
