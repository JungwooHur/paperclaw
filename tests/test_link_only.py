"""The one thing the citation pass must never do: change what the paper says.

It rebuilds every span in a block to attach links, so a bug in that rebuild
silently rewrites the body. Two of the three linking paths already refused to
write when the text came out different; the numeric path — the one that runs on
most papers — did not.
"""
import pytest

import link_references as lr


def spans(*texts):
    return [{"type": "text", "text": {"content": t}, "plain_text": t}
            for t in texts]


class TestComparingWhatWasWritten:

    def test_the_same_text_split_differently_is_the_same_text(self):
        # Splitting a span to link part of it is the whole point; only the
        # characters matter.
        assert lr.same_text(spans("연구 [1] 참고"), spans("연구 ", "[", "1", "]", " 참고"))

    def test_a_dropped_character_is_caught(self):
        assert not lr.same_text(spans("연구 [1] 참고"), spans("연구 [1] 참"))

    def test_an_added_character_is_caught(self):
        assert not lr.same_text(spans("연구 [1]"), spans("연구 [1]."))

    def test_an_equation_span_counts_as_its_expression(self):
        eq = [{"type": "equation", "equation": {"expression": "L(N)"},
               "plain_text": "L(N)"}]
        assert lr.same_text(eq, spans("L(N)"))

    def test_empty_matches_empty(self):
        assert lr.same_text([], [])


class TestRefusingToWrite:

    def test_it_raises_when_the_rebuild_lost_text(self):
        with pytest.raises(ValueError, match="text changed"):
            lr.check_link_only(spans("연구 [1] 참고"), spans("연구 [1]"), "blk-1")

    def test_it_names_the_block(self):
        with pytest.raises(ValueError, match="blk-9"):
            lr.check_link_only(spans("가"), spans("나"), "blk-9")

    def test_it_passes_an_honest_rebuild(self):
        lr.check_link_only(spans("연구 [1]"), spans("연구 ", "[", "1", "]"), "blk-1")


class TestSayingWhyNothingWasLinked:
    """`slots_linked: 0` reads as "the linker failed". On one paper it meant the
    opposite — the translation had dropped every citation marker, so there was
    nothing to link. The two need to look different in the report."""

    def test_a_body_with_no_citations_is_reported(self):
        assert lr.citation_shortfall({}, 73) == "body has no citations (73 in source)"

    def test_a_body_that_has_them_is_not(self):
        assert lr.citation_shortfall({"I": [("b1", 1)]}, 73) is None

    def test_a_source_with_none_either_is_not_a_shortfall(self):
        # A paper that genuinely cites nothing is not a defect to report.
        assert lr.citation_shortfall({}, 0) is None


class TestRelinkingAfterTheListIsRebuilt:
    """Rebuilding the reference list gives every entry a new block, so the links
    already in the body point at blocks that were just archived. The numeric
    path skipped any span that already carried a link, so a relink left the
    whole body pointing at nothing — which looks exactly like working links
    until one is clicked."""

    def marker(self, text, url):
        return {"type": "text", "text": {"content": text, "link": {"url": url}},
                "plain_text": text}

    def test_a_citation_piece_loses_its_stale_link(self):
        spans = [self.marker("[", "u"), self.marker("7", "u"),
                 self.marker("]", "u")]
        cleared = lr.clear_citation_links(spans)
        assert all(not (s.get("text") or {}).get("link") for s in cleared)

    def test_a_separator_inside_a_group_is_cleared_too(self):
        cleared = lr.clear_citation_links([self.marker(", ", "u")])
        assert not (cleared[0].get("text") or {}).get("link")

    def test_a_link_on_prose_is_left_alone(self):
        # Someone may have linked a phrase by hand; that is theirs.
        spans = [self.marker("자세한 설명은 여기", "https://x")]
        assert (lr.clear_citation_links(spans)[0]["text"]["link"]["url"]
                == "https://x")

    def test_an_unlinked_span_is_untouched(self):
        spans = [{"type": "text", "text": {"content": "7"}, "plain_text": "7"}]
        assert lr.clear_citation_links(spans) == spans

    def test_the_text_is_unchanged(self):
        spans = [self.marker("[", "u"), self.marker("7", "u"),
                 self.marker("]", "u")]
        assert lr.same_text(spans, lr.clear_citation_links(spans))


class TestWhatTheNumericPathIsAllowedToChange:
    """That path does not only link — it REWRITES the citation number to the
    source's, which is the whole point: a translation that renumbered its
    citations is repaired to match the reference list. So the invariant is not
    "the text is identical" but "only the numbers inside brackets moved"."""

    def test_renumbering_a_citation_is_allowed(self):
        assert lr.same_prose(spans("연구 [1] 참고"), spans("연구 [69] 참고"))

    def test_a_group_may_be_renumbered(self):
        assert lr.same_prose(spans("[1, 2] 참고"), spans("[69, 80] 참고"))

    def test_losing_a_word_is_still_caught(self):
        assert not lr.same_prose(spans("연구 [1] 참고"), spans("연구 [69]"))

    def test_losing_a_bracket_is_caught(self):
        assert not lr.same_prose(spans("연구 [1] 참고"), spans("연구 1 참고"))

    def test_dropping_a_whole_citation_is_caught(self):
        assert not lr.same_prose(spans("연구 [1] 과 [2]"), spans("연구 [69] 과 "))

    def test_it_refuses_to_write_when_prose_changed(self):
        with pytest.raises(ValueError, match="prose changed"):
            lr.check_prose_only(spans("연구 [1] 참고"), spans("연구 [69]"), "blk-3")

    def test_it_passes_an_honest_renumbering(self):
        lr.check_prose_only(spans("연구 [1] 참고"), spans("연구 [69] 참고"), "blk-3")

    def test_the_rebuild_itself_drops_a_stale_link(self):
        # The helper being right is not enough: `_rewrite_block` has to call it.
        # Without that the numeric path skips any span that already carries a
        # link, and a relink leaves the whole body pointing at archived blocks.
        block = {"id": "b1", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "연구 "}, "plain_text": "연구 "},
            {"type": "text", "text": {"content": "[", "link": {"url": "old"}},
             "plain_text": "["},
            {"type": "text", "text": {"content": "7", "link": {"url": "old"}},
             "plain_text": "7"},
            {"type": "text", "text": {"content": "]", "link": {"url": "old"}},
             "plain_text": "]"},
        ]}}
        out = lr._rewrite_block(block, [7], "page-id", {7: "new-block"}, {})
        urls = [(s.get("text") or {}).get("link", {}).get("url")
                for s in out if (s.get("text") or {}).get("link")]
        # Notion strips the dashes from a block id in an anchor URL.
        assert urls and all(u.endswith("#newblock") for u in urls)
