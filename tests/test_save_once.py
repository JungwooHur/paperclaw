"""Saving a Q&A twice leaves one callout, not two.

The writer inserted the callout, then read the page back to check where it
landed — and that read hit a Notion 429. The read had no retry, so the script
died with a traceback and exit 1 AFTER the write had succeeded. The caller saw
a failure, ran it again, and the page got a second identical callout.

Two layers: reads retry the way every other script's reads do, and a question
that is already on the page is not written again, so a caller that retries for
any reason cannot duplicate it.
"""
import io
import json
import urllib.error

import save_qa_callout as sq


class TestAQuestionAlreadyOnThePage:

    def test_the_same_question_is_found(self):
        assert sq.already_saved(["Q: 왜 이렇게 설계했나요?"], "Q: 왜 이렇게 설계했나요?")

    def test_the_marker_and_spacing_do_not_matter(self):
        assert sq.already_saved(["Q: 왜  이렇게 설계했나요?"], "왜 이렇게 설계했나요?")

    def test_a_different_question_is_not(self):
        assert not sq.already_saved(["Q: 왜 이렇게 설계했나요?"], "Q: 학습률은 얼마인가요?")

    def test_an_empty_page_has_nothing(self):
        assert not sq.already_saved([], "Q: 왜 이렇게 설계했나요?")


class _Reply:
    def __init__(self, body):
        self._body = body

    def read(self):
        return json.dumps(self._body).encode()


class TestReadsRetry:

    def test_a_rate_limit_is_retried(self, monkeypatch):
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                             {"Retry-After": "0"}, io.BytesIO(b""))
            return _Reply({"results": [], "has_more": False})

        monkeypatch.setenv("NOTION_TOKEN", "test")
        monkeypatch.setattr(sq.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(sq.time, "sleep", lambda s: None)
        assert sq.api_get("/blocks/x/children") == {"results": [], "has_more": False}
        assert len(calls) == 2

    def test_a_missing_page_is_not(self, monkeypatch):
        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {},
                                         io.BytesIO(b""))

        monkeypatch.setenv("NOTION_TOKEN", "test")
        monkeypatch.setattr(sq.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(sq.time, "sleep", lambda s: None)
        try:
            sq.api_get("/blocks/x/children")
        except urllib.error.HTTPError as err:
            assert err.code == 404
        else:
            raise AssertionError("a 404 must surface, not be retried away")
