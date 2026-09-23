"""Only one copy of the backstop may scan at a time.

Two invocations exist — the five-minute timer and the trigger that fires when a
reply goes out — and the service is saturated often enough that runs overlap.
Two copies each read the page, each see no callout for a pair, and each file
one. The lock lived only on the trigger, so the timer could always walk into it.
"""
import os

import run_lock


class TestHoldingIt:

    def test_the_first_caller_gets_it(self, tmp_path):
        assert run_lock.acquire(str(tmp_path / "a.lock")) is not None

    def test_a_second_caller_in_the_same_process_is_refused(self, tmp_path):
        path = str(tmp_path / "b.lock")
        first = run_lock.acquire(path)
        assert first is not None
        assert run_lock.acquire(path) is None

    def test_releasing_lets_the_next_one_in(self, tmp_path):
        path = str(tmp_path / "c.lock")
        held = run_lock.acquire(path)
        run_lock.release(held)
        assert run_lock.acquire(path) is not None

    def test_two_different_locks_do_not_collide(self, tmp_path):
        assert run_lock.acquire(str(tmp_path / "d.lock")) is not None
        assert run_lock.acquire(str(tmp_path / "e.lock")) is not None

    def test_the_file_is_created_where_asked(self, tmp_path):
        path = str(tmp_path / "f.lock")
        run_lock.acquire(path)
        assert os.path.exists(path)

    def test_an_unwritable_path_does_not_stop_the_caller(self, tmp_path):
        # A lock that cannot be taken must not become a reason to skip the work;
        # the wording and timing checks still stand behind it.
        assert run_lock.acquire("/proc/definitely/not/writable.lock") is not None
