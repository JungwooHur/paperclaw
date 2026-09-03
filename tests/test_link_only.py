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
