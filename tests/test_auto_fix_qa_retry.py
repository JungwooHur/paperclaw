"""The Q&A healer outlasts a Notion rate limit, and never repeats a write twice.

`auto_fix_qa` kept its own HTTP helpers after every other script moved onto
`notion_retry`, so one 429 on a read crashed the healer and ended that cycle's
Q&A heal for every page after it. It did that about 40 times in a week.

Reads retry what `notion_retry` retries. A write retries only a 429, which
Notion refuses before applying anything: a 5xx can arrive after the write
landed, and an append sent twice would be a duplicated callout.
"""
import io
import itertools
import json
import re
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


def _text(content):
    return {"type": "text", "text": {"content": content}}


def _paragraph(content):
    return {"type": "paragraph", "paragraph": {"rich_text": [_text(content)]}}


class _Workspace:
    """An in-memory Notion holding block trees, so `main()` runs end to end.

    `fail(method, path)` decides each request: None answers it, an exception is
    raised before anything changes, and `("after", exc)` applies the request
    and then raises, the way a 5xx or a timeout can follow a write that landed.
    """

    def __init__(self, *pages):
        self.pages = list(pages)
        self.blocks = {p: {"type": "page", "children": []} for p in pages}
        self.requests = []
        self.fail = lambda method, path: None
        self._ids = itertools.count(1)

    def add(self, parent, btype, payload, after=None):
        bid = f"blk-{next(self._ids)}"
        payload = dict(payload)
        kids = payload.pop("children", [])
        self.blocks[bid] = {"type": btype, btype: payload, "children": [],
                            "parent": parent}
        siblings = self.blocks[parent]["children"]
        at = siblings.index(after) + 1 if after in siblings else len(siblings)
        siblings.insert(at, bid)
        for kid in kids:
            self.add(bid, kid["type"], kid[kid["type"]])
        return bid

    def legacy_qa(self, page, question, answer):
        """A broken Q&A: the question in a default-color callout's text."""
        return self.add(page, "callout", {
            "rich_text": [_text(question)], "color": "default",
            "children": [_paragraph(answer)]})

    def good_qa(self, page, question, answer):
        return self.add(page, "callout", {
            "rich_text": [], "color": "gray_background",
            "children": [{"type": "toggle", "toggle": {
                "rich_text": [_text(question)],
                "children": [_paragraph(answer)]}}]})

    def callouts(self, page):
        """(color, question, answer) of each top-level callout, in order."""
        out = []
        for bid in self.blocks[page]["children"]:
            b = self.blocks[bid]
            if b["type"] != "callout":
                continue
            kids = [self.blocks[k] for k in b["children"]]
            if kids and kids[0]["type"] == "toggle":
                holder, answers = kids[0]["toggle"], kids[0]["children"]
            else:
                holder, answers = b["callout"], b["children"]
            question = "".join(r["text"]["content"] for r in holder["rich_text"])
            answer = "".join(r["text"]["content"]
                             for k in answers
                             for r in self.blocks[k]["paragraph"]["rich_text"])
            out.append((b["callout"]["color"], question, answer))
        return out

    def _view(self, bid):
        b = self.blocks[bid]
        body = dict(b[b["type"]])
        if "rich_text" in body:
            body["rich_text"] = [
                {"plain_text": r["text"]["content"], "annotations": {}}
                for r in body["rich_text"]]
        return {"id": bid, "type": b["type"], b["type"]: body,
                "has_children": bool(b["children"])}

    def _apply(self, method, path, body):
        m = re.fullmatch(r"/blocks/([^/?]+)/children(?:\?.*)?", path)
        if m and method == "GET":
            kids = self.blocks[m.group(1)]["children"]
            return {"results": [self._view(k) for k in kids], "has_more": False}
        if m and method == "PATCH":
            after, made = body.get("after"), []
            for child in body["children"]:
                after = self.add(m.group(1), child["type"],
                                 child[child["type"]], after)
                made.append(self._view(after))
            return {"results": made}
        m = re.fullmatch(r"/blocks/([^/?]+)", path)
        if m and method == "DELETE":
            bid = m.group(1)
            self.blocks[self.blocks[bid]["parent"]]["children"].remove(bid)
            return {}
        if method == "POST" and path.endswith("/query"):
            return {"results": [{"id": p} for p in self.pages],
                    "has_more": False}
        raise AssertionError(f"unexpected {method} {path}")

    def __call__(self, req, timeout=None):
        method, path = req.get_method(), req.full_url[len(aq.API):]
        self.requests.append((method, path))
        outcome = self.fail(method, path)
        if isinstance(outcome, BaseException):
            raise outcome
        body = json.loads(req.data) if req.data else None
        result = self._apply(method, path, body)
        if outcome is not None:
            raise outcome[1]
        return _Reply(result)

    def pages_reached(self):
        return {p for _, path in self.requests for p in self.pages
                if path.startswith(f"/blocks/{p}/")}


