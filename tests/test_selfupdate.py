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

    def helper_said(self, home: Path) -> str:
        """
        Whatever the swap script printed.

        Without this a failure is the word "still" and nothing else — and the
        script runs detached, on another machine, in CI.
        """
        log = home / "logs" / "upgrade.log"

        if not log.exists():
            return f"Nothing was written to {log}."

        return f"{log}:\n{log.read_text(encoding='utf-8', errors='replace')}"

    def wait_for(self, condition, seconds: float = 25) -> bool:
        """
        Wait for something to become true, tolerating it not being there yet.

        Between the two renames neither name exists, and a condition that reads
        a file in that instant raises rather than answers. The helper is quick
        enough to land in that window, which is how this was found.
        """
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            try:
                if condition():
                    return True
            except OSError:
                pass

            time.sleep(0.2)

        return False

    def test_the_directories_are_exchanged(self, tmp_path):
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "replacement", "new")
        keep = tmp_path / "kept"

        self.start_swap(current, replacement, keep)

        assert self.wait_for(lambda: (current / "who").read_text() == "new"), (
            f"still {(current / 'who').read_text()}\n"
            f"{self.helper_said(current.parent / 'home')}"
        )
        assert keep.is_dir() and (keep / "who").read_text() == "old"

    def test_a_failure_puts_the_working_installation_back(self, tmp_path):
        """
        The property that makes this safe to attempt at all.

        A tool that is merely out of date is a great deal better than one that
        is not there.
        """
        current = self.installation(tmp_path / "current", "old")
        keep = tmp_path / "kept"

        # A replacement that is not there, rather than one taken away mid-flight.
        # Racing the helper is how this test used to work and it stopped working
        # the moment the helper got faster — which says nothing about the code
        # and everything about the test.
        self.start_swap(current, tmp_path / "never-arrived", keep, seconds=4)

        assert self.wait_for(lambda: current.is_dir() and (current / "who").read_text() == "old"), (
            f"{self.helper_said(current.parent / 'home')}"
        )
        assert not keep.exists(), "the original was left under the wrong name"

    def test_nothing_is_written_to_disk_to_be_blocked(self, tmp_path, monkeypatch):
        """
        A script on disk is what application control policies exist to stop.

        Reported from Windows: "portable-swap.py was blocked in accordance with
        application control policies". The same lesson the installer already
        carries — a `.ps1` on disk will not run under the default execution
        policy while a string handed to `iex` will — so the code goes in as an
        argument, and there is no file for a rule to match.
        """
        started: list = []

        def remember(argv, **_kwargs):
            started.append(argv)

            return 1

        monkeypatch.setattr(selfupdate.spawn, "start_detached", remember)

        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "replacement", "new")

        selfupdate.swap(current, replacement, tmp_path / "kept")

        argv = started[0]

        assert argv[1] == "-c", "the helper is not passed as code"
        assert not any(str(part).endswith(".py") for part in argv), "a script is named"
        assert not list(tmp_path.rglob("*.py")), "a script was written to disk"



@pytest.mark.skipif(os.name != "nt", reason="the restriction being documented is Windows-only")
class TestWhyTheHelperIsOutside:
    def test_what_windows_actually_does_with_a_directory_in_use(self, tmp_path):
        """
        Recorded rather than assumed, because the assumption was wrong.

        The helper was placed outside both bundles on the belief that Windows
        refuses to rename a directory containing a running executable. This
        test was written to assert that belief and did not: the rename
        succeeded. Since Vista the loader maps images with `FILE_SHARE_DELETE`,
        which permits renaming — deleting is what stays forbidden.

        So the arrangement is not required by this, and the reason it stays is
        narrower: the swap has to outlive the process asking for it, and a
        script the system shell runs is the least that can do that. This test
        now records the behaviour so the next person does not have to guess
        either way.
        """
        import shutil

        home = tmp_path / "inside"
        home.mkdir()
        copied = shutil.copy(sys.executable, home / Path(sys.executable).name)

        running = subprocess.Popen([str(copied), "-c", "import time; time.sleep(30)"])

        try:
            time.sleep(2)

            assert running.poll() is None, "the copied interpreter did not start, so this proves nothing"

            os.rename(home, tmp_path / "moved")

            assert (tmp_path / "moved").is_dir()
        finally:
            running.kill()
            running.wait(timeout=10)


