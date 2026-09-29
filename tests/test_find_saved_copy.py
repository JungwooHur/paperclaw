"""The backstop's search for an existing copy of an exchange.

Written inline, a timing match printed "skip" and then moved on to the next
candidate PAGE instead of ending the search, so the exchange was filed anyway —
and the wording test that would have recognised the backstop's own earlier copy
was skipped on that page too. Every healer cycle filed the same exchange again
until it left the look-back window: three pages collected over nine hundred
copies in two days.
"""
import auto_save_qa as aq

ASKED = "2026-09-23T11:29:00.000Z"
REPLIED = "2026-09-23T11:32:00.000Z"
QUESTION = "Q: 이 논문에서 말하는 alpha-beta search와 MCTS는 어떻게 다른가요"
ANSWER = "alpha-beta search는 가지치기로 탐색 폭을 줄이고, MCTS는 표본으로 가치를 추정합니다."

AGENT_COPY = {"created": "2026-09-23T11:31:00.000Z",
              "question": "Q: alpha-beta search와 MCTS는 이 논문에서 어떻게 다른가",
              "body": "요약된 답변"}
BACKSTOP_COPY = {"created": "2026-09-24T12:00:00.000Z",
                 "question": QUESTION, "body": ANSWER}


def search(pages, fetch=None):
    fetch = fetch or (lambda pid: pages[pid])
    return aq.find_saved_copy(list(pages), {}, fetch, "own", ASKED, REPLIED,
                              QUESTION, ANSWER)


class TestTheAgentSavedIt:

    def test_it_counts_as_saved(self):
        assert search({"own": [AGENT_COPY]}) == "saved"

    def test_on_a_sibling_page_too(self):
        assert search({"sibling": [AGENT_COPY], "own": []}) == "saved"


class TestTheBackstopSavedItBefore:

    def test_its_own_earlier_copy_is_recognised(self):
        assert search({"own": [BACKSTOP_COPY]}) == "saved"

    def test_even_beside_the_agent_s_copy(self):
        # The exact state of the pages that ran away.
        assert search({"own": [AGENT_COPY, BACKSTOP_COPY]}) == "saved"


class TestNothingThere:

    def test_an_empty_page_is_missing(self):
        assert search({"own": []}) == "missing"

    def test_a_page_that_cannot_be_read_defers(self):
        def fetch(pid):
            raise RuntimeError("429")
        assert search({"own": None}, fetch) == "unknown"

    def test_a_fetched_page_is_cached_for_the_next_pair(self):
        cache = {}
        aq.find_saved_copy(["own"], cache, lambda pid: [], "own", ASKED, REPLIED,
                           QUESTION, ANSWER)
        assert cache == {"own": []}
