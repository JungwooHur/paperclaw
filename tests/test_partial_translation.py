"""A partly translated page is not a processed paper.

A processing request first asks whether the paper is already done. It used to
answer from 500 characters of text, while the audit calls a page with figures,
a couple of sections and under 4,000 characters a translation that never
finished, and tells the reader to re-process it. The two disagreed, so a page
left holding its abstract and introduction was reported "already in Notion" on
every request, and the audit's advice could not be followed.

One rule now answers both. A page that is not done is emptied before it is
processed again, so the new translation does not land under the old fragment
and the figures are placed against the new text instead of piling up on top —
but only when everything on it is something the clear can judge.
"""
import json
import sys
import time
import urllib.error

import collect_papers as cp
import extract_paper_figures as ef
import extract_pdf_media as epm
import link_references as lr
import verify_sections as vs


BOT = {"object": "user", "id": "bot-1"}
ME = BOT["id"]
PERSON = {"object": "user", "id": "person-1"}


def by(block, who):
    return dict(block, created_by=who, last_edited_by=who)


def rich(text):
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def block(bid, kind, text=""):
    return {"id": bid, "type": kind, kind: {"rich_text": rich(text)},
            "created_by": BOT, "last_edited_by": BOT}


def image(bid):
    return {"id": bid, "type": "image",
            "image": {"type": "file", "file": {"url": f"https://f.example/{bid}.png"},
                      "caption": rich(f"Figure {bid}: a figure")},
            "created_by": BOT, "last_edited_by": BOT}


def equation(bid, expression):
    return {"id": bid, "type": "equation", "equation": {"expression": expression},
            "created_by": BOT, "last_edited_by": BOT}


SENTENCE = "이 문장은 번역된 본문 한 문단을 흉내 낸 예시 문장이다. "