@pytest.fixture
def workspace(monkeypatch):
    """Runs `main()` against a `_Workspace`; returns (exit code, out, err)."""
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_RESEARCH_DB", "db")
    monkeypatch.setattr(aq.time, "sleep", lambda seconds: None)

    def install(*pages):
        ws = _Workspace(*pages)
        monkeypatch.setattr(aq.urllib.request, "urlopen", ws)
        return ws

    return install


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["auto_fix_qa.py", *argv])
    code = aq.main()
    out = capsys.readouterr()
    return code, out.out, out.err


class TestNotionDownStopsTheRun:
    """Containing each page must not turn an outage into hours of failures."""

    @pytest.mark.parametrize("failure", [
        TimeoutError("timed out"), _http_error(503), _http_error(401)],
        ids=["timeout", "503", "401"])
    def test_a_sweep_stops_once_pages_keep_failing(
            self, workspace, monkeypatch, capsys, failure):
        # The query answers and every block read then fails: the sweep of
        # every page used to run to the end, each page through its own
        # timeouts and back-offs, with the rest of qa-heal waiting behind it.
        ws = workspace(*[f"p{i}" for i in range(40)])
        for p in ws.pages:
            ws.add(p, "paragraph", _paragraph("x")["paragraph"])
        ws.fail = lambda method, path: (
            None if path.endswith("/query") else failure)

        code, out, err = _run(monkeypatch, capsys, "--all")

        assert len(ws.pages_reached()) == aq.MAX_FAILURES_IN_A_ROW
        assert code != 0
        assert "already well-formed" not in out

    def test_a_page_that_answers_resets_the_count(
            self, monkeypatch, capsys):
        # Failures that are not consecutive are single pages, not an outage.
        pages = ["a", "b", "ok-1", "c", "d", "ok-2", "e", "f"]
        healed = []

        def heal_page(page_id, dry_run=False):
            healed.append(page_id)
            if not page_id.startswith("ok"):
                raise _http_error(502)
            return 0

        monkeypatch.setattr(aq, "MAX_FAILURES_IN_A_ROW", 3)
        monkeypatch.setattr(aq, "heal_page", heal_page)
        monkeypatch.setattr(aq, "query_paper_pages", lambda: pages)
        _run(monkeypatch, capsys, "--all")
        assert healed == pages


class TestTheSummaryIsHonest:

    def test_a_failed_page_is_not_reported_well_formed(
            self, monkeypatch, capsys):
        def heal_page(page_id, dry_run=False):
            if page_id == "page-b":
                raise _http_error(401)
            return 0

        monkeypatch.setattr(aq, "heal_page", heal_page)
        monkeypatch.setattr(aq, "query_paper_pages",
                            lambda: ["page-a", "page-b"])
        code, out, err = _run(monkeypatch, capsys, "--all")

        assert "already well-formed" not in out
        assert re.search(r"\b1\b[^\n]*fail", out)
        assert code != 0

    def test_a_clean_run_still_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr(aq, "heal_page", lambda page_id, dry_run=False: 0)
        monkeypatch.setattr(aq, "query_paper_pages", lambda: ["page-a"])
        code, out, err = _run(monkeypatch, capsys, "--all")
        assert "already well-formed" in out
        assert code == 0


class TestABugKeepsItsTraceback:

    def test_a_programming_error_shows_where_it_broke(
            self, workspace, monkeypatch, capsys):
        # A block without a "type": not Notion failing but this code.
        ws = workspace("page-a", "page-b")
        real = aq.fetch_children
        monkeypatch.setattr(
            aq, "fetch_children",
            lambda pid: [{"id": "x"}] if pid == "page-a" else real(pid))

        code, out, err = _run(monkeypatch, capsys, "--all")

        assert "Traceback" in err
        assert "find_qa_callouts" in err
        assert "page-b" in ws.pages_reached()
        assert "already well-formed" not in out
        assert code != 0
