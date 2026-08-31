"""Where the injected reference list starts, and what counts as body.

Six modules ask this one question before they touch a page: the structural
audit, the maths healer, back-matter stripping and the three float injectors.
Getting it wrong does not fail loudly — it silently sweeps an appendix into
"everything after the references", or drops a figure below the bibliography.
"""
import reference_section as rs


def para(text):
    return {"type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def heading(text):
    return {"type": "heading_1",
            "heading_1": {"rich_text": [{"plain_text": text}]}}


def entries(count, start=1):
    return [para(f"[{n}] Author, A. A paper title. 2024.")
            for n in range(start, start + count)]


class TestLooksLikeList:
    def test_recognises_a_run_of_english_numbered_entries(self):
        assert rs.looks_like_list(entries(3)) is True

    def test_rejects_a_translated_bibliography(self):
        korean = [para(f"[{n}] 저자, 논문 제목, 2024년.") for n in (1, 2, 3)]
        assert rs.looks_like_list(korean) is False

    def test_rejects_a_run_too_short_to_be_a_list(self):
        assert rs.looks_like_list(entries(2)) is False

    def test_tolerates_one_stray_paragraph_among_entries(self):
        run = entries(9) + [para("Note added by hand.")]
        assert rs.looks_like_list(run) is True


class TestBodyBlocks:
    def test_excludes_the_reference_list_and_its_heading(self):
        body = [heading("1 Introduction"), para("본문 한 문단.")]
        blocks = body + [heading("References")] + entries(4)
        assert rs.body_blocks(blocks) == body

    def test_returns_everything_when_no_list_was_injected(self):
        blocks = [heading("1 Introduction"), para("본문 한 문단.")]
        assert rs.body_blocks(blocks) == blocks

    def test_a_heading_before_the_bibliography_is_not_its_start(self):
        """An appendix that merely precedes the references is still body.

        Collecting every paragraph to the end of the page made any earlier
        heading match, so acting on it swept that appendix away.
        """
        appendix = [heading("Appendix G"), para("부록 본문 한 문단.")]
        blocks = [heading("1 Introduction"), para("본문.")] + appendix
        blocks += [heading("References")] + entries(5)
        assert rs.body_blocks(blocks)[-2:] == appendix


class TestBodyEndAnchor:
    def test_points_at_the_last_block_before_the_reference_list(self):
        last = para("본문의 마지막 문단.")
        last["id"] = "last-body-block"
        blocks = [heading("1 Introduction"), last, heading("References")]
        blocks += entries(3)
        assert rs.body_end_anchor(blocks) == "last-body-block"

    def test_is_none_when_the_page_has_no_reference_list(self):
        blocks = [heading("1 Introduction"), para("본문.")]
        assert rs.body_end_anchor(blocks) is None
