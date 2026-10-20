"""Where a table goes on the page.

The table anchor had three faults, all measured on one page of seven tables:

* It looked for `Table N\\b`. A translated body glues a Korean particle straight
  onto the number (`Table 2에`, `Table 6과`), and Hangul is a word character, so
  `\\b` does not hold there and every such mention was invisible.
* Its fallback was the heading whose number equals the TABLE number, so Table 6
  sat under `6 Conclusion` whatever section it belongs to.
* The caption was looked for within 8,000 characters of the table id, so a long
  table had none and was written as a bare `Table 5`.

The figure injector fixed the first fault for itself and kept the second, which is
why both kinds now read one mention rule and one section rule.
"""
import pytest

import extract_paper_figures as ef
import extract_paper_tables as et

ME = "integration-user"
PERSON = "a-person"


def para(bid, text):
    return {"id": bid, "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def heading(bid, text, level=1):
    kind = f"heading_{level}"
    return {"id": bid, "type": kind, kind: {"rich_text": [{"plain_text": text}]}}


def img(bid, caption, made=ME, edited=ME):
    return {"id": bid, "type": "image",
            "created_by": {"id": made}, "last_edited_by": {"id": edited},
            "image": {"type": "file",
                      "file": {"url": f"https://files.example/{bid}.png?sig=1"},
                      "caption": [{"plain_text": caption}] if caption else []}}


def table(fid, caption):
    return {"id": fid, "num": int(fid.rsplit("T", 1)[1]), "caption": caption}


# --- one mention rule -------------------------------------------------------


class TestAMentionWithAParticle:

    @pytest.mark.parametrize("particle", ["에", "의", "과", "는", "로"])
    def test_a_table_is_found_through_the_particle(self, particle):
        blocks = [para("p1", "본문입니다."),
                  para("p2", f"결과는 Table 2{particle} 정리되어 있다.")]
        assert et._table_anchor(2, blocks) == "p2"

    def test_the_korean_word_too(self):
        blocks = [para("p1", "자세한 수치는 표 3에서 볼 수 있다.")]
        assert et._table_anchor(3, blocks) == "p1"

    def test_a_roman_number_with_a_particle(self):
        blocks = [para("p1", "TABLE II에서 보듯이 오차가 줄어든다.")]
        assert et._table_anchor(2, blocks) == "p1"


class TestAMentionOfADifferentTable:

    def test_table_10_is_not_table_1(self):
        blocks = [para("p1", "Table 10에서 비교한다."),
                  para("p2", "Table 1과 같은 설정이다.")]
        assert et._table_anchor(1, blocks) == "p2"

    def test_an_appendix_label_is_not_table_1(self):
        blocks = [para("p1", "Table A.1의 결과를 보면"),
                  para("p2", "Table 1과 같은 설정이다.")]
        assert et._table_anchor(1, blocks) == "p2"

    def test_a_decimal_label_is_not_table_1(self):
        blocks = [para("p1", "Table 1.2의 결과를 보면"),
                  para("p2", "Table 1에 있다.")]
        assert et._table_anchor(1, blocks) == "p2"

    def test_a_word_after_the_label_is_not_a_roman_numeral(self):
        # `i` is the roman 1: "Table in the appendix" must not read as Table I.
        blocks = [para("p1", "the Table in the appendix"),
                  para("p2", "Table 1에 있다.")]
        assert et._table_anchor(1, blocks) == "p2"

    def test_a_word_ending_in_the_label_is_not_a_mention(self):
        # 목표 is "goal" and ends in 표; "suitable" ends in "table".
        blocks = [para("p1", "목표 1에 도달한다."),
                  para("p2", "a suitable 1 option"),
                  para("p3", "표 1에 있다.")]
        assert et._table_anchor(1, blocks) == "p3"

    def test_another_tables_caption_is_a_cross_reference(self):
        blocks = [para("p1", "Table 1: 설정. 비교는 Table 4를 보라."),
                  para("p2", "Table 4에서 우리는 결과를 보인다.")]
        assert et._table_anchor(4, blocks) == "p2"


class TestFiguresReadTheSameRule:

    def test_the_rule_is_shared(self):
        # One definition, two callers: they drifted apart once already.
        assert ef.mention_pattern("table", 2).search("Table 2에")
        assert ef.mention_pattern("figure", 2).search("Fig. 2에")

    def test_a_lowercase_figure_mention_counts_as_a_table_one_does(self):
        blocks = [para("p1", "왼쪽의 fig. 5에서 보듯이")]
        assert ef._anchor_for(5, blocks) == "p1"

    def test_a_word_ending_in_fig_is_not_a_mention(self):
        blocks = [para("p1", "the config 2 setting"), para("p2", "Figure 2에서")]
        assert ef._anchor_for(2, blocks) == "p2"


# --- fallback: the float's own section, not its number --------------------


class TestFallbackToTheTablesOwnSection:

    PAGE = [heading("h1", "1 Introduction (서론)"),
            para("p1", "본문입니다."),
            heading("h3", "3 Method (방법)"),
            para("p3", "본문입니다."),
            heading("h5", "5 Results (결과)"),
            para("p5", "본문입니다."),
            heading("h6", "6 Conclusion (결론)")]

    def test_a_table_in_section_3_goes_under_section_3(self):
        assert et._table_anchor(5, self.PAGE, "S3.T5") == "h3"

    def test_never_under_the_heading_that_shares_its_number(self):
        assert et._table_anchor(6, self.PAGE, "S4.T6") is None

    def test_a_roman_section_heading(self):
        page = [heading("h1", "I. INTRODUCTION"), heading("h2", "II. METHOD")]
        assert et._table_anchor(4, page, "S2.T4") == "h2"

    def test_an_appendix_table_goes_under_its_appendix(self):
        page = self.PAGE + [heading("ha", "Appendix A Additional Results (부록 A)"),
                            heading("hb", "부록 B 세부 설정")]
        assert et._table_anchor(6, page, "A1.T6") == "ha"
        assert et._table_anchor(7, page, "A2.T7") == "hb"

    def test_a_mention_still_wins_over_the_section(self):
        page = self.PAGE + [para("px", "Table 5에서 비교한다.")]
        assert et._table_anchor(5, page, "S3.T5") == "px"

    def test_figures_fall_back_to_their_own_section_too(self):
        assert ef._anchor_for(5, self.PAGE, "S3.F5") == "h3"
        assert ef._anchor_for(6, self.PAGE, "S4.F6") is None


# --- the caption is the table's own -----------------------------------------


class TestTheCaptionIsTheTablesOwn:

    def test_a_long_table_keeps_its_caption(self):
        rows = "".join(f"<tr><td>prompt line {i} " + "x" * 80 + "</td></tr>"
                       for i in range(200))
        html = ('<figure class="ltx_table" id="S4.T5"><table>' + rows
                + '</table><figcaption class="ltx_caption">Table 5: Prompt '
                'examples.</figcaption></figure>')
        assert len(html) > 8000
        assert et.parse_tables(html) == [
            {"id": "S4.T5", "num": 5, "caption": "Table 5: Prompt examples."}]

    def test_a_caption_is_never_borrowed_from_the_next_table(self):
        html = ('<figure class="ltx_table" id="S4.T5"><table><tr><td>1</td></tr>'
                '</table></figure>'
                '<figure class="ltx_table" id="S4.T6"><figcaption>Table 6: Next.'
                '</figcaption><table></table></figure>')
        caps = {t["id"]: t["caption"] for t in et.parse_tables(html)}
        assert caps == {"S4.T5": "", "S4.T6": "Table 6: Next."}

    def test_a_panel_caption_is_not_the_tables(self):
        html = ('<figure class="ltx_table" id="S2.T3">'
                '<figure class="ltx_figure_panel" id="S2.T3.sf1">'
                '<figcaption>(a) Left half.</figcaption><table></table></figure>'
                '<figcaption>Table 3: Both halves.</figcaption></figure>')
        assert et.parse_tables(html)[0]["caption"] == "Table 3: Both halves."
