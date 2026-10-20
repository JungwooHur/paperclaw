"""The Q&A healer outlasts a Notion rate limit, and never repeats a write twice.

`auto_fix_qa` kept its own HTTP helpers after every other script moved onto
`notion_retry`, so one 429 on a read crashed the healer and ended that cycle's
Q&A heal for every page after it. It did that about 40 times in a week.

Reads retry what `notion_retry` retries. A write retries only a 429, which
Notion refuses before applying anything: a 5xx can arrive after the write
landed, and an append sent twice would be a duplicated callout.
"""
import io
import json
import sys
import urllib.error

import pytest

import auto_fix_qa as aq
import notion_retry


class _Reply:

    def __init__(self, body):
        self._body = body

    def read(self):
        return json.dumps(self._body).encode()


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        "https://api.notion.com/v1/x", code, "err", headers, io.BytesIO(b"{}"))


class _Notion:
    """Answers each request with the next scripted outcome.

    An int is an HTTP error with that code, an exception is raised as it is,
    and anything else is the JSON body of a success.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url))
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, int):
            raise _http_error(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return _Reply(outcome)


@pytest.fixture
def notion(monkeypatch):
    """Installs a scripted Notion and records every wait instead of sleeping."""
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    waits = []
    monkeypatch.setattr(aq.time, "sleep", waits.append)

    def install(*outcomes):
        fake = _Notion(*outcomes)
        fake.waits = waits
        monkeypatch.setattr(aq.urllib.request, "urlopen", fake)
        return fake

    return install


class TestReads:

    def test_a_get_retries_a_rate_limit(self, notion):
        fake = notion(429, {"ok": True})
        assert aq.api_get("/blocks/b/children") == {"ok": True}
        assert len(fake.requests) == 2
        assert len(fake.waits) == 1

    def test_a_get_retries_a_server_error(self, notion):
        fake = notion(502, {"ok": True})
        assert aq.api_get("/blocks/b/children") == {"ok": True}
        assert len(fake.requests) == 2

    def test_the_request_timeout_is_kept(self, notion):
        fake = notion(429, {"ok": True})
        aq.api_get("/pages/p")
        assert fake.timeouts == [aq.HTTP_TIMEOUT, aq.HTTP_TIMEOUT]

    def test_a_database_query_is_a_read(self, notion):
        fake = notion(502, {"results": [], "has_more": False})
        assert aq.api_post("/databases/db/query", {})["results"] == []
        assert [m for m, _ in fake.requests] == ["POST", "POST"]

    def test_a_bad_request_is_not_retried(self, notion):
        fake = notion(400, {"ok": True})
        with pytest.raises(urllib.error.HTTPError):
            aq.api_get("/pages/p")
        assert len(fake.requests) == 1


class TestWrites:

    def test_a_patch_retries_a_rate_limit(self, notion):
        fake = notion(429, {"results": [{"id": "new"}]})
        assert aq.api_patch("/blocks/b", {})["results"][0]["id"] == "new"
        assert len(fake.requests) == 2

    def test_a_patch_does_not_retry_a_server_error(self, notion):
        fake = notion(502, {"ok": True})
        with pytest.raises(urllib.error.HTTPError) as err:
            aq.api_patch("/blocks/b", {})
        assert err.value.code == 502
        assert len(fake.requests) == 1

    def test_an_append_is_not_sent_twice_after_a_server_error(self, notion):
        # The insert of the rebuilt callout. Its 5xx may follow a write that
        # landed, and a second send would leave two copies on the page.
        fake = notion(500, {"results": [{"id": "dup"}]})
        with pytest.raises(urllib.error.HTTPError):
            aq.api_patch("/blocks/page/children", {"children": [{}]})
        assert fake.requests == [
            ("PATCH", aq.API + "/blocks/page/children")]

    def test_a_delete_retries_a_rate_limit(self, notion):
        fake = notion(429, {})
        aq.api_delete("blk")
        assert [m for m, _ in fake.requests] == ["DELETE", "DELETE"]

    def test_a_delete_does_not_retry_a_server_error(self, notion):
        fake = notion(503, {})
        with pytest.raises(urllib.error.HTTPError):
            aq.api_delete("blk")
        assert len(fake.requests) == 1

    def test_a_post_that_creates_does_not_retry_a_server_error(self, notion):
        fake = notion(502, {"id": "page"})
        with pytest.raises(urllib.error.HTTPError):
            aq.api_post("/pages", {})
        assert len(fake.requests) == 1

    def test_a_post_that_creates_retries_a_rate_limit(self, notion):
        fake = notion(429, {"id": "page"})
        assert aq.api_post("/pages", {}) == {"id": "page"}
        assert len(fake.requests) == 2


class TestGivingUp:

    def test_it_stops_after_the_last_attempt(self, notion):
        tries = notion_retry.MAX_RETRIES
        fake = notion(*([429] * tries), {"ok": True})
        with pytest.raises(urllib.error.HTTPError) as err:
            aq.api_get("/pages/p")
        assert err.value.code == 429
        assert len(fake.requests) == tries
        assert len(fake.waits) == tries - 1

    def test_notion_s_retry_after_is_honoured(self, notion):
        fake = notion(_http_error(429, retry_after="7"), {"ok": True})
        aq.api_get("/pages/p")
        assert fake.waits == [7.0]


class TestOnePageCannotEndTheScan:

    def test_the_pages_after_a_failing_one_are_still_healed(
            self, monkeypatch, capsys):
        healed = []

        def heal_page(page_id, dry_run=False):
            healed.append(page_id)
            if page_id == "page-a":
                raise _http_error(429)
            return 1

        monkeypatch.setattr(aq, "heal_page", heal_page)
        monkeypatch.setattr(aq, "_full_sweep_due", lambda: False)
        monkeypatch.setattr(aq, "query_recent_paper_pages",
                            lambda hours: ["page-a", "page-b"])
        monkeypatch.setattr(sys, "argv", ["auto_fix_qa.py"])

        aq.main()

        out = capsys.readouterr()
        assert healed == ["page-a", "page-b"]
        assert "page-a: error HTTPError" in out.err
        assert "page page-b: fixed 1 callout(s)" in out.out

    def test_a_missing_token_still_ends_the_run(self, monkeypatch):
        # Only a page's own failure is contained; a run that cannot reach
        # Notion at all must still stop.
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.setattr(aq, "_full_sweep_due", lambda: False)
        monkeypatch.setattr(aq, "query_recent_paper_pages",
                            lambda hours: ["page-a"])
        monkeypatch.setattr(aq.urllib.request, "urlopen", _Notion({"results": []}))
        monkeypatch.setattr(sys, "argv", ["auto_fix_qa.py"])
        with pytest.raises(SystemExit):
            aq.main()


class TestTheHealerQuery:
    """`heal_paper_pages` queries the DB through this `api_post`."""

    def test_a_rate_limit_is_not_retried_twice_over(self, notion):
        # Its own wrapper used to retry every HTTP error `api_post` gave up
        # on, so one throttled query made five times the attempts.
        import heal_paper_pages as hp
        tries = notion_retry.MAX_RETRIES
        fake = notion(*([429] * (tries * 5)))
        with pytest.raises(urllib.error.HTTPError):
            hp._post_retry("/databases/db/query", {})
        assert len(fake.requests) == tries

    def test_a_dropped_connection_is_still_retried(self, notion):
        import heal_paper_pages as hp
        fake = notion(urllib.error.URLError("reset"), {"results": []})
        assert hp._post_retry("/databases/db/query", {}) == {"results": []}
        assert len(fake.requests) == 2
