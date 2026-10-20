"""The Q&A healer outlasts a Notion rate limit without duplicating a callout.

`auto_fix_qa` kept its own HTTP helpers after every other script moved onto
`notion_retry`, so one 429 on a read crashed the healer and ended that cycle's
Q&A heal for every page after it. It did that about 40 times in a week.

Reads retry what `notion_retry` retries. A write retries only a 429, which
Notion refuses before applying anything: a 5xx can arrive after the write
landed, and an append sent twice would be a duplicated callout. A later run can
still meet such an insert, so it checks for the copy before rebuilding.
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
        tries = hp.QUERY_ATTEMPTS
        fake = notion(*([429] * (tries * 5)))
        with pytest.raises(urllib.error.HTTPError):
            hp._post_retry("/databases/db/query", {})
        assert len(fake.requests) == tries

    def test_a_dropped_connection_is_still_retried(self, notion):
        import heal_paper_pages as hp
        fake = notion(urllib.error.URLError("reset"), {"results": []})
        assert hp._post_retry("/databases/db/query", {}) == {"results": []}
        assert len(fake.requests) == 2

    def test_a_twenty_second_run_of_server_errors_is_outlasted(
            self, monkeypatch):
        # One failed query aborts every paper healer for the cycle. Its old
        # wrapper waited 2+4+6+8 s between five sends; the shared ladder alone
        # gives up after 7 s.
        import heal_paper_pages as hp
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        def urlopen(req, timeout=None):
            if clock[0] < 20:
                raise _http_error(502)
            return _Reply({"results": []})

        monkeypatch.setenv("NOTION_TOKEN", "test-token")
        monkeypatch.setattr(aq.time, "sleep", sleep)
        monkeypatch.setattr(aq.urllib.request, "urlopen", urlopen)
        assert hp._post_retry("/databases/db/query", {}) == {"results": []}


def _text(content):
    return {"type": "text", "text": {"content": content}}


def _paragraph(content):
    return {"type": "paragraph", "paragraph": {"rich_text": [_text(content)]}}


def _equation(expression):
    return {"type": "equation", "equation": {"expression": expression}}


def _image(url, caption):
    return {"type": "image", "image": {
        "type": "external", "external": {"url": url},
        "caption": [_text(caption)]}}


def _code(content):
    return {"type": "code", "code": {
        "rich_text": [_text(content)], "language": "python"}}


def _answer_blocks(answer):
    return [_paragraph(answer)] if isinstance(answer, str) else answer


def _check_insert(children):
    """Refuses what Notion refuses before applying any of an insert."""
    for child in children:
        body = child[child["type"]]
        if child["type"] == "equation" and not body.get("expression"):
            raise _http_error(400)
        _check_insert(body.get("children", []))


class _Workspace:
    """An in-memory Notion holding block trees, so `main()` runs end to end.

    `fail(method, path)` decides each request: None answers it, an exception is
    raised before anything changes, and `("after", exc)` applies the request
    and then raises, the way a 5xx or a timeout can follow a write that landed.
    Reading the children of a block in `unreadable` answers 404, and an insert
    holding an equation without its expression is refused whole, as Notion
    refuses it.
    """

    def __init__(self, *pages):
        self.pages = list(pages)
        self.blocks = {p: {"type": "page", "children": []} for p in pages}
        self.requests = []
        self.fail = lambda method, path: None
        self.unreadable = set()
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
        """A broken Q&A: the question in a default-color callout's text.

        `answer` is one paragraph's text or a list of blocks.
        """
        return self.add(page, "callout", {
            "rich_text": [_text(question)], "color": "default",
            "children": _answer_blocks(answer)})

    def good_qa(self, page, question, answer):
        return self.add(page, "callout", {
            "rich_text": [], "color": "gray_background",
            "children": [{"type": "toggle", "toggle": {
                "rich_text": [_text(question)],
                "children": _answer_blocks(answer)}}]})

    def shows(self, page):
        """Every block type and every string a block holds, under `page`."""
        found = set()

        def strings(value):
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, dict):
                for item in value.values():
                    strings(item)
            elif isinstance(value, list):
                for item in value:
                    strings(item)

        def walk(bid):
            for kid in self.blocks[bid]["children"]:
                b = self.blocks[kid]
                found.add(b["type"])
                strings(b[b["type"]])
                walk(kid)

        walk(page)
        return found

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
            if m.group(1) in self.unreadable:
                raise _http_error(404)
            kids = self.blocks[m.group(1)]["children"]
            return {"results": [self._view(k) for k in kids], "has_more": False}
        if m and method == "PATCH":
            _check_insert(body["children"])
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


class TestAnInsertThatLandedIsNotMadeAgain:
    """A 5xx or a timeout can follow an insert that Notion applied."""

    @pytest.mark.parametrize("failure", [
        _http_error(502), TimeoutError("timed out")], ids=["502", "timeout"])
    def test_the_next_run_removes_the_original_instead(
            self, workspace, monkeypatch, capsys, failure):
        ws = workspace("page")
        ws.add("page", "paragraph", _paragraph("intro")["paragraph"])
        ws.legacy_qa("page", "Q: what is x", "x is y")
        sent = []

        def fail(method, path):
            if method == "PATCH" and not sent:
                sent.append(path)
                return ("after", failure)
            return None

        ws.fail = fail
        _run(monkeypatch, capsys, "--page", "page")
        ws.fail = lambda method, path: None
        _run(monkeypatch, capsys, "--page", "page")

        assert ws.callouts("page") == [
            ("gray_background", "Q: what is x", "x is y")]

    def test_a_different_answer_to_the_same_question_is_kept(
            self, workspace, monkeypatch, capsys):
        # Same question, another answer: a second Q&A, not a leftover copy.
        ws = workspace("page")
        ws.good_qa("page", "Q: what is x", "x is y")
        ws.legacy_qa("page", "Q: what is x", "x is z")

        _run(monkeypatch, capsys, "--page", "page")

        assert sorted(ws.callouts("page")) == [
            ("gray_background", "Q: what is x", "x is y"),
            ("gray_background", "Q: what is x", "x is z")]

    @pytest.mark.parametrize("copy, original, kept", [
        ([_paragraph("The loss is"), _equation(r"L = \sum_i x_i")],
         [_paragraph("The loss is"), _equation(r"L = -\log p(y|x)")],
         {r"L = -\log p(y|x)"}),
        ([_paragraph("See below"), _image("https://e.test/5.png", "Figure 5")],
         [_paragraph("See below"), _image("https://e.test/3.png", "Figure 3")],
         {"https://e.test/3.png", "Figure 3"}),
        ([_paragraph("See below"), _paragraph("")],
         [_paragraph("See below"), _image("https://e.test/3.png", "Figure 3")],
         {"https://e.test/3.png", "Figure 3"}),
        ([_paragraph("use x = 1")], [_code("use x = 1")], {"code"}),
    ], ids=["equation", "image", "image-vs-empty-paragraph", "block-type"])
    def test_what_only_the_original_holds_is_not_archived(
            self, workspace, monkeypatch, capsys, copy, original, kept):
        # The same question and the same answer TEXT: an equation, an image or
        # a block type is not text, and a copy that lacks it is not a copy.
        ws = workspace("page")
        ws.good_qa("page", "Q: what is the loss", copy)
        ws.legacy_qa("page", "Q: what is the loss", original)

        _run(monkeypatch, capsys, "--page", "page")

        assert kept <= ws.shows("page")


class TestOtherQAsAreNotRead:
    """Only a Q&A asking the broken one's question can be its copy."""

    def test_an_unreadable_answer_elsewhere_does_not_block_the_heal(
            self, workspace, monkeypatch, capsys):
        ws = workspace("page")
        other = ws.good_qa("page", "Q: another question", "a")
        toggle = ws.blocks[other]["children"][0]
        answer = ws.blocks[toggle]["children"][0]
        ws.add(answer, "paragraph", _paragraph("nested")["paragraph"])
        ws.unreadable.add(answer)
        ws.legacy_qa("page", "Q: what is x", "x is y")

        code, out, err = _run(monkeypatch, capsys, "--page", "page")

        assert code == 0
        assert ws.callouts("page") == [
            ("gray_background", "Q: another question", "a"),
            ("gray_background", "Q: what is x", "x is y")]

    def test_a_copy_that_cannot_be_read_is_no_proof(
            self, workspace, monkeypatch, capsys):
        # Unreadable, the copy cannot show it holds the answer, so the broken
        # Q&A is rebuilt as before: a duplicate can be removed, a lost answer
        # cannot be restored.
        ws = workspace("page")
        copy = ws.good_qa("page", "Q: what is x", "x is y")
        ws.unreadable.add(ws.blocks[copy]["children"][0])
        ws.legacy_qa("page", "Q: what is x", "x is y")

        code, out, err = _run(monkeypatch, capsys, "--page", "page")

        assert code == 0
        assert [color for color, _, _ in ws.callouts("page")] == [
            "gray_background", "gray_background"]

    @pytest.mark.parametrize("broken", ["legacy", "no-question"])
    def test_answers_to_other_questions_cost_no_request(
            self, workspace, monkeypatch, capsys, broken):
        # A page with one broken callout, healed or skipped, used to read the
        # whole answer of every other Q&A on it, on every run.
        ws = workspace("page")
        toggles = []
        for i in range(5):
            callout = ws.good_qa("page", f"Q: question {i}",
                                 [_paragraph(f"part {j}") for j in range(3)])
            toggles.append(ws.blocks[callout]["children"][0])
        if broken == "legacy":
            ws.legacy_qa("page", "Q: what is x", "x is y")
        else:
            ws.add("page", "callout", {
                "rich_text": [], "color": "default",
                "children": [{"type": "toggle", "toggle": {
                    "rich_text": [], "children": [_paragraph("y")]}}]})

        _run(monkeypatch, capsys, "--page", "page")

        read = {path for method, path in ws.requests if method == "GET"}
        assert not [t for t in toggles
                    if f"/blocks/{t}/children?page_size=100" in read]