class TestStartingTheSwap:
    """
    Reported from Windows: `CreateProcess` refused with access denied on a
    freshly extracted `python.exe` — one second after that same interpreter had
    run successfully through the launcher.
    """

    def test_the_helper_runs_on_the_interpreter_already_running(self, tmp_path, monkeypatch):
        """
        Not the new bundle's, which was the obvious choice and the wrong one.

        A file written moments ago can be held open by whatever scans it. The
        one already executing cannot be, because it is already executing —
        and renaming the directory it runs from is permitted on Windows, which
        is what makes this possible at all.
        """
        started: list = []
        monkeypatch.setattr(
            selfupdate.spawn, "start_detached", lambda argv, **k: started.append(argv) or 1
        )

        current = tmp_path / "current"
        current.mkdir()
        replacement = tmp_path / "new"
        (replacement / "python").mkdir(parents=True)
        (replacement / "python" / "python.exe").write_text("", encoding="utf-8")

        selfupdate.swap(current, replacement, tmp_path / "kept")

        assert started[0][0] == sys.executable
        assert str(replacement) not in started[0][0]

    def test_a_refused_start_is_retried_rather_than_raised(self, monkeypatch, tmp_path):
        # It is a lock being released, not a permission that is going to
        # change, so waiting is the whole of the fix.
        from portable import spawn

        attempts = []
        # Kept before patching, or the replacement calls itself.
        real = spawn.subprocess.Popen

        def denied_then_fine(argv, **kwargs):
            attempts.append(1)

            if len(attempts) < 3:
                raise PermissionError(5, "Access is denied")

            return real([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL)

        monkeypatch.setattr(spawn.subprocess, "Popen", denied_then_fine)
        monkeypatch.setattr(spawn, "DENIED_SECONDS", 5.0)

        process = spawn._with_retries([sys.executable, "-c", "pass"])
        process.wait(timeout=10)

        assert len(attempts) == 3

    def test_something_that_is_not_a_lock_is_not_retried(self, monkeypatch):
        # A missing file will still be missing in fifteen seconds, and waiting
        # for it only delays the answer.
        from portable import spawn

        attempts = []

        def missing(argv, **kwargs):
            attempts.append(1)

            raise FileNotFoundError(2, "The system cannot find the file specified")

        monkeypatch.setattr(spawn.subprocess, "Popen", missing)

        with pytest.raises(FileNotFoundError):
            spawn._with_retries(["nothing"])

        assert len(attempts) == 1

    def test_a_swap_that_cannot_start_leaves_a_sentence_and_not_a_traceback(
        self, monkeypatch, capsys, tmp_path
    ):
        """
        The daemon is already stopped by then.

        A traceback there leaves somebody with no supervisor, no upgrade and no
        sentence about either.
        """
        from portable import cli, paths

        monkeypatch.setattr(paths, "bundle", lambda: tmp_path / "bundle")
        monkeypatch.setattr(
            selfupdate,
            "latest",
            lambda: selfupdate.Release(version="99.0.0", url="x", digest_url="y"),
        )
        monkeypatch.setattr(selfupdate, "can_upgrade", lambda: tmp_path / "bundle")
        monkeypatch.setattr(selfupdate, "fetch", lambda release, into: tmp_path / "unpacked")
        monkeypatch.setattr(selfupdate, "works", lambda root: "99.0.0")
        monkeypatch.setattr(cli, "_await_gone", lambda timeout=20: None)
        monkeypatch.setattr(
            selfupdate,
            "swap",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError(5, "Access is denied")),
        )

        assert cli.main(["upgrade"]) == 1

        said = capsys.readouterr().err

        assert "still 1." in said or "this is still" in said.lower()
        assert "portable up" in said, "it should say how to carry on"


