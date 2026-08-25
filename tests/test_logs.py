"""
Reading what the supervised processes write.

Read from the files rather than through the daemon. The daemon does not read
them — it hands each child a descriptor and steps out of the way, which is why a
worker that dies mid-sentence still leaves the sentence — so a route through the
API would be this same reading with a socket in between, and would stop working
at the moment the daemon did.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from portable import logs, paths


def write(name: str, text: str) -> Path:
    target = paths.logs() / f"{name}.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    return target


class TestFindingThem:
    def test_a_prefix_gathers_every_worker_of_a_kind(self):
        """
        `php` means all of them.

        A pool is four processes per version, and somebody thinking about "PHP"
        is not thinking about `php-8.4.24-1` through `-4`. Twelve logs across
        three versions is the ordinary case, not an unusual one.
        """
        for version in ("8.4.24", "8.3.30"):
            for index in (1, 2):
                write(f"php-{version}-{index}", "x\n")

        write("caddy", "y\n")

        assert len(logs.resolve("php")) == 4
        assert len(logs.resolve("php-8.4.24")) == 2

    def test_an_exact_name_wins_over_a_prefix(self):
        # Otherwise asking for `caddy` on a machine that also has `caddy-admin`
        # quietly gives both.
        write("caddy", "one\n")
        write("caddy-admin", "two\n")

        assert [source.name for source in logs.resolve("caddy")] == ["caddy"]

    def test_a_process_that_died_is_still_listed(self):
        # Which is the point. What died an hour ago is exactly what somebody
        # wants to read, and it is no longer in `status` to be asked about.
        write("php-8.4.24-1", "it stopped\n")

        assert [source.name for source in logs.available()] == ["php-8.4.24-1"]

    def test_nothing_at_all_is_not_an_error(self):
        assert logs.available() == []
        assert logs.resolve("php") == []


class TestReadingTheEnd:
    def test_it_returns_the_last_lines(self):
        write("caddy", "".join(f"line {index}\n" for index in range(100)))
        found = logs.tail(logs.resolve("caddy")[0], 5)

        assert found == [f"line {index}" for index in range(95, 100)]

    def test_a_file_shorter_than_asked_for_gives_what_there_is(self):
        write("caddy", "only\ntwo\n")

        assert logs.tail(logs.resolve("caddy")[0], 50) == ["only", "two"]

    def test_a_long_file_is_not_read_whole(self):
        """
        Read backwards in blocks.

        These files are small, and "small" is a property of today's usage rather
        than a promise — a log big enough to matter is exactly the one somebody
        is trying to read.
        """
        write("caddy", "".join(f"{index:0>200}\n" for index in range(5000)))
        found = logs.tail(logs.resolve("caddy")[0], 3)

        assert len(found) == 3
        assert found[-1] == f"{4999:0>200}"

    def test_a_line_that_is_not_utf8_does_not_stop_it(self):
        # A worker killed mid-write leaves a partial character behind, and that
        # is not a reason to refuse to show the rest.
        target = paths.logs() / "php.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fine\n\xff\xfe broken\nalso fine\n")

        assert len(logs.tail(logs.resolve("php")[0], 10)) == 3


class TestFollowing:
    def collect(self, sources, stop, into):
        for name, line in logs.follow(sources, stop):
            into.append((name, line))

    def test_new_lines_arrive(self):
        write("caddy", "before\n")
        source = logs.resolve("caddy")[0]

        seen: list[tuple[str, str]] = []
        stop = threading.Event()
        watcher = threading.Thread(target=self.collect, args=([source], stop, seen), daemon=True)
        watcher.start()

        time.sleep(0.3)

        with source.path.open("a", encoding="utf-8") as handle:
            handle.write("after\n")

        time.sleep(0.5)
        stop.set()
        watcher.join(timeout=5)

        assert ("caddy", "after") in seen
        assert ("caddy", "before") not in seen, "it replayed history instead of following"

    def test_a_file_replaced_under_it_starts_again(self):
        """
        A pool being restarted takes its logs away and puts them back.

        Following by offset alone would then read from the middle of a shorter
        file and yield fragments — or nothing at all, forever.
        """
        write("php", "one\ntwo\nthree\n")
        source = logs.resolve("php")[0]

        seen: list[tuple[str, str]] = []
        stop = threading.Event()
        watcher = threading.Thread(target=self.collect, args=([source], stop, seen), daemon=True)
        watcher.start()

        time.sleep(0.3)
        write("php", "fresh\n")
        time.sleep(0.5)
        stop.set()
        watcher.join(timeout=5)

        assert ("php", "fresh") in seen

    def test_a_log_that_does_not_exist_yet_is_waited_for(self):
        # Watching a name before the process starts is the useful order to do
        # things in, and a follower that gave up would stop precisely when
        # something was about to happen.
        missing = logs.Source(name="later", path=paths.logs() / "later.log")

        seen: list[tuple[str, str]] = []
        stop = threading.Event()
        watcher = threading.Thread(target=self.collect, args=([missing], stop, seen), daemon=True)
        watcher.start()

        time.sleep(0.3)
        write("later", "here now\n")
        time.sleep(0.5)
        stop.set()
        watcher.join(timeout=5)

        assert ("later", "here now") in seen


class TestHowItReads:
    def test_caddys_structured_levels_are_recognised(self):
        assert logs.severity('{"level":"error","msg":"address in use"}') == "error"
        assert logs.severity('{"level":"warn","msg":"HTTP/2 skipped"}') == "warn"
        assert logs.severity('{"level":"info","msg":"server running"}') == ""

    def test_so_is_the_prose_everything_else_writes(self):
        # PHP and the databases write sentences, and the wording varies by
        # version. Matched loosely: colouring a line that did not need it costs
        # nothing, and a scheme precise enough never to do that would miss the
        # failures worth seeing.
        assert logs.severity("PHP Fatal error: something") == "error"
        assert logs.severity("Warning: PHP Startup: Unable to load") == "warn"
        assert logs.severity("Installing MariaDB system tables") == ""

    def test_the_label_says_who_is_speaking(self):
        rendered = logs.render("php-8.4.24-1", "hello", width=14, colour=False)

        assert rendered.startswith("php-8.4.24-1   | ")

    def test_colour_can_be_left_out(self):
        assert "\033" not in logs.render("caddy", "PHP Fatal error", colour=False)
        assert "\033" in logs.render("caddy", "PHP Fatal error", colour=True)
