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


class TestARunInTitle:
    """Journals that run their Methods headings into the paragraph.

    Nature sets a Methods subsection as `Domain knowledge` in bold followed
    immediately by the text. The translation keeps the words and loses the bold,
    so the title becomes the first few words of an ordinary paragraph and the
    whole Methods section reads as one undifferentiated block — fifteen thousand
    characters of it on one page.

    The convention that makes this recognisable is the translation's own: a
    heading is written `English Title (한글 제목)`, and what follows a title is a
    new sentence, while what follows a term gloss is a Korean particle glued to
    the bracket.
    """

    def test_a_run_in_title_is_split(self):
        block = para("Domain knowledge (도메인 지식) 이 시스템은 규칙만 사용합니다.")
        assert ph.split_run_in(block) is not None

    def test_the_title_becomes_the_heading(self):
        head, _ = ph.split_run_in(
            para("Domain knowledge (도메인 지식) 이 시스템은 규칙만 사용합니다."))
        assert ph.text_of(head) == "Domain knowledge (도메인 지식)"

    def test_the_rest_stays_a_paragraph(self):
        _, body = ph.split_run_in(
            para("Domain knowledge (도메인 지식) 이 시스템은 규칙만 사용합니다."))
        assert body["type"] == "paragraph"
        assert ph.text_of(body) == "이 시스템은 규칙만 사용합니다."

    def test_nothing_of_the_text_is_lost(self):
        text = "Optimization (최적화) 각 모듈은 여러 워커로 학습됩니다."
        head, body = ph.split_run_in(para(text))
        assert ph.text_of(head) + " " + ph.text_of(body) == text

    def test_a_term_gloss_is_not_a_title(self):
        # The particle 는 is glued to the bracket: this is prose explaining a
        # term, and splitting it would cut a sentence in half.
        assert ph.split_run_in(
            para("탐색 기법 (약어)는 가치를 추정합니다.")) is None

    def test_a_gloss_in_the_middle_is_not_a_title(self):
        assert ph.split_run_in(
            para("우리는 policy network (정책 네트워크) 를 학습시킵니다.")) is None

    def test_an_english_gloss_is_not_a_title(self):
        # A title's gloss is the Korean translation of it.
        assert ph.split_run_in(
            para("Some method (an acronym) estimates the value.")) is None

    def test_a_long_first_phrase_is_not_a_title(self):
        assert ph.split_run_in(para(
            "We compare three different versions of the program in this work "
            "and describe each (세 가지 버전) 아래에서 자세히 설명합니다.")) is None

    def test_a_paragraph_with_no_bracket_is_left_alone(self):
        assert ph.split_run_in(para("평범한 본문 문단입니다.")) is None

    def test_a_heading_is_not_touched(self):
        block = para("Domain knowledge (도메인 지식) 본문")
        block["type"] = "heading_1"
        block["heading_1"] = block.pop("paragraph")
        assert ph.split_run_in(block) is None

    def test_an_equation_in_the_body_survives_as_an_equation(self):
        # Slicing a span means slicing its characters, and an equation has none
        # to slice — writing text into one is rejected by Notion outright.
        block = para("")
        block["paragraph"]["rich_text"] = [
            {"type": "text", "text": {"content": "Optimization (최적화) 각 모듈 "},
             "plain_text": "Optimization (최적화) 각 모듈 "},
            {"type": "equation", "equation": {"expression": "\\alpha_{\\theta}"},
             "plain_text": "\\alpha_{\\theta}"},
            {"type": "text", "text": {"content": " 를 학습합니다."},
             "plain_text": " 를 학습합니다."},
        ]
        head, body = ph.split_run_in(block)
        kinds = [s["type"] for s in body["paragraph"]["rich_text"]]
        assert "equation" in kinds
        assert all("text" not in s for s in body["paragraph"]["rich_text"]
                   if s["type"] == "equation")

    def test_a_title_that_would_cut_an_equation_is_refused(self):
        block = para("")
        block["paragraph"]["rich_text"] = [
            {"type": "text", "text": {"content": "Optimization (최적"},
             "plain_text": "Optimization (최적"},
            {"type": "equation", "equation": {"expression": "x"}, "plain_text": "x"},
            {"type": "text", "text": {"content": ") 본문입니다."},
             "plain_text": ") 본문입니다."},
        ]
        assert ph.split_run_in(block) is None