class TestWhenTheDataLivesInsideTheBundle:
    """
    `home set --beside` puts it there, which is the point of that mode: a flash
    drive holding the whole installation, one folder that travels.

    Replacing the bundle then means replacing the folder that also holds the
    sites and the databases. Reported from Windows, where it failed loudly — the
    helper's own log was inside the directory it was renaming, and Windows will
    not rename a directory holding an open file, so it waited out its deadline
    against a lock it held itself. On POSIX it "succeeded" and carried every
    site into the copy kept as the previous version, which is worse.
    """

    def installation(self, root: Path, marker: str, beside: bool = True) -> Path:
        (root / "python").mkdir(parents=True)
        (root / "portable.cmd").write_text(marker, encoding="utf-8")

        if beside:
            (root / "data" / "sites").mkdir(parents=True)
            (root / "data" / "sites.json").write_text('["demo"]', encoding="utf-8")
            (root / "portable.home").write_text("beside\n", encoding="utf-8")

        return root

    def wait_for(self, condition, seconds: float = 25) -> bool:
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            try:
                if condition():
                    return True
            except OSError:
                pass

            time.sleep(0.2)

        return False

    def start(self, current: Path, replacement: Path, keep: Path) -> None:
        source = str(Path(__file__).resolve().parent.parent / "src")
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from pathlib import Path; "
                    "from portable import selfupdate; "
                    "selfupdate.RENAME_SECONDS = 20; "
                    "selfupdate.swap(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))"
                ),
                source,
                str(current),
                str(replacement),
                str(keep),
            ],
            check=True,
            timeout=60,
            env={**os.environ, "PORTABLE_HOME": str(current / "data")},
        )

    def test_the_data_travels_with_the_new_bundle(self, tmp_path):
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "new", "new", beside=False)
        keep = tmp_path / "kept"

        self.start(current, replacement, keep)

        assert self.wait_for(
            lambda: (current / "portable.cmd").read_text() == "new"
        ), "the bundle was not replaced"
        assert self.wait_for(lambda: (current / "data" / "sites.json").exists()), (
            "the sites went into the copy kept as the previous version"
        )
        assert (current / "portable.home").read_text().strip() == "beside", (
            "the pointer naming the data directory did not come across"
        )

    def test_the_previous_copy_keeps_only_the_bundle(self, tmp_path):
        # Which is also what makes it small enough to leave lying around.
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "new", "new", beside=False)
        keep = tmp_path / "kept"

        self.start(current, replacement, keep)

        # Waiting on the data directory would prove nothing: it is there before
        # the swap as well as after. The bundle changing is what only happens
        # once the exchange has run.
        assert self.wait_for(lambda: (current / "portable.cmd").read_text() == "new")

        assert not (keep / "data").exists()
        assert (keep / "portable.cmd").read_text() == "old"

    def test_the_data_directory_is_not_moved_at_all(self, tmp_path):
        """
        It used to be carried out and back, which is a lot of moving parts
        around every site and database somebody has. Now the folder stays and
        only its bundle files change, so there is nothing to carry.
        """
        current = self.installation(tmp_path / "current", "old")
        replacement = self.installation(tmp_path / "new", "new", beside=False)
        keep = tmp_path / "kept"

        before = (current / "data" / "sites.json").stat().st_ino

        self.start(current, replacement, keep)

        assert self.wait_for(lambda: (current / "portable.cmd").read_text() == "new")
        assert (current / "data" / "sites.json").stat().st_ino == before, (
            "the data was moved, when it did not need to be touched"
        )

    def test_the_helpers_log_is_not_inside_what_it_renames(self, tmp_path, monkeypatch):
        """
        It was, and it held the lock that stopped the rename.

        A file open inside a directory is enough for Windows to refuse to rename
        it, so the helper spent its whole deadline waiting for itself.
        """
        started: dict = {}
        monkeypatch.setattr(
            selfupdate.spawn,
            "start_detached",
            lambda argv, **kwargs: started.update(kwargs) or 1,
        )

        current = self.installation(tmp_path / "current", "old")

        selfupdate.swap(current, tmp_path / "new", tmp_path / "kept")

        log = started["log"]

        assert current not in log.parents, f"the log is inside the bundle: {log}"