def section(n, chars, tag=""):
    body = (SENTENCE * (chars // len(SENTENCE) + 1))[:chars]
    return [block(f"h{tag}{n}", "heading_1", f"{n} Section {n} (섹션 {n})"),
            block(f"p{tag}{n}", "paragraph", body)]


def page(sections=0, chars=0, images=0, tag=""):
    out = []
    for n in range(1, sections + 1):
        out += section(n, chars, tag)
    return out + [image(f"i{tag}{k}") for k in range(images)]


def references(n=20):
    return [block("ref", "heading_1", "References")] + [
        block(f"r{k}", "paragraph", f"[{k}] A. Author. A paper title. In Proc. 2020.")
        for k in range(1, n + 1)]


def ids(blocks):
    return [b["id"] for b in blocks]


STUB = page(sections=2, chars=1300, images=12)            # an abstract and an intro
DONE = page(sections=10, chars=3000, images=12)
IMAGES_ONLY = page(images=30)


class TestWhatCountsAsDone:

    def test_figures_with_no_text_are_not_done(self):
        assert cp.classify_translation(IMAGES_ONLY) == "EMPTY"

    def test_an_abstract_and_an_introduction_are_not_done(self):
        assert cp.classify_translation(STUB) == "PARTIAL"

    def test_a_full_translation_is_done(self):
        assert cp.classify_translation(DONE) == "TRANSLATED"

    def test_a_short_paper_with_few_figures_is_done(self):
        # The audit's own rule needs three figures before it calls a short page
        # unfinished: a workshop paper is short and that is not a fault.
        assert cp.classify_translation(page(sections=3, chars=700, images=2)) == "TRANSLATED"

    def test_a_few_hundred_characters_are_not_a_translation(self):
        assert cp.classify_translation(page(sections=1, chars=300, images=2)) == "EMPTY"
        assert cp.classify_translation(page(sections=1, chars=600, images=2)) == "TRANSLATED"

    def test_the_injected_reference_list_is_not_a_translation(self):
        # It is English apparatus placed from the source, like the figures; counted
        # as text it made a page with no translation read as 1,000s of characters.
        assert cp.classify_translation(IMAGES_ONLY + references()) == "EMPTY"

    def test_a_translation_below_an_early_reference_list_is_still_one(self):
        # A list injected before the text arrived ends up ABOVE it. Measuring only
        # what precedes the list read this page as empty, and a clear would then
        # have archived the whole translation.
        assert cp.classify_translation(references() + DONE) == "TRANSLATED"

    def test_a_translation_below_a_list_with_no_heading_of_its_own_is_still_one(self):
        # The list ends at its last entry. Stretched to the next heading, it swallowed
        # a translation that opened with a paragraph, or whose titles came out as
        # paragraphs, and the page read as empty.
        tail = [block(f"q{k}", "paragraph", SENTENCE * 8) for k in range(6)]
        bullets = [block(f"b{k}", "bulleted_list_item", SENTENCE * 20) for k in range(40)]
        for after in (tail, bullets):
            blocks = [block("ab", "paragraph", SENTENCE * 4)] + references(29) + after
            assert cp.classify_translation(blocks) == "TRANSLATED"
            assert "refused" in cp.plan_clear(blocks, ME)

    def test_every_injected_list_is_taken_out_not_only_the_first(self):
        assert cp.classify_translation(IMAGES_ONLY + references(60) + references(60)) == "EMPTY"

    def test_equation_blocks_count_as_text(self):
        maths = page(sections=4, chars=700, images=3) + [
            equation(f"e{k}", r"\mathbf{x}_{t+1} = f(\mathbf{x}_t, \mathbf{u}_t) + w_t")
            for k in range(40)]
        assert cp.classify_translation(maths) == "TRANSLATED"

    def test_a_page_with_no_headings_and_a_short_body_is_not_done(self):
        blocks = [block("p", "paragraph", SENTENCE * 40)] + [image(f"i{k}") for k in range(5)]
        assert cp.classify_translation(blocks) == "PARTIAL"

    def test_a_long_body_is_never_called_partial(self):
        # Whatever the headings look like, a page carrying this much text is not
        # one to empty on a guess.
        blocks = [block("p", "paragraph", SENTENCE * 600)] + [image(f"i{k}") for k in range(5)]
        assert cp.classify_translation(blocks) == "TRANSLATED"

    def test_titles_written_as_paragraphs_are_not_a_missing_translation(self):
        blocks = [block("t1", "paragraph", "1 Introduction (서론)"),
                  block("p1", "paragraph", SENTENCE * 20),
                  block("t2", "paragraph", "2 Method (방법)"),
                  block("p2", "paragraph", SENTENCE * 20)] + [image(f"i{k}") for k in range(4)]
        assert cp.classify_translation(blocks) == "TRANSLATED"

    def test_archived_and_trashed_blocks_are_not_read(self):
        gone = [dict(b, archived=True) for b in page(sections=10, chars=3000, tag="x")]
        trashed = [dict(b, in_trash=True) for b in page(sections=10, chars=3000, tag="y")]
        assert cp.classify_translation(STUB + gone + trashed) == "PARTIAL"


class TestWhatTheStatusSays:

    def test_a_finished_page_is_processed(self):
        assert cp.status_for(DONE) == ("PROCESSED", "TRANSLATED")

    def test_an_unfinished_page_is_unprocessed(self):
        assert cp.status_for(STUB) == ("UNPROCESSED", "PARTIAL")
        assert cp.status_for(IMAGES_ONLY) == ("UNPROCESSED", "EMPTY")
        assert cp.status_for([]) == ("UNPROCESSED", "EMPTY")

    def test_a_page_that_cannot_be_read_counts_as_processed(self):
        # Unknown must not read as empty: a transient error would otherwise send a
        # finished paper back through translation and clear its page.
        def fail(_):
            raise urllib.error.URLError("timed out")
        assert cp.read_blocks("page", fetch=fail) is None
        assert cp.status_for(None)[0] == "PROCESSED"


def audit(monkeypatch, capsys, blocks):
    monkeypatch.setattr(vs, "fetch_blocks", lambda _: blocks)
    monkeypatch.setattr(vs, "page_title", lambda _: "a paper")
    monkeypatch.setattr(sys, "argv", ["verify_sections.py", "--page", "p", "--json"])
    vs.main()
    return {f["type"] for f in json.loads(capsys.readouterr().out)["findings"]}


# Pages either side of each bound the rule draws: 3 figures, 4 sections, 4,000
# characters. If the audit and the status ever stop reading one rule, one of
# these lands on a different side of it.
EDGES = [STUB, DONE,
         page(sections=4, chars=950, images=3),     # just under every bound
         page(sections=4, chars=1020, images=3),    # just over the character bound
         page(sections=5, chars=600, images=3),     # one section too many
         page(sections=4, chars=950, images=2),     # one figure too few
         page(sections=3, chars=700, images=2)]


class TestTheAuditAgrees:
    """The status and the audit read one rule, so its advice can be followed."""

    def test_the_edges_straddle_the_character_bound(self):
        chars = [vs.body_chars(p) for p in EDGES[2:4]]
        assert chars[0] < vs.PARTIAL_MAX_CHARS <= chars[1]

    def test_a_page_with_sections_is_flagged_exactly_when_the_status_says_partial(
            self, monkeypatch, capsys):
        for blocks in EDGES:
            flagged = "SKIPPED_TRANSLATION" in audit(monkeypatch, capsys, blocks)
            assert flagged == (cp.classify_translation(blocks) == "PARTIAL")

    def test_every_page_with_no_headings_the_status_calls_partial_is_flagged(
            self, monkeypatch, capsys):
        blocks = [block("p", "paragraph", SENTENCE * 40)] + [image(f"i{k}") for k in range(5)]
        assert cp.classify_translation(blocks) == "PARTIAL"
        assert "SKIPPED_TRANSLATION" in audit(monkeypatch, capsys, blocks)


class TestWhatIsCleared:

    def test_a_partial_page_loses_its_fragment_and_its_figures(self):
        assert cp.plan_clear(STUB, ME) == {"ids": ids(STUB)}

    def test_figures_on_a_page_with_no_text_are_cleared_too(self):
        # Left in place, the translation is appended under them and every figure
        # stays stacked above the first heading, where no healer moves it.
        assert cp.plan_clear(IMAGES_ONLY, ME) == {"ids": ids(IMAGES_ONLY)}

    def test_the_reference_list_goes_with_the_page(self):
        # Kept, it would sit above the new translation: the body below the
        # bibliography.
        assert cp.plan_clear(STUB + references(), ME) == {"ids": ids(STUB + references())}

    def test_saved_questions_and_child_pages_stay(self):
        qa = {"id": "qa", "type": "callout", "callout": {"rich_text": []}, "has_children": True}
        sub = {"id": "sub", "type": "child_page", "child_page": {"title": "notes"}}
        assert cp.plan_clear([qa] + STUB + [sub], ME) == {"ids": ids(STUB)}

    def test_a_finished_page_is_never_cleared(self):
        assert "refused" in cp.plan_clear(DONE, ME)
        assert "refused" in cp.plan_clear(page(sections=3, chars=700, images=2), ME)

    def test_a_translation_below_an_early_list_is_never_cleared(self):
        assert "refused" in cp.plan_clear(references() + DONE, ME)

    def test_a_record_kept_after_the_reference_list_stops_the_clear(self):
        # A first-principles descent is written after the list on purpose.
        record = [block("fp", "heading_1", "First principles"),
                  by({"id": "tg", "type": "toggle", "toggle": {"rich_text": rich("layer 1")},
                      "has_children": True}, BOT)]
        assert "refused" in cp.plan_clear(STUB + references() + record, ME)

    def test_a_record_on_a_page_with_no_list_stops_the_clear_too(self):
        record = [block("fp", "heading_1", "First principles"),
                  block("fr", "paragraph", "frontier"),
                  block("st", "code", json.dumps({"version": 1, "nodes": []}))]
        assert "refused" in cp.plan_clear(STUB + record, ME)

    def test_two_reference_lists_stop_the_clear(self):
        assert "refused" in cp.plan_clear(IMAGES_ONLY + references() + references(), ME)

    def test_a_question_nested_under_a_paragraph_stops_the_clear(self):
        host = dict(block("host", "paragraph", "본문"), has_children=True)
        assert "refused" in cp.plan_clear(STUB + [host], ME)

    def test_a_block_it_does_not_recognise_stops_the_clear(self):
        toggle = by({"id": "tg", "type": "toggle", "toggle": {"rich_text": rich("notes")}}, BOT)
        assert "refused" in cp.plan_clear(STUB + [toggle], ME)

    def test_the_owners_own_notes_stay_whatever_their_type(self):
        # A reading-list row the owner annotated: a line on what to look at, a link,
        # a bookmark. None of it is the integration's to remove.
        note = by(block("note", "paragraph", "나중에 다시 읽어 볼 것."), PERSON)
        mark = by({"id": "bm", "type": "bookmark", "bookmark": {"url": "https://x.example"}}, PERSON)
        toggle = by({"id": "tg", "type": "toggle", "toggle": {"rich_text": rich("q.")},
                     "has_children": True}, PERSON)
        assert cp.plan_clear([note, mark, toggle] + IMAGES_ONLY, ME) == {"ids": ids(IMAGES_ONLY)}

    def test_a_block_a_person_has_edited_is_theirs(self):
        edited = dict(block("fixed", "paragraph", SENTENCE * 5), last_edited_by=PERSON)
        assert "fixed" not in cp.plan_clear(STUB + [edited], ME)["ids"]

    def test_nothing_is_cleared_when_the_integration_cannot_be_identified(self):
        assert "refused" in cp.plan_clear(STUB, None)

    def test_an_already_archived_block_is_not_cleared_again(self):
        gone = dict(image("old"), archived=True)
        assert "old" not in cp.plan_clear(STUB + [gone], ME)["ids"]

    def test_an_empty_page_has_nothing_to_clear(self):
        assert cp.plan_clear([], ME) == {"ids": []}


class TestTheListWaitsForTheText:
    """The clear leaves a page empty for as long as translation takes; the
    reference list must not be placed on it in that time."""

    def test_a_page_with_no_body_is_deferred_before_any_source_is_fetched(self, monkeypatch):
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: IMAGES_ONLY)

        def no_network(*_):
            raise AssertionError("fetched the source for a page with no body")
        monkeypatch.setattr(ef, "arxiv_id_from_page", no_network)
        assert lr.link_page("p", apply=True)["deferred"]

    def test_a_page_holding_only_the_opening_is_deferred_too(self, monkeypatch):
        # A thousand characters is not the paper: an upload that pauses after its
        # first sections already has them, and a list placed then ends up above
        # the rest of the text.
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: STUB)
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: None)
        assert lr.link_page("p", apply=True)["deferred"]

    def test_a_fresh_page_with_its_first_few_hundred_characters_is_deferred(self, monkeypatch):
        # No figures are placed yet at that point, so the fragment rule cannot fire:
        # the body bar is what holds the list back.
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: page(sections=2, chars=350))
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: None)
        assert lr.link_page("p", apply=True)["deferred"]

    def test_a_translated_page_is_not_deferred(self, monkeypatch):
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: DONE)
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: None)
        assert "deferred" not in lr.link_page("p", apply=False)

    def test_relinking_an_existing_list_is_not_deferred(self, monkeypatch):
        # Relinking adds no list, so it has nothing to wait for — even on a page
        # that does not read as finished.
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: STUB + references())
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: None)
        assert "deferred" not in lr.link_page("p", apply=False, relink=True)


