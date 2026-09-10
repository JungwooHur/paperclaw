"""Surviving Notion's rate limit.

Every script here reaches Notion through two functions, and neither retried. A
single 429 — which Notion returns routinely under a burst — therefore killed
whatever was running: a healer cycle lost its whole page, an injection stopped
halfway. Notion says how long to wait in a `Retry-After` header; not reading it
was the difference between a pause and a failure.
"""
import pytest

import notion_retry as aq


class Boom(Exception):
    """Stands in for urllib's HTTPError, which needs a live response to build."""

    def __init__(self, code, retry_after=None):
        self.code = code
        self.headers = {"Retry-After": retry_after} if retry_after else {}


class TestDecidingToRetry:

    def test_a_rate_limit_is_retried(self):
        assert aq.should_retry(Boom(429), attempt=1)

    def test_a_server_error_is_retried(self):
        # 502/503 from Notion are transient in exactly the same way.
        assert aq.should_retry(Boom(503), attempt=1)

    def test_a_bad_request_is_not(self):
        # 400 means the payload is wrong; repeating it just wastes the window.
        assert not aq.should_retry(Boom(400), attempt=1)

    def test_a_missing_page_is_not(self):
        assert not aq.should_retry(Boom(404), attempt=1)

    def test_it_gives_up_after_the_last_attempt(self):
        assert not aq.should_retry(Boom(429), attempt=aq.MAX_RETRIES)


class TestHowLongToWait:

    def test_notion_s_own_number_is_used(self):
        assert aq.retry_delay(Boom(429, retry_after="3"), attempt=1) == 3.0

    def test_a_missing_header_falls_back_to_backing_off(self):
        first = aq.retry_delay(Boom(429), attempt=1)
        second = aq.retry_delay(Boom(429), attempt=2)
        assert 0 < first < second

    def test_an_unparseable_header_falls_back_too(self):
        assert aq.retry_delay(Boom(429, retry_after="soon"), attempt=1) > 0

    def test_the_wait_is_capped(self):
        # A retry that sleeps for minutes is a hang, not a retry.
        assert aq.retry_delay(Boom(429, retry_after="9999"), attempt=1) <= aq.MAX_RETRY_WAIT