class TestUpgradingFromInsideTheFolder:
    """
    The reason this never finished on Windows, reported twice as "it downloads
    and stays on the old version".

    Windows will not rename a directory that is any process's current
    directory. By the time an upgrade runs it is usually two: the shell it was
    typed into — the documentation says `portable.cmd upgrade`, which means
    standing in the folder — and the helper, which inherited that directory
    because nothing said otherwise. So the rename could not succeed, the
    deadline ran out, the working copy went back, and nothing had changed.

    Moving the contents instead means the directory is never renamed, and
    nothing about whose current directory it is matters.

    On POSIX this passes either way — nothing there objects to renaming a
    directory somebody is standing in. Windows is the witness, and CI runs one.
    """

    def bundle(self, root: Path, marker: str) -> Path:
        (root / "python").mkdir(parents=True)
        (root / "portable.cmd").write_text(marker, encoding="utf-8")

        return root

    def test_it_completes_while_a_shell_stands_in_the_bundle(self, tmp_path):
        current = self.bundle(tmp_path / "current", "old")
        replacement = self.bundle(tmp_path / "new", "new")
        keep = tmp_path / "kept"
        source = str(Path(__file__).resolve().parent.parent / "src")

        # cwd=current is the whole point: this process holds the directory the
        # way an ordinary terminal does, and then goes away.
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from pathlib import Path; "
                    "from portable import selfupdate; "
                    "selfupdate.RENAME_SECONDS = 20; "
                    "selfupdate.swap(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))"
                ),
                source,
                str(current),
                str(replacement),
                str(keep),
            ],
            check=True,
            timeout=60,
            cwd=str(current),
            env={**os.environ, "PORTABLE_HOME": str(tmp_path / "home")},
        )

        deadline = time.monotonic() + 25

        while time.monotonic() < deadline:
            try:
                if (current / "portable.cmd").read_text() == "new":
                    break
            except OSError:
                pass

            time.sleep(0.2)

        log = tmp_path / "portable-upgrade.log"
        said = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(no log)"

        assert (current / "portable.cmd").read_text() == "new", said
        assert (keep / "portable.cmd").read_text() == "old"

    def test_the_bundle_directory_itself_is_never_renamed(self, tmp_path):
        # The directory keeps its identity, which is what makes every handle on
        # it — a shell, an editor, a file browser — stop mattering.
        current = self.bundle(tmp_path / "current", "old")
        replacement = self.bundle(tmp_path / "new", "new")
        before = current.stat().st_ino

        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from pathlib import Path; "
                    "from portable import selfupdate; "
                    "selfupdate.RENAME_SECONDS = 20; "
                    "selfupdate.swap(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))"
                ),
                source := str(Path(__file__).resolve().parent.parent / "src"),
                str(current),
                str(replacement),
                str(tmp_path / "kept"),
            ],
            check=True,
            timeout=60,
            env={**os.environ, "PORTABLE_HOME": str(tmp_path / "home")},
        )

        deadline = time.monotonic() + 25

        while time.monotonic() < deadline and (current / "portable.cmd").read_text() != "new":
            time.sleep(0.2)

        assert current.stat().st_ino == before, "the directory was replaced rather than refilled"
        assert source