OLD_PAGE = "2026-01-01T00:00:00.000Z"


class TestAClearedPageGetsItsListBack:
    """An old page loses its reference list to a clear; the healer, which only
    places lists on pages from the last week, must still put it back."""

    def heal(self, monkeypatch, tmp_path, records):
        monkeypatch.setattr(cp, "CLEARED_DIR", str(tmp_path))
        for name in records:
            (tmp_path / name).write_text("{}")
        monkeypatch.setattr(lr, "link_page",
                            lambda pid, apply=False, blocks=None: {"linked": pid})
        # A page translated as long ago as it was made: the translation's own age
        # (tests/test_reference_list_age.py) cannot rescue it either.
        old = [dict(b, created_time=OLD_PAGE) for b in DONE]
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: old)
        monkeypatch.setattr(cp, "_me_id", lambda: ME)
        return lr.heal_references("abc-123", created_time=OLD_PAGE)

    def test_an_old_page_cleared_this_week_is_healed_like_a_new_one(self, monkeypatch, tmp_path):
        rep = self.heal(monkeypatch, tmp_path, [f"abc123-{int(time.time()) - 3600}.json"])
        assert rep == {"linked": "abc-123"}

    def test_an_old_page_never_cleared_is_still_skipped(self, monkeypatch, tmp_path):
        rep = self.heal(monkeypatch, tmp_path, [f"other1-{int(time.time())}.json"])
        assert "skipped" in rep

    def test_a_clear_long_ago_still_counts(self, monkeypatch, tmp_path):
        # The run after the clear may have failed, and the paper be re-sent weeks
        # later onto a page that is now empty and so leaves no new record.
        rep = self.heal(monkeypatch, tmp_path, [f"abc123-{int(time.time()) - 30 * 86400}.json"])
        assert rep == {"linked": "abc-123"}


class TestPdfFiguresWaitForTheText:

    def test_a_page_with_no_body_gets_no_pdf_figures(self, monkeypatch):
        # They would all land above the first heading of a page being processed.
        monkeypatch.setattr(vs, "fetch_blocks", lambda _: [])

        def no_pdf(*_):
            raise AssertionError("fetched the PDF for a page with no body")
        monkeypatch.setattr(epm, "fetch_pdf", no_pdf)
        assert epm.inject("p", "paper.pdf", apply=True)["deferred"]
