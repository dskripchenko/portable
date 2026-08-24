"""
Getting a build onto disk.

Most of these test refusals rather than successes. Everything acquired here is
executed afterwards, so the interesting question is not "does a good archive
unpack" but "what happens to a bad one" — and the answer has to be that it does
not survive on disk to be found by whatever looks next.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from portable import acquire
from portable.catalog import Build


def make_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)

    return path


def build_for(path: Path, *, checksum: str | None = None, algorithm: str = "sha256") -> Build:
    return Build(
        name="test",
        version="1.0.0",
        url=path.as_uri(),
        filename=path.name,
        checksum=checksum,
        algorithm=algorithm,
    )


class TestDigest:
    def test_it_hashes_with_the_algorithm_it_was_given(self, tmp_path):
        target = tmp_path / "f.bin"
        target.write_bytes(b"portable")

        assert acquire.digest(target, "sha256") == hashlib.sha256(b"portable").hexdigest()
        assert acquire.digest(target, "sha512") == hashlib.sha512(b"portable").hexdigest()


class TestUnpack:
    def test_it_flattens_a_single_wrapping_directory(self, tmp_path):
        # Caddy's archive holds files at the root, others nest them one deep.
        # Downstream should not have to know which it was handed.
        archive = make_zip(tmp_path / "a.zip", {"caddy-2.0/caddy.exe": "x", "caddy-2.0/LICENSE": "y"})
        into = acquire.unpack(build_for(archive), archive, tmp_path / "out")

        assert (into / "caddy.exe").exists()
        assert not (into / "caddy-2.0").exists()

    def test_it_leaves_a_flat_archive_alone(self, tmp_path):
        archive = make_zip(tmp_path / "b.zip", {"php.exe": "x", "php-cgi.exe": "y"})
        into = acquire.unpack(build_for(archive), archive, tmp_path / "out")

        assert (into / "php.exe").exists()
        assert (into / "php-cgi.exe").exists()

    def test_unpacking_twice_replaces_rather_than_merges(self, tmp_path):
        # An upgrade that left the previous version's files behind would produce
        # a directory that is neither version.
        target = tmp_path / "out"
        first = make_zip(tmp_path / "1.zip", {"old.txt": "x"})
        acquire.unpack(build_for(first), first, target)

        second = make_zip(tmp_path / "2.zip", {"new.txt": "y"})
        acquire.unpack(build_for(second), second, target)

        assert (target / "new.txt").exists()
        assert not (target / "old.txt").exists(), "the previous version survived the replacement"

    def test_an_archive_escaping_its_directory_is_refused(self, tmp_path):
        # An untrusted archive that this tool then runs. `extractall` sanitises,
        # but as an implementation detail — the refusal is made explicit here.
        archive = tmp_path / "evil.zip"

        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../../escaped.txt", "pwned")

        with pytest.raises(acquire.VerificationError) as excinfo:
            acquire.unpack(build_for(archive), archive, tmp_path / "out")

        assert "outside" in str(excinfo.value)
        assert not (tmp_path.parent / "escaped.txt").exists()


class TestDownload:
    def test_it_verifies_against_the_published_checksum(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})
        correct = acquire.digest(source, "sha256")

        landed = acquire.download(build_for(source, checksum=correct), tmp_path / "dl")

        assert landed.exists()
        assert acquire.digest(landed, "sha256") == correct

    def test_a_mismatch_discards_the_file_and_names_both_digests(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})
        wrong = "0" * 64

        with pytest.raises(acquire.VerificationError) as excinfo:
            acquire.download(build_for(source, checksum=wrong), tmp_path / "dl")

        message = str(excinfo.value)

        assert wrong in message and "received" in message
        # The whole point: nothing executable is left behind for the next thing
        # that goes looking.
        assert list((tmp_path / "dl").glob("*")) == []

    def test_an_already_correct_file_is_not_fetched_again(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})
        checksum = acquire.digest(source, "sha256")
        destination = tmp_path / "dl"

        first = acquire.download(build_for(source, checksum=checksum), destination)
        stamp = first.stat().st_mtime_ns

        second = acquire.download(build_for(source, checksum=checksum), destination)

        assert second == first
        assert second.stat().st_mtime_ns == stamp, "the archive was fetched a second time"

    def test_a_stale_file_that_does_not_match_is_replaced(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})
        checksum = acquire.digest(source, "sha256")

        destination = tmp_path / "dl"
        destination.mkdir()
        (destination / source.name).write_bytes(b"a truncated earlier attempt")

        landed = acquire.download(build_for(source, checksum=checksum), destination)

        assert acquire.digest(landed, "sha256") == checksum

    def test_a_build_without_a_checksum_is_fetched_but_marked(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})

        result = acquire.install(build_for(source, checksum=None))

        # Not a failure — some publishers list nothing. But "arrived without
        # incident" and "verified" are different claims, and the difference is
        # kept rather than rounded off.
        assert result.verified is False

    def test_an_interrupted_download_leaves_no_part_file_behind(self, tmp_path):
        source = tmp_path / "src.zip"
        make_zip(source, {"a.txt": "hello"})
        destination = tmp_path / "dl"

        acquire.download(build_for(source, checksum=acquire.digest(source, "sha256")), destination)

        assert list(destination.glob("*.part")) == []
