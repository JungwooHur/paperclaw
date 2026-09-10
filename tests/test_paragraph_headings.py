"""Section titles the assembler emitted as paragraphs instead of headings.

Every structural check reads headings: the audit groups sections by them, the
duplicate healer keys on them, the citation alignment splits on them. A page
whose titles are ordinary paragraphs therefore has NO sections at all — the
audit reports it as "nothing translated yet" and skips everything, so a page
holding forty thousand characters and a duplicated half stayed invisible.
"""
import paragraph_headings as ph


def para(text):
    return {"id": "p1", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text",
                                         "text": {"content": text},
                                         "plain_text": text}]}}


class TestRecognisingASectionTitle:

    def test_a_numbered_title_is_one(self):
        assert ph.heading_level(para("3.2.2 Multi-Head Attention (3.2.2 멀티-헤드 어텐션)"))

    def test_a_top_level_section_becomes_a_top_heading(self):
        assert ph.heading_level(para("7 Conclusion (7 결론)")) == 1

    def test_a_subsection_becomes_a_deeper_heading(self):
        assert ph.heading_level(para("5.4 Regularization (5.4 정규화)")) == 3

    def test_prose_is_not_a_title(self):
        assert ph.heading_level(para(
            "3개의 서로 다른 attention 함수를 비교하면 결과는 다음과 같다.")) is None

    def test_a_sentence_that_opens_with_a_number_is_not_a_title(self):
        assert ph.heading_level(para(
            "3.5 배의 속도 향상을 관측했으며 이는 기존 연구와 일치한다.")) is None

    def test_a_long_line_is_not_a_title(self):
        assert ph.heading_level(para("3.1 " + "Encoder and Decoder Stacks " * 8)) is None

    def test_a_block_that_is_already_a_heading_is_left_alone(self):
        block = para("7 Conclusion (7 결론)")
        block["type"] = "heading_1"
        block["heading_1"] = block.pop("paragraph")
        assert ph.heading_level(block) is None

    def test_a_figure_caption_is_not_a_title(self):
        assert ph.heading_level(para("Figure 2: The Transformer architecture")) is None


class TestPromoting:

    def test_the_text_is_carried_over_unchanged(self):
        block = para("7 Conclusion (7 결론)")
        promoted = ph.promote(block)
        assert ph.text_of(promoted) == "7 Conclusion (7 결론)"

    def test_the_level_is_applied(self):
        assert ph.promote(para("5.4 Regularization (5.4 정규화)"))["type"] == "heading_3"

    def test_formatting_inside_survives(self):
        block = para("")
        block["paragraph"]["rich_text"] = [
            {"type": "text", "text": {"content": "7 Conclusion"},
             "plain_text": "7 Conclusion", "annotations": {"bold": True}}]
        assert ph.promote(block)["heading_1"]["rich_text"][0]["annotations"]["bold"]

    def test_a_paragraph_that_is_not_a_title_is_not_promoted(self):
        assert ph.promote(para("본문 문단입니다.")) is None


class TestTellingTheTwoStatesApart:
    """A page with no headings is one of two things, and they look identical
    from here: a page waiting for its translation, and a translated page whose
    titles came out as paragraphs. Reporting the second as the first is what
    made a fully translated page invisible to every structural check."""

    def image(self):
        return {"id": "i1", "type": "image", "image": {}}

    def test_an_empty_page_is_untranslated(self):
        kind, _ = ph.no_heading_finding([para("")])
        assert kind == "NOT_TRANSLATED"

    def test_a_page_of_prose_with_no_titles_is_untranslated(self):
        kind, _ = ph.no_heading_finding([para("본문 문단입니다.")])
        assert kind == "NOT_TRANSLATED"

    def test_titles_as_paragraphs_is_its_own_finding(self):
        kind, ids = ph.no_heading_finding(
            [para("1 Introduction (1 서론)"), para("본문")])
        assert kind == "HEADINGS_AS_PARAGRAPHS" and len(ids) == 1

    def test_figures_without_text_still_report_a_skipped_translation(self):
        blocks = [self.image(), self.image(), self.image()]
        kind, _ = ph.no_heading_finding(blocks)
        assert kind == "SKIPPED_TRANSLATION"

    def test_titles_win_over_the_figure_signal(self):
        # Figures plus real titles is a translated page, not a skipped one.
        blocks = [self.image(), self.image(), self.image(),
                  para("2 Background (2 배경)")]
        kind, _ = ph.no_heading_finding(blocks)
        assert kind == "HEADINGS_AS_PARAGRAPHS"
