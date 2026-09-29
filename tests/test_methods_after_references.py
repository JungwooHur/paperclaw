"""A journal paper's Methods come AFTER its reference list, and are body.

Nature and its sister journals print the article, then the references, then a
Methods section that holds the actual method: the algorithm, the reward design,
the training details. Two rules written for arXiv papers treated everything past
the reference list as back matter. The processing agent therefore left Methods
out of the page, and `strip_backmatter` — which archived from the first
back-matter heading to the end of the page — would have removed a Methods
section that sat below a translated reference list.
"""
import strip_backmatter as sb
import verify_sections as vs


def heading(text, level=1):
    kind = f"heading_{level}"
    return {"type": kind, "id": text,
            kind: {"rich_text": [{"type": "text", "plain_text": text,
                                  "text": {"content": text}}]}}


def para(text):
    return {"type": "paragraph", "id": text,
            "paragraph": {"rich_text": [{"type": "text", "plain_text": text,
                                         "text": {"content": text}}]}}


class TestTheCutStopsAtTheBody:

    def test_methods_below_the_references_survive(self):
        blocks = [heading("Conclusion (결론)"), para("본문"),
                  heading("References (참고문헌)"), para("1. 저자 그리고 저자"),
                  heading("Methods"), heading("GRPO", 2), para("방법 본문")]
        assert sb.backmatter_end(blocks, 2) == 4

    def test_data_and_code_availability_are_kept_with_methods(self):
        blocks = [heading("References"), para("목록"),
                  heading("Data availability (데이터 가용성)"), para("저장소")]
        assert sb.backmatter_end(blocks, 0) == 2

    def test_acknowledgements_after_references_are_still_back_matter(self):
        blocks = [heading("References"), para("목록"),
                  heading("Acknowledgements"), para("감사")]
        assert sb.backmatter_end(blocks, 0) == 4

    def test_a_subheading_inside_the_references_does_not_stop_it(self):
        blocks = [heading("References"), heading("Main text", 2), para("목록")]
        assert sb.backmatter_end(blocks, 0) == 3

    def test_with_nothing_after_it_the_cut_runs_to_the_end(self):
        blocks = [heading("References"), para("목록")]
        assert sb.backmatter_end(blocks, 0) == 2


SOURCE = ("Conclusion\nWe present the model.\n1. Author, A. A work (2020).\n"
          "Methods\nGRPO\nGRPO is the algorithm that we use.\n")


class TestMethodsMissingFromThePage:

    def test_a_source_with_methods_and_a_page_without_is_reported(self):
        assert vs.methods_missing(SOURCE, ["Abstract (초록)", "Conclusion (결론)"])

    def test_a_page_that_has_them_is_not(self):
        assert not vs.methods_missing(SOURCE, ["Conclusion (결론)", "Methods"])

    def test_a_translated_title_counts(self):
        assert not vs.methods_missing(SOURCE, ["Methods (방법)"])

    def test_a_source_without_a_methods_section_is_not(self):
        # "Methods" inside a sentence, even at a line break, is not a heading.
        prose = "as described in the\nMethods section below, we train.\n"
        assert not vs.methods_missing(prose, ["Conclusion (결론)"])

    def test_nothing_to_compare_is_not(self):
        assert not vs.methods_missing("", ["Conclusion (결론)"])
