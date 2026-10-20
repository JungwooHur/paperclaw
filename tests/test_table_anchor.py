"""Where a table goes on the page, and moving the ones already in the wrong place.

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


def table(fid, caption, legacy=None):
    """A parsed source table; `legacy` is what the pre-fix parser read as its
    caption, the same as `caption` unless given."""
    return {"id": fid, "num": int(fid.rsplit("T", 1)[1]), "caption": caption,
            "legacy_caption": caption if legacy is None else legacy}


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
        assert ef.mentions("table", 2, "Table 2에")
        assert ef.mentions("figure", 2, "Fig. 2에")

    def test_a_lowercase_figure_mention_counts_as_a_table_one_does(self):
        blocks = [para("p1", "왼쪽의 fig. 5에서 보듯이")]
        assert ef._anchor_for(5, blocks) == "p1"

    def test_a_word_ending_in_fig_is_not_a_mention(self):
        blocks = [para("p1", "the config 2 setting"), para("p2", "Figure 2에서")]
        assert ef._anchor_for(2, blocks) == "p2"


class TestAListOrARangeOfTables:
    """One sentence often cites several tables at once. Missing the later ones
    sent them to a fallback, and the relocation then MOVED a table away from the
    sentence citing it."""

    @pytest.mark.parametrize("text, num", [
        ("결과는 Tables 2 and 3에 정리했다.", 2),
        ("결과는 Tables 2 and 3에 정리했다.", 3),
        ("결과는 Table 2와 3에 정리했다.", 3),
        ("결과는 Tables 2와 3에 정리했다.", 3),
        ("Tables 2, 3, and 4를 보라.", 4),
        ("Table 2, 3에서 비교한다.", 3),
        ("Table 2 및 3에서 비교한다.", 3),
        ("결과는 Table 2-4에 정리했다.", 3),
        ("결과는 표 2–4에 정리했다.", 3),
        ("결과는 표 2~4에서 볼 수 있다.", 4),
        ("Tables 2 to 4 compare them.", 3),
        ("Tab. 2에서 비교한다.", 2),
        ("Table~2에서 비교한다.", 2),
        ("Tables 2에서 비교한다.", 2),
        ("TABLES II and III에서", 3),
    ])
    def test_every_table_the_sentence_cites(self, text, num):
        roman = {v: k for k, v in ef.ROMAN_VALUES.items()}[num]
        assert ef.mentions("table", num, text, roman)

    @pytest.mark.parametrize("text, num", [
        ("Figures 2 and 3에서 보듯이", 3),
        ("Figs. 2–4를 보라.", 3),
        ("그림 5와 6에서", 6),
    ])
    def test_figures_too(self, text, num):
        assert ef.mentions("figure", num, text)

    @pytest.mark.parametrize("text, num", [
        ("Table 2와 3개의 실험을 비교한다.", 3),   # "Table 2 and three runs"
        ("표 2개를 비교한다.", 2),                  # "two tables"
        ("Table 2 and Figure 3에서", 3),
        ("Table 2-4에 정리했다.", 5),
        ("Table 2, 30에서", 3),
        ("Tables 2.1 and 2.2에서", 2),
    ])
    def test_what_is_not_a_mention(self, text, num):
        assert not ef.mentions("table", num, text)

    def test_a_list_does_not_switch_from_numbers_to_roman(self):
        assert not ef.mentions("table", 1, "Table 2 and I think so.", "i")

    def test_the_first_table_of_a_list_is_still_found(self):
        assert ef.mentions("table", 2, "Table 2와 3개의 실험을 비교한다.")


class TestAListGoesOnOnlyWithAnotherLabel:
    """A number after "Table 2, " or "Table 2와 " is another label only when
    nothing makes it a quantity: a section (`3절`), a duration (`3초`), a count
    (`3가지`), an ordinal (`3rd`), a unit (`4 m/s`, `32 GPUs`). Reading those
    as citations anchored the next float on ordinary prose, and the relocation
    moved a correctly placed table off the sentence citing it."""

    @pytest.mark.parametrize("text, num", [
        ("Table 2와 3절에서 설명한 설정을 비교한다.", 3),
        ("설정은 Table 2에 있다 (Table 1, 3rd row 참조).", 3),
        ("Table 2, 3 seeds 평균을 보고한다.", 3),
        ("Table 1, 2 GB의 메모리가 필요하다.", 2),
        ("In Table 1, 2 of the 5 methods fail.", 2),
        ("Table 1, 5-shot 설정이다.", 5),
        ("표 2와 3년 동안의 데이터", 3),
    ])
    def test_tables(self, text, num):
        assert not ef.mentions("table", num, text)

    @pytest.mark.parametrize("text, num", [
        ("그림 2, 3D 재구성 결과를 보인다.", 3),
        ("(그림 4, 3초 후의 상태)", 3),
        ("(그림 4, 3분 동안)", 3),
        ("그림 2와 3가지 방법", 3),
        ("Figure 2 and 3 epochs are compared.", 3),
        ("As shown in Figure 2, 3 layers suffice.", 3),
        ("Figure 2 to 7 times faster", 7),
        ("Fig. 3 - 10 trials", 10),
        ("Figure 2, 32 GPUs", 32),
        ("Fig. 3, 4 m/s", 4),
        ("Figure 1 to 3D", 3),
        ("Figure 1, and 3 more", 3),
    ])
    def test_figures(self, text, num):
        assert not ef.mentions("figure", num, text)

    @pytest.mark.parametrize("kind, text, num", [
        ("figure", "Figures 3 to 5 show the rollouts.", 4),
        ("figure", "Fig. 1 & 2", 2),
        ("figure", "Figure 7, 8 and 9.", 9),
        ("figure", "그림 1, 2", 2),
        ("table", "Table 2와 3 참조.", 3),
        ("table", "Tables 2 and 3 show it.", 3),
        ("table", "Tables A.1 and A.2 show it.", "A.2"),
        ("table", "TABLE II and III show it.", 3),
    ])
    def test_a_real_list_still_cites_every_label(self, kind, text, num):
        roman = {3: "iii"}.get(num, "") if "II" in text else ""
        assert ef.mentions(kind, num, text, roman)

    def test_the_prose_is_no_citation_to_move_a_table_to(self):
        source = [table("S2.T1", "Table 1: Setup."), table("S2.T2", "Table 2: Settings."),
                  table("S4.T3", "Table 3: Main results.")]
        blocks = [heading("h1", "1 Introduction (서론)"),
                  para("p1", "Table 2와 3절에서 설명한 설정을 비교한다."),
                  heading("h2", "2 Method (방법)"),
                  para("p2", "Table 1 참조. Table 2 참조."),
                  img("i1", "Table 1: Setup."), img("i2", "Table 2: Settings."),
                  heading("h4", "4 Experiments (실험)"),
                  para("p4", "주요 결과는 Table 3 참조."),
                  img("i3", "Table 3: Main results.")]
        assert et.table_anchors(source, blocks)["S4.T3"] == ("p4", True)
        assert "i3" not in moved(et.plan_relocation(blocks, source, ME))


class TestAnOrdinalStillCites:
    """`그림 2번` is "figure number 2"; only a count (`표 2개`) is not a citation."""

    @pytest.mark.parametrize("kind, text, num", [
        ("figure", "그림 2번에서 보듯이 정책이 수렴한다.", 2),
        ("table", "표 3번을 보면", 3),
        ("figure", "그림 2와 3번에서", 3),
    ])
    def test_it(self, kind, text, num):
        assert ef.mentions(kind, num, text)

    def test_a_count_is_still_no_citation(self):
        assert not ef.mentions("table", 2, "표 2개를 비교한다.")


class TestTheSameSyllableAfterAListCanBeAMeasure:
    """`번` and `도` glue onto a label as well as onto a count: `그림 2와 3번에서`
    cites figure 3, `3번째` is "third", `3번 반복` is "three times", and after a
    comma `30도` is thirty degrees. A product (`3×3`) and a fraction (`3/4`) are
    measures too. Read as labels, they anchored a float on prose."""

    @pytest.mark.parametrize("kind, text, num", [
        ("figure", "(그림 2, 3번째 행)", 3),
        ("table", "(Table 1, 2번째 열 참조)", 2),
        ("figure", "그림 2, 3번 반복한 결과", 3),
        ("figure", "그림 2와 3번씩 반복했다.", 3),
        ("figure", "(그림 2, 30도 회전)", 30),
        ("table", "Table 2, 3×3 grid", 3),
        ("table", "Table 2, 3 × 3 grid", 3),
        ("table", "Tables 2, 3 x 3 grids", 3),
        ("figure", "Figure 2, 3/4 of the runs", 3),
    ])
    def test_a_measure_ends_the_list(self, kind, text, num):
        assert not ef.mentions(kind, num, text)

    @pytest.mark.parametrize("kind, text, num", [
        ("figure", "그림 2번에서 보듯이", 2),
        ("figure", "그림 2와 3번에서", 3),
        ("figure", "그림 2와 3번 참조.", 3),
        ("figure", "(그림 2, 3번)", 3),
        ("figure", "그림 2와 3도 함께 보면", 3),
        ("table", "Table 2, 3에서 3×3 grid를 쓴다.", 3),
    ])
    def test_a_label_still_cites(self, kind, text, num):
        assert ef.mentions(kind, num, text)


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


class TestOnlyATopLevelHeadingIsTheSection:
    """`6-DoF Pose Tracking`, `3) Reward` and a local `4. Flexibility` all open
    with a number, and taking the first such heading put a float of section 6,
    III or IV inside another section — and the relocation moved a table that
    already sat under its own `6 Limitations` there."""

    DOF = [heading("h1", "1 Introduction (서론)"), para("p1", "서론."),
           heading("h3", "3 Method (방법)"), para("p3", "방법."),
           heading("h3x", "6-DoF Pose Tracking (6-DoF 포즈 추적)", 2), para("q3", "추적."),
           heading("h4", "4 Experiments (실험)"), para("p4", "실험."),
           heading("h6", "6 Limitations (한계)"), para("p6", "한계.")]

    def test_a_title_opening_with_a_number_is_no_section(self):
        assert et.table_anchors([table("S6.T4", "Table 4: Failures.")],
                                self.DOF)["S6.T4"] == ("h6", False)
        assert ef._anchor_for("7", self.DOF, "S6.F7") == "h6"

    def test_a_table_under_its_own_section_stays(self):
        blocks = self.DOF[:-1] + [img("t6", "Table 6: Failures."), self.DOF[-1]]
        plan = et.plan_relocation(blocks, [table("S6.T6", "Table 6: Failures.")], ME)
        assert plan["moves"] == []

    def test_an_ieee_item_label_is_no_section(self):
        blocks = [heading("h1", "I. INTRODUCTION (서론)"), para("p1", "서론."),
                  heading("h2", "II. METHOD (방법)"),
                  heading("h2a", "A. Policy (정책)", 2),
                  heading("h2a3", "3) Reward (보상)", 3), para("q3", "보상."),
                  heading("h3", "III. EXPERIMENTS (실험)"), para("p3", "실험.")]
        assert et.table_anchors([table("S3.T2", "TABLE II: Results.")],
                                blocks)["S3.T2"] == ("h3", False)
        assert ef._anchor_for("3", blocks, "S3.F3") == "h3"

    def test_a_section_heading_beats_a_local_subsection_number(self):
        blocks = [heading("hIII", "III. Architecture", 2), para("p0", "본문."),
                  heading("h1", "1. Backbone", 3), heading("h2", "2. Goal conditioning", 3),
                  heading("h4", "4. Flexibility", 3), para("p4", "본문."),
                  heading("hIV", "IV. Training data", 2), para("p5", "본문.")]
        assert et.table_anchors([table("S4.T2", "TABLE II: Mixture.")],
                                blocks)["S4.T2"] == ("hIV", False)

    def test_a_local_item_of_another_section_is_not_the_section(self):
        # Section 4 shows only as `4.1`/`4.2`; the `4.` is the fourth item of
        # section 2, and taking it moved an uncited table of section 4 there.
        blocks = [heading("h1", "1 Introduction (서론)"), para("p1", "서론."),
                  heading("h2", "2 Method (방법)"), para("p2", "방법."),
                  heading("i1", "1. Backbone (백본)", 3), heading("i2", "2. Goal (목표)", 3),
                  heading("i4", "4. Flexibility (유연성)", 3), para("q4", "본문."),
                  heading("h3", "3 Training (학습)"), para("p3", "학습."),
                  heading("h41", "4.1 Setup (설정)", 2), para("p41", "설정."),
                  heading("h42", "4.2 Results (결과)", 2), para("p42", "결과."),
                  heading("h5", "5 Conclusion (결론)"), para("p5", "결론.")]
        assert et.table_anchors([table("S4.T2", "Table 2: Ablation.")],
                                blocks)["S4.T2"] == ("h41", False)
        assert ef._anchor_for("3", blocks, "S4.F3") == "h41"

    def test_nor_one_of_a_later_section(self):
        blocks = [heading("h3", "3 Training (학습)"), para("p3", "학습."),
                  heading("h41", "4.1 Setup (설정)", 2), para("p41", "설정."),
                  heading("h5", "5 Conclusion (결론)"), para("p5", "결론."),
                  heading("k", "핵심 요약", 2),
                  heading("i3", "3. Cost (비용)", 3), heading("i4", "4. Limits (한계)", 3)]
        assert et.table_anchors([table("S4.T2", "Table 2: Ablation.")],
                                blocks)["S4.T2"] == ("h41", False)

    def test_nor_a_heading_out_of_order_with_its_neighbours(self):
        # Between `2` and `4`, a `6.` is not where section 6 begins.
        blocks = [heading("h1", "1 Introduction (서론)"), para("p1", "서론."),
                  heading("h2", "2 Related Work (관련 연구)"), para("p2", "본문."),
                  heading("g6", "6. 요약"), para("q6", "본문."),
                  heading("g4", "4. 요약"), para("q4", "본문.")]
        assert et.table_anchors([table("S6.T9", "Table 9: Results.")],
                                blocks)["S6.T9"] == (None, False)

    def test_a_section_the_page_opens_at_its_first_subsection(self):
        blocks = [heading("h5", "5 Post-Training"), para("p5", "본문."),
                  heading("h61", "6.1 Dense features", 3), para("p61", "본문."),
                  heading("h616", "6.1.6 Video classification", 3),
                  heading("h7", "7 Evaluation"), para("p7", "본문.")]
        assert et.table_anchors([table("S6.T6", "Table 6: Video.")],
                                blocks)["S6.T6"] == ("h61", False)


def section_html(sid, tag, title, body="", cls="ltx_section"):
    """A top-level LaTeXML section, with its printed label in an `ltx_tag`."""
    return (f'<section id="{sid}" class="{cls}"><h2 class="ltx_title">'
            f'<span class="ltx_tag ltx_tag_section">{tag} </span>{title}</h2>'
            f'{body}</section>')


def table_html(tid, caption):
    return (f'<figure id="{tid}" class="ltx_table"><figcaption class="ltx_caption">'
            f'{caption}</figcaption><table><tr><td>1</td></tr></table></figure>')


class TestTheSectionIsTheOneTheSourcePrintsItIn:
    """The section comes from the source, not from the page's numbers: a page
    may number its own sections, and an id may not name its section at all."""

    SUMMARY_HTML = "".join(
        section_html(f"S{n}", n, title,
                     table_html("S2.T1", "Table 1: Prior work.") if n == 2 else
                     table_html("S4.T3", "Table 3: Clip scenarios.") if n == 4 else "")
        for n, title in [(1, "Introduction"), (2, "Related Work"), (3, "Architecture"),
                         (4, "Causal Dataset"), (5, "Training Strategy")])
    SUMMARY = [heading("g1", "1. Limits of prior work"), para("q1", "본문."),
               heading("g2", "2. Contributions"), para("q2", "본문."),
               heading("g3", "3. Causal Dataset"), img("iT3", "Table 3: Clip scenarios."),
               para("q3", "본문."),
               heading("g4", "4. The model"), para("q4", "본문."),
               heading("g5", "5. Training Strategy"), para("q5", "본문.")]

    def test_the_heading_carrying_the_sections_title(self):
        tables = et.parse_tables(self.SUMMARY_HTML)
        sections = ef.source_sections(self.SUMMARY_HTML)
        assert et.table_anchors(tables, self.SUMMARY, sections)["S4.T3"] == ("g3", False)
        plan = et.plan_relocation(self.SUMMARY, tables, ME, sections=sections)
        assert plan["moves"] == []

    def test_no_number_on_a_page_numbered_apart_from_the_source(self):
        # Its '3.' is the source's 4, so its '2.' says nothing about section 2.
        tables = et.parse_tables(self.SUMMARY_HTML)
        sections = ef.source_sections(self.SUMMARY_HTML)
        assert et.table_anchors(tables, self.SUMMARY, sections)["S2.T1"] == (None, False)

    def test_no_number_without_a_heading_that_shows_the_numbering(self):
        # Korean titles alone cannot show that '4.' is the source's 4.
        page = [heading("k1", "1. 서론"), heading("k3", "3. 구현"),
                heading("k4", "4. 성능 평가"), para("q4", "본문.")]
        tables = et.parse_tables(self.SUMMARY_HTML)
        sections = ef.source_sections(self.SUMMARY_HTML)
        assert et.table_anchors(tables, page, sections)["S4.T3"] == (None, False)

    def test_a_number_on_a_page_that_shows_it_numbers_as_the_source(self):
        page = [heading("e1", "1 Introduction (서론)"), heading("e2", "2 관련 연구"),
                heading("e3", "3 Architecture (구조)"),
                heading("e4", "4 데이터셋"), para("q4", "본문.")]
        tables = et.parse_tables(self.SUMMARY_HTML)
        sections = ef.source_sections(self.SUMMARY_HTML)
        anchors = et.table_anchors(tables, page, sections)
        assert anchors["S4.T3"] == ("e4", False)
        assert anchors["S2.T1"] == ("e2", False)

    def test_one_agreeing_heading_is_not_enough(self):
        # A summary opens with `1. Introduction` as the paper does and numbers
        # the rest itself: its `4.` is a conclusion, not the source's dataset.
        page = [heading("g1", "1. Introduction (서론)"), para("q1", "본문."),
                heading("g2", "2. 핵심 아이디어"), para("q2", "본문."),
                heading("g3", "3. 주요 실험 결과"), para("q3", "본문."),
                heading("g4", "4. 결론 및 한계"), para("q4", "본문.")]
        tables = et.parse_tables(self.SUMMARY_HTML)
        sections = ef.source_sections(self.SUMMARY_HTML)
        assert not ef._numbering_mirrors(page, sections)
        assert et.table_anchors(tables, page, sections)["S4.T3"] == (None, False)
        assert et.plan_relocation(page + [img("i3", "Table 3: Clip scenarios.")],
                                  tables, ME, sections=sections)["moves"] == []

    def test_a_heading_that_shortens_a_long_title_agrees_with_it(self):
        html = "".join(section_html(f"S{n}", n, title,
                                    table_html("S2.T1", "Table 1: Benchmark.") if n == 2 else "")
                       for n, title in [(1, "Introduction"), (2, "Embodied Reasoning"),
                                        (3, "Robot Actions"),
                                        (4, "Specializing and Adapting the Model for "
                                            "New Embodiments")])
        page = [heading("m1", "1. Introduction (서론)"), para("q1", "본문."),
                heading("m2", "2. 모델 제품군"), para("q2", "본문."),
                heading("m4", "4. Specializing and Adapting the Model"), para("q4", "본문.")]
        sections = ef.source_sections(html)
        assert ef._numbering_mirrors(page, sections)
        assert et.table_anchors(et.parse_tables(html), page, sections)["S2.T1"] == ("m2", False)

    def test_a_title_in_the_bracket_counts_too(self):
        page = [heading("g1", "1. 기존 연구의 한계"), heading("g3", "3. 인과 데이터셋 (Causal Dataset)")]
        tables = et.parse_tables(self.SUMMARY_HTML)
        assert et.table_anchors(tables, page)["S4.T3"] == ("g3", False)

    def test_a_restarted_part_is_not_the_body_section_of_its_number(self):
        # A supplementary part restarts the section ids (S1a, S2a); the floats
        # inside keep ids S1.Tn, S2.Tn.
        html = (section_html("S1", 1, "Introduction") + section_html("S2", 2, "Related Work")
                + section_html("S1a", "A", "Supplementary data")
                + section_html("S2a", "B", "Supplementary results",
                               table_html("S2.T9", "Table 9: Extra results.")))
        blocks = [heading("h1", "1. Introduction"), para("p1", "본문."),
                  heading("h2", "2. Related Work"), para("p2", "본문."),
                  heading("ha", "Appendix A: Supplementary data"), para("pa", "본문."),
                  heading("hb", "Appendix B: Supplementary results"), para("pb", "본문."),
                  img("i9", "Table 9: Extra results.")]
        tables = et.parse_tables(html)
        sections = ef.source_sections(html)
        assert et.table_anchors(tables, blocks, sections)["S2.T9"] == ("hb", False)
        # It already sits in that section: no reason to move it.
        assert et.plan_relocation(blocks, tables, ME, sections=sections)["moves"] == []

    def test_a_paragraph_marked_as_a_section_is_not_one(self):
        # Some renders mark a run-in paragraph `ltx_section` too.
        html = section_html("A1", "Appendix A", "Appendix",
                            '<section id="subsubsubsectionx3" class="ltx_section">'
                            '<h5 class="ltx_title">Joint deviation</h5>'
                            + table_html("A1.T11", "Table 11: Reward terms.") + '</section>',
                            cls="ltx_appendix")
        assert et.parse_tables(html)[0]["section"]["id"] == "A1"


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
            {"id": "S4.T5", "num": 5, "caption": "Table 5: Prompt examples.",
             "legacy_caption": ""}]

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

    def test_a_figure_the_id_wraps_carries_its_caption(self):
        # A rotated table: the id is on a wrapper, the figure with the caption
        # inside it, and the label is none the id's counter could match.
        html = ('<div id="S2.T2" class="ltx_table ltx_transformed_outer">'
                '<div class="ltx_transformed_inner"><figure>'
                '<figcaption>Table SI-2: Every training run.</figcaption>'
                '<object id="S2.T2.g1" class="ltx_graphics"></object>'
                '</figure></div></div>')
        assert et.parse_tables(html)[0]["caption"] == "Table SI-2: Every training run."

    def test_what_the_old_parser_read_is_kept_beside_it(self):
        # Pages placed before the fix carry THAT caption, and only it says which
        # table such an image shows.
        html = ('<figure class="ltx_table" id="S2.T3">'
                '<figure class="ltx_figure_panel" id="S2.T3.sf1">'
                '<figcaption>(a) Left half.</figcaption><table></table></figure>'
                '<figcaption>Table 3: Both halves.</figcaption></figure>')
        assert et.parse_tables(html)[0]["legacy_caption"] == "(a) Left half."


# --- a forced run replaces what the old parser wrote --------------------------


LONG_TABLE = ('<figure class="ltx_table" id="S4.T5"><table>'
              + "".join(f"<tr><td>prompt line {i} " + "x" * 80 + "</td></tr>"
                        for i in range(200))
              + '</table><figcaption class="ltx_caption">Table 5: Prompt '
              'examples.</figcaption></figure>')


class TestAForcedRunReplacesWhatTheOldParserWrote:
    """`--force` archives the images its new copies stand in for, matched by
    caption. A page placed before the caption fix carries the OLD caption, so
    matching only the new one left two copies of the table — and no tool could
    clear them: one is bare, one captioned, so no caption repeats."""

    def run_forced(self, monkeypatch, blocks, html):
        import notion_upload
        import translate_fulltext
        import verify_sections as vs

        calls = []
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: blocks)
        monkeypatch.setattr(ef, "fetch_html", lambda _a: (html, "stub"))
        monkeypatch.setattr(et, "render_tables", lambda _a, tables, _o, _s: [
            {"id": t["id"], "num": t["num"], "caption": t["caption"], "path": "x.png"}
            for t in tables])
        monkeypatch.setattr(notion_upload, "upload_image", lambda _p: "upload-id")
        monkeypatch.setattr(translate_fulltext, "notion",
                            lambda method, path, body=None: calls.append(
                                (method, path, body)) or {})
        monkeypatch.setattr("time.sleep", lambda _s: None)
        et.inject_tables("page", "arxiv-id", apply=True, force=True,
                         keep_text=True)
        return [path.rsplit("/", 1)[1] for method, path, body in calls
                if body == {"archived": True}]

    def test_the_bare_copy_is_replaced(self, monkeypatch):
        body = [para(f"p{i}", "본문 문장입니다. " * 20) for i in range(8)]
        blocks = body + [para("p4", "Table 5의 예시를 보라."), img("i5", "Table 5")]
        assert self.run_forced(monkeypatch, blocks, LONG_TABLE) == ["i5"]

    def test_never_a_caption_another_table_now_has(self):
        # T5 has no caption of its own, so the old parser read T6's. Archiving on
        # that key would remove T6's image although this run never re-placed it.
        html = ('<figure class="ltx_table" id="S4.T5"><table></table></figure>'
                '<figure class="ltx_table" id="S4.T6"><figcaption>Table 6: Next.'
                '</figcaption><table></table></figure>')
        tables = et.parse_tables(html)
        keys = et.forced_keys([tables[0]], tables)
        assert et.replaced_by([img("i6", "Table 6: Next.")], keys) == []
        assert et.replaced_by([img("i5", "Table 5")], keys) == ["i5"]


# --- relocating the tables already on a page --------------------------------


SOURCE = [table("S1.T1", "Table 1: Settings."),
          table("S3.T2", "Table 2: Main results."),
          table("S3.T3", "Table 3: Ablation."),
          table("S4.T5", "Table 5: Prompt examples.", legacy="")]


def page(*middle):
    return ([heading("h1", "1 Introduction (서론)"),
             para("p1", "설정은 Table 1에 정리했다.")]
            + list(middle)
            + [heading("h6", "6 Conclusion (결론)"), para("p6", "끝.")])


def apply_plan(blocks, plan):
    """What the page looks like after `plan` ran: new images in, old ones out."""
    out = list(blocks)
    for move in plan["moves"]:
        at = next(i for i, b in enumerate(out) if b["id"] == move["after"])
        out[at + 1:at + 1] = [img("new-" + it["old"], it["caption"])
                              for it in move["items"]]
    gone = {it["old"] for move in plan["moves"] for it in move["items"]}
    out = [b for b in out if b["id"] not in gone]
    for fix in plan["recaption"]:
        b = next(b for b in out if b["id"] == fix["block"])
        b["image"]["caption"] = [{"plain_text": fix["caption"]}]
    return out


def moved(plan):
    return {it["old"]: move["after"] for move in plan["moves"] for it in move["items"]}


CORRECT = page(img("i1", "Table 1: Settings."),
               heading("h2", "2 Method (방법)"),
               para("p2", "방법 설명."),
               heading("h3", "3 Experiments (실험)"),
               para("p3", "결과는 Table 2와 Table 3에 있다."),
               img("i2", "Table 2: Main results."),
               img("i3", "Table 3: Ablation."),
               heading("h4", "4 Analysis (분석)"),
               para("p4", "Table 5의 예시를 보라."),
               img("i5", "Table 5: Prompt examples."))


class TestPlanningARelocation:

    def test_nothing_moves_on_a_correct_page(self):
        plan = et.plan_relocation(CORRECT, SOURCE, ME)
        assert plan["moves"] == [] and plan["recaption"] == []

    def test_a_table_under_its_numbers_heading_moves_to_its_mention(self):
        blocks = page(img("i1", "Table 1: Settings."),
                      heading("h2", "2 Method (방법)"),
                      img("i2", "Table 2: Main results."),
                      para("p2", "방법 설명."),
                      heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 2와 Table 3에 있다."),
                      img("i3", "Table 3: Ablation."))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert moved(plan) == {"i2": "p3"}
        item = plan["moves"][0]["items"][0]
        assert item["caption"] == "Table 2: Main results."
        assert item["url"].startswith("https://files.example/i2.png")

    def test_a_table_at_the_end_of_the_body_moves_to_its_section(self):
        blocks = page(img("i1", "Table 1: Settings."),
                      heading("h4", "4 Analysis (분석)"),
                      para("p4", "예시는 아래와 같다."))
        blocks.append(img("i5", "Table 5: Prompt examples."))
        assert moved(et.plan_relocation(blocks, SOURCE, ME)) == {"i5": "h4"}

    def test_a_bare_caption_is_replaced_by_the_source_caption(self):
        blocks = page(heading("h4", "4 Analysis (분석)"),
                      para("p4", "Table 5의 예시를 보라."),
                      para("p4b", "다음 문단."),
                      img("i5", "Table 5"))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert moved(plan) == {"i5": "p4"}
        assert plan["moves"][0]["items"][0]["caption"] == "Table 5: Prompt examples."

    def test_a_bare_caption_in_the_right_place_is_recaptioned_in_place(self):
        blocks = page(heading("h4", "4 Analysis (분석)"),
                      para("p4", "Table 5의 예시를 보라."),
                      img("i5", "Table 5"))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert plan["moves"] == []
        # The caption it replaces is in the plan, so the change can be undone.
        assert plan["recaption"] == [{"block": "i5", "was": "Table 5",
                                      "caption": "Table 5: Prompt examples."}]

    def test_two_tables_of_one_anchor_keep_their_numeric_order(self):
        blocks = page(heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 2와 Table 3에 있다."),
                      para("p3b", "다음 문단."),
                      img("i3", "Table 3: Ablation."),
                      img("i2", "Table 2: Main results."))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert [(m["after"], [it["old"] for it in m["items"]])
                for m in plan["moves"]] == [("p3", ["i2", "i3"])]

    def test_an_out_of_order_table_is_put_back_in_order(self):
        blocks = page(heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 2와 Table 3에 있다."),
                      img("i3", "Table 3: Ablation."),
                      img("i2", "Table 2: Main results."))
        after = apply_plan(blocks, et.plan_relocation(blocks, SOURCE, ME))
        caps = [b["image"]["caption"][0]["plain_text"] for b in after
                if b["type"] == "image"]
        assert caps == ["Table 2: Main results.", "Table 3: Ablation."]

    def test_a_figure_between_the_anchor_and_the_table_is_fine(self):
        blocks = page(heading("h3", "3 Experiments (실험)"),
                      para("p3", "Figure 4와 Table 2에 결과가 있다."),
                      img("f4", "Figure 4: A plot."),
                      img("i2", "Table 2: Main results."))
        assert et.plan_relocation(blocks, SOURCE, ME)["moves"] == []

    def test_an_image_a_person_edited_is_left_alone(self):
        blocks = page(heading("h2", "2 Method (방법)"),
                      img("i2", "Table 2: Main results.", edited=PERSON),
                      heading("h3", "3 Experiments (실험)"),
                      para("p3", "Table 2에 있다."))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert plan["moves"] == []
        assert [x["block"] for x in plan["left"]] == ["i2"]

    def test_an_image_a_person_added_is_left_alone(self):
        blocks = page(heading("h2", "2 Method (방법)"),
                      img("i2", "Table 2: Main results.", made=PERSON, edited=PERSON),
                      para("p3", "Table 2에 있다."))
        assert et.plan_relocation(blocks, SOURCE, ME)["moves"] == []

    def test_without_knowing_who_we_are_nothing_is_ours(self):
        blocks = page(heading("h2", "2 Method (방법)"),
                      img("i2", "Table 2: Main results."),
                      para("p3", "Table 2에 있다."))
        assert et.plan_relocation(blocks, SOURCE, None)["moves"] == []

    def test_a_table_with_no_anchor_stays_where_it_is(self):
        blocks = [heading("h0", "Introduction (서론)"),
                  para("p0", "본문."), img("i3", "Table 3: Ablation.")]
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert plan["moves"] == []

    def test_an_image_the_source_does_not_have_stays(self):
        blocks = page(heading("h2", "2 Method (방법)"),
                      img("i9", "Table 9: Something else."))
        assert et.plan_relocation(blocks, SOURCE, ME)["moves"] == []

    def test_a_runaway_page_is_refused(self):
        blocks = page(img("i2", "Table 2: Main results."),
                      img("i2b", "Table 2: Main results."))
        assert "refused" in et.plan_relocation(blocks, SOURCE, ME)

    def test_the_reference_list_is_never_touched(self):
        refs = [heading("hr", "References"),
                para("r1", "[1] A. Author. A first title. 2020."),
                para("r2", "[2] B. Author. A second title. 2021."),
                para("r3", "[3] C. Author. A third title, Table 2. 2022."),
                img("i2", "Table 2: Main results.")]
        blocks = page(heading("h2", "2 Method (방법)"),
                      para("p2", "방법 설명.")) + refs
        assert et.plan_relocation(blocks, SOURCE, ME)["moves"] == []
        # ...and nothing in it is an anchor for a table in the body.
        body = page(heading("h2", "2 Method (방법)"),
                    img("i2", "Table 2: Main results.")) + refs[:4]
        assert et.plan_relocation(body, [table("S2.T2", "Table 2: Main results.")],
                                  ME)["moves"] == []

    def test_a_second_plan_is_empty(self):
        blocks = page(img("i1", "Table 1"),
                      heading("h2", "2 Method (방법)"),
                      img("i2", "Table 2: Main results."),
                      img("i5", "Table 5"),
                      para("p2", "방법 설명."),
                      heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 3과 Table 2에 있다."),
                      img("i3", "Table 3: Ablation."),
                      heading("h4", "4 Analysis (분석)"),
                      para("p4", "Table 5의 예시를 보라."))
        # Placed when Table 1's caption was out of the old parser's reach too.
        source = [table("S1.T1", "Table 1: Settings.", legacy="")] + SOURCE[1:]
        first = et.plan_relocation(blocks, source, ME)
        assert first["moves"] and first["recaption"]
        second = et.plan_relocation(apply_plan(blocks, first), source, ME)
        assert second["moves"] == [] and second["recaption"] == []


class TestWhichTableAnImageShows:
    """The caption is the image's only identity. A number alone names a different
    table as soon as the numbering differs from the page's: the source was
    revised, or it numbers per chapter."""

    def test_a_caption_from_an_earlier_version_names_no_table(self):
        # v2 inserted a table 2; the page still shows v1's 2 and 3.
        v2 = [table("S2.T1", "Table 1: Setup."),
              table("S3.T2", "Table 2: New baseline comparison."),
              table("S3.T3", "Table 3: Main results."),
              table("S4.T4", "Table 4: Ablation.")]
        blocks = [heading("h1", "1 Introduction (서론)"),
                  para("p1", "설정은 Table 1에 있다."),
                  img("i1", "Table 1: Setup."),
                  heading("h3", "3 Experiments (실험)"),
                  para("p3", "주요 결과는 Table 2에 있다."),
                  img("i2", "Table 2: Main results."),
                  heading("h4", "4 Analysis (분석)"),
                  para("p4", "ablation은 Table 3에 있다."),
                  img("i3", "Table 3: Ablation.")]
        plan = et.plan_relocation(blocks, v2, ME)
        assert plan["moves"] == [] and plan["recaption"] == []
        assert {x["block"] for x in plan["left"]} == {"i2", "i3"}

    def test_a_chapter_numbered_label_is_not_its_chapter(self):
        source = [table("S2.T1", "Table 2.1: Alpha results (revised wording)."),
                  table("S2.T2", "Table 2.2: Beta results.")]
        blocks = [heading("h2", "2 Method (방법)"),
                  para("p2", "결과는 Table 2.1에 있다."),
                  img("i21", "Table 2.1: Alpha results.")]
        plan = et.plan_relocation(blocks, source, ME)
        assert plan["moves"] == [] and plan["recaption"] == []

    def test_a_bare_caption_the_old_parser_never_wrote_names_no_table(self):
        # Table 3's caption was always in reach, so no injector wrote `Table 3`.
        blocks = page(heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 3에 있다."), img("i3", "Table 3"))
        plan = et.plan_relocation(blocks, SOURCE, ME)
        assert plan["moves"] == [] and plan["recaption"] == []

    def test_a_panel_caption_the_old_parser_wrote_is_still_its_table(self):
        source = [table("S3.T3", "Table 3: Both halves.", legacy="(a) Left half.")]
        blocks = page(heading("h3", "3 Experiments (실험)"),
                      para("p3", "결과는 Table 3에 있다."),
                      img("i3", "Table 3: (a) Left half."))
        plan = et.plan_relocation(blocks, source, ME)
        assert [fix["caption"] for fix in plan["recaption"]] == ["Table 3: Both halves."]


class TestARelocationNeverMovesATableOffItsCitation:

    def test_a_table_cited_with_its_neighbour_stays(self):
        source = [table("S4.T2", "Table 2: Main results."),
                  table("S4.T3", "Table 3: Ablation.")]
        blocks = [heading("h4", "4 Experiments (실험)"),
                  para("p4", "실험 설정을 설명한다."),
                  para("p4b", "결과는 Tables 2 and 3에 정리했다."),
                  img("i2", "Table 2: Main results."), img("i3", "Table 3: Ablation."),
                  heading("h5", "5 Conclusion (결론)"), para("p5", "끝.")]
        assert et.plan_relocation(blocks, source, ME)["moves"] == []

    def test_a_section_fallback_does_not_lift_a_table_off_a_paragraph(self):
        # No rule sees a mention here, yet the table follows text — it may be
        # the sentence citing it in words the rule does not know.
        source = [table("S4.T2", "Table 2: Main results.")]
        blocks = [heading("h3", "3 Experiments (실험)"),
                  para("p3", "두 방법의 결과를 아래 표에 정리했다."),
                  img("i2", "Table 2: Main results."),
                  heading("h4", "4 Analysis (분석)"), para("p4", "분석.")]
        plan = et.plan_relocation(blocks, source, ME)
        assert plan["moves"] == []
        assert [x["block"] for x in plan["left"]] == ["i2"]

    def test_a_section_fallback_still_lifts_a_table_off_a_wrong_heading(self):
        # Where the old number fallback put it: right under `5 …`.
        blocks = page(heading("h4", "4 Analysis (분석)"),
                      para("p4", "예시는 아래와 같다."),
                      heading("h5", "5 Discussion (논의)"),
                      img("i5", "Table 5: Prompt examples."),
                      para("p5", "논의."))
        assert moved(et.plan_relocation(blocks, SOURCE, ME)) == {"i5": "h4"}


class TestAnAppendixThatRestartsTheNumbering:
    """`S2.T1` and `A1.T1` are both number 1; the appendix one prints `Table A.1`.
    The mention to look for is the printed label, not the id's counter."""

    SOURCE = [table("S2.T1", "Table 1: Main results."),
              table("A1.T1", "Table A.1: Extra hyperparameters.")]
    PAGE = [heading("h2", "2 Method (방법)"),
            para("p2", "주요 결과는 Table 1과 같다."),
            img("i1", "Table 1: Main results."),
            heading("ha", "Appendix A Hyperparameters (부록 A)"),
            para("pa", "하이퍼파라미터는 Table A.1에 정리했다."),
            img("ia", "Table A.1: Extra hyperparameters.")]

    def test_the_appendix_table_anchors_on_its_own_label(self):
        assert et.table_anchors(self.SOURCE, self.PAGE)["A1.T1"] == ("pa", True)

    def test_a_correct_page_stays_as_it_is(self):
        assert et.plan_relocation(self.PAGE, self.SOURCE, ME)["moves"] == []

    def test_a_number_two_tables_share_is_no_label(self):
        # No printed label, and the counter's 1 is the body table's too.
        source = [self.SOURCE[0], table("A1.T1", "")]
        assert et.table_anchors(source, self.PAGE)["A1.T1"] == ("ha", False)


def appendix_html(rows_long: bool):
    """`S3.T5` in the body and `A1.T5` in the appendix: the id counter restarts.
    A long appendix table keeps its caption past the old parser's window."""
    rows = "".join(f"<tr><td>prompt line {i} " + "x" * 80 + "</td></tr>"
                   for i in range(120 if rows_long else 1))
    return (section_html("S3", 3, "Results", table_html("S3.T5", "Table 5: Main results."))
            + section_html("A1", "Appendix A", "Prompts",
                           '<figure id="A1.T5" class="ltx_table"><table>' + rows
                           + '</table><figcaption>Table A.5: Prompt used for evaluation.'
                           '</figcaption></figure>', cls="ltx_appendix"))


class TestAnImageTheOldRendererMadeForTwoTables:
    """The old renderer made ONE image per id counter: the first table's picture
    under the last one's caption. With a restarted counter that caption names
    the appendix table while the picture shows the body's."""

    @pytest.mark.parametrize("rows_long, caption", [
        (True, "Table 5"),                                   # the old bare caption
        (False, "Table A.5: Prompt used for evaluation."),  # the last table's own
    ])
    def test_it_is_left_where_it_is(self, rows_long, caption):
        html = appendix_html(rows_long)
        blocks = [heading("h3", "3 Results (결과)"),
                  para("p3", "주요 결과는 Table 5에 있다."), img("i5", caption),
                  para("p3b", "본문 " * 50),
                  heading("ha", "Appendix A Prompts (부록 A)"),
                  para("pa", "평가 prompt는 Table A.5에 있다.")]
        plan = et.plan_relocation(blocks, et.parse_tables(html), ME,
                                  sections=ef.source_sections(html))
        assert plan["moves"] == [] and plan["recaption"] == []
        assert [x["block"] for x in plan["left"]] == ["i5"]


class TestANewPageWithARestartedCounter:
    """The injector keyed the picture, the caption and the anchor on the id
    counter: one image for `S3.T5` and `A1.T5` together, showing the body's
    table, captioned and placed as the appendix's — and the appendix table was
    never placed, since its number read as placed."""

    def run_cycle(self, monkeypatch, page, html):
        import notion_upload
        import translate_fulltext
        import verify_sections as vs

        def notion(method, path, body=None):
            if path.endswith("/children"):
                new = [{"id": f"img-{len(page)}", "type": "image",
                        "picture": c["image"]["file_upload"]["id"],
                        "image": {"caption": [{"plain_text": x["text"]["content"]}
                                             for x in c["image"]["caption"]]}}
                       for c in body["children"]]
                at = next(i for i, b in enumerate(page) if b["id"] == body["after"])
                page[at + 1:at + 1] = new
            return {}

        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: [dict(b) for b in page])
        monkeypatch.setattr(ef, "fetch_html", lambda _a: (html, "stub"))
        monkeypatch.setattr(et, "render_tables", lambda _a, tables, _o, _s: [
            {"id": t["id"], "num": t["num"], "caption": t["caption"],
             "path": "picture-of-" + t["id"]} for t in tables])
        monkeypatch.setattr(notion_upload, "upload_image", lambda path: path)
        monkeypatch.setattr(translate_fulltext, "notion", notion)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        return et.inject_tables("page", "arxiv-id", apply=True, keep_text=True)

    def test_each_table_gets_its_own_picture_caption_and_place(self, monkeypatch):
        page = [heading("h3", "3 Results (결과)"),
                para("p3", "주요 결과는 Table 5에 있다. " + "본문 " * 600),
                heading("ha", "Appendix A Prompts (부록 A)"),
                para("pa", "평가 prompt는 Table A.5에 있다.")]
        html = appendix_html(True)
        self.run_cycle(monkeypatch, page, html)
        images = [(page[i - 1]["id"], b["picture"], b["image"]["caption"][0]["plain_text"])
                  for i, b in enumerate(page) if b["type"] == "image"]
        assert images == [
            ("p3", "picture-of-S3.T5", "Table 5: Main results."),
            ("pa", "picture-of-A1.T5", "Table A.5: Prompt used for evaluation.")]
        assert self.run_cycle(monkeypatch, page, html)["skipped_existing"]


class TestAContainerOfSeveralTables:
    """One LaTeXML id can hold two captioned tables; the old renderer pictured
    its largest one, so the image's caption cannot say which it shows."""

    HTML = section_html("S5", 5, "Setup", (
        '<figure id="S5.T8" class="ltx_table"><div class="ltx_flex_figure">'
        '<figure id="S5.T8.fig1" class="ltx_figure ltx_figure_panel">'
        '<figcaption class="ltx_caption">Table 7: Model sizes.</figcaption>'
        '<table><tr><td>N</td></tr></table></figure>'
        '<figure id="S5.T8.fig2" class="ltx_figure ltx_figure_panel">'
        '<figcaption class="ltx_caption">Table 8: Fixed settings.</figcaption>'
        '<table><tr><td>lr</td></tr></table></figure></div></figure>'))

    def test_its_image_keeps_its_caption_and_place(self):
        blocks = [heading("h5", "5 Setup"),
                  para("p1", "Table 8 lists the fixed settings and Table 7 the model sizes."),
                  img("i1", "Table 7: Model sizes."), para("p2", "More text.")]
        plan = et.plan_relocation(blocks, et.parse_tables(self.HTML), ME)
        assert plan["moves"] == [] and plan["recaption"] == []
        assert [x["block"] for x in plan["left"]] == ["i1"]
        assert plan["left"][0]["why"].startswith("its id holds several tables")


class TestEachTableOfAContainerIsItsOwn:
    """A new page pictures each table of a container by its own element, under
    its own caption, beside its own citation. One image per id showed the
    largest panel under the id's caption, cited where the OTHER table was."""

    PANELS = TestAContainerOfSeveralTables.HTML

    # The tables follow their captions in flex cells of their own.
    CAPTIONS_THEN_TABLES = section_html("S4", 4, "Results", (
        '<figure id="S4.T4" class="ltx_table"><div class="ltx_flex_figure">'
        '<figure id="S4.T4.fig1" class="ltx_figure ltx_figure_panel">'
        '<figcaption>Table 3: Tracking.</figcaption></figure>'
        '<figure id="S4.T4.fig2" class="ltx_figure ltx_figure_panel">'
        '<figcaption>Table 4: Memory.</figcaption></figure>'
        '<div class="ltx_flex_cell"><table id="S4.T4.2"><tr><td>a</td></tr></table></div>'
        '<div class="ltx_flex_cell"><table id="S4.T4.3"><tr><td>b</td></tr></table></div>'
        '</div></figure>'))

    # Each caption follows its table.
    TABLES_THEN_CAPTIONS = section_html("S4", 4, "Results", (
        '<figure id="S4.T5" class="ltx_table"><div class="ltx_flex_figure">'
        '<table id="S4.T5.3"><tr><td>a</td></tr></table></div>'
        '<figcaption>Table 4: Single models.</figcaption>'
        '<div class="ltx_flex_figure"><table id="S4.T5.12"><tr><td>b</td></tr></table></div>'
        '<figcaption>Table 5: Ensembles.</figcaption></figure>'))

    def parts(self, html):
        return [(t["id"], t["caption"], t.get("container")) for t in et.parse_tables(html)]

    def test_panels_with_their_own_captions(self):
        assert self.parts(self.PANELS) == [
            ("S5.T8.fig1", "Table 7: Model sizes.", "S5.T8"),
            ("S5.T8.fig2", "Table 8: Fixed settings.", "S5.T8")]

    def test_captions_then_tables(self):
        assert self.parts(self.CAPTIONS_THEN_TABLES) == [
            ("S4.T4.2", "Table 3: Tracking.", "S4.T4"),
            ("S4.T4.3", "Table 4: Memory.", "S4.T4")]

    def test_tables_then_captions(self):
        assert self.parts(self.TABLES_THEN_CAPTIONS) == [
            ("S4.T5.3", "Table 4: Single models.", "S4.T5"),
            ("S4.T5.12", "Table 5: Ensembles.", "S4.T5")]

    def test_a_part_is_not_parsed_twice(self):
        html = self.TABLES_THEN_CAPTIONS.replace('id="S4.T5.3"', 'id="S4.T4"')
        assert [t["id"] for t in et.parse_tables(html)] == ["S4.T4", "S4.T5.12"]

    def test_what_the_old_parser_wrote_for_the_id_still_names_it(self):
        # The old parser wrote one caption per id, a bare `Table 5` for a long
        # one; both parts carry it, and it still says the image is the id's.
        parts = [dict(table("S4.T5", "Table 4: Single models.", legacy=""),
                      id=pid, several_tables=True, container="S4.T5")
                 for pid in ("S4.T5.3", "S4.T5.12")]
        parts[1]["caption"] = "Table 5: Ensembles."
        blocks = [heading("h4", "4 Results (결과)"), para("p4", "Table 5에 있다."),
                  img("i5", "Table 5")]
        plan = et.plan_relocation(blocks, parts, ME)
        assert [(x["block"], x["why"].split(";")[0]) for x in plan["left"]] == [
            ("i5", "its id holds several tables")]

    def test_two_captions_and_one_table_stay_one_picture(self):
        # Which caption the table belongs to cannot be read off the markup.
        html = ('<figure id="A9.T2" class="ltx_table"><figcaption>Table 1: Robustness.'
                '</figcaption><table id="A9.T2.5"><tr><td>1</td></tr></table>'
                '<figcaption>Table 2: Arithmetic.</figcaption></figure>')
        tables = et.parse_tables(html)
        assert [(t["id"], t.get("several_tables"), t.get("container")) for t in tables] \
            == [("A9.T2", True, None)]

    def test_a_new_page_gets_each_one(self, monkeypatch):
        page = [heading("h5", "5 Setup (설정)"),
                para("p1", "모델 크기는 Table 7에 있다. " + "본문 " * 300),
                para("p2", "고정 설정은 Table 8에 있다. " + "본문 " * 300)]
        run = TestANewPageWithARestartedCounter().run_cycle
        run(monkeypatch, page, self.PANELS)
        images = [(page[i - 1]["id"], b["picture"], b["image"]["caption"][0]["plain_text"])
                  for i, b in enumerate(page) if b["type"] == "image"]
        assert images == [
            ("p1", "picture-of-S5.T8.fig1", "Table 7: Model sizes."),
            ("p2", "picture-of-S5.T8.fig2", "Table 8: Fixed settings.")]
        assert run(monkeypatch, page, self.PANELS)["skipped_existing"]


def _rendered_heights(html, tmp_path):
    """{table id: picture height} from the real renderer on a local file."""
    pytest.importorskip("playwright.sync_api")
    src = tmp_path / "src.html"
    src.write_text(html)
    try:
        images = et.render_tables("arxiv-id", et.parse_tables(html), str(tmp_path),
                                  "file://" + str(src))
    except Exception as e:   # no browser installed
        pytest.skip(f"no browser: {e}")
    heights = {}
    for im in images:
        with open(im["path"], "rb") as fh:
            heights[im["id"]] = int.from_bytes(fh.read(24)[20:24], "big")
    return heights


class TestTheRendererPicturesEachPart:
    """Runs the real renderer where a browser is installed: each part of a
    container is pictured by itself, so a tall table's picture is tall."""

    HTML = ('<html><body><section id="S5" class="ltx_section">'
            '<h2 class="ltx_title">5 Setup</h2>'
            '<figure id="S5.T8" class="ltx_table"><div class="ltx_flex_figure">'
            '<figure id="S5.T8.fig1" class="ltx_figure ltx_figure_panel">'
            '<figcaption class="ltx_caption">Table 7: Model sizes.</figcaption>'
            '<table border="1"><tr><td>' + "N" * 40 + '</td><td>d</td></tr>'
            + '<tr><td>1</td><td>2</td></tr>' * 20 + '</table></figure>'
            '<figure id="S5.T8.fig2" class="ltx_figure ltx_figure_panel">'
            '<figcaption class="ltx_caption">Table 8: Fixed settings.</figcaption>'
            '<table border="1"><tr><td>' + "l" * 40 + '</td><td>1e-3</td></tr>'
            '<tr><td>bs</td><td>64</td></tr></table></figure>'
            '</div></figure></section></body></html>')

    TALL = ('<table border="1" id="{}"><tr><td>' + "N" * 40 + '</td><td>d</td></tr>'
            + '<tr><td>1</td><td>2</td></tr>' * 20 + '</table>')
    SHORT = ('<table border="1" id="{}"><tr><td>' + "l" * 40 + '</td><td>1e-3</td></tr>'
             '<tr><td>bs</td><td>64</td></tr></table>')

    def page(self, figure):
        return ('<html><body><section id="S4" class="ltx_section">'
                f'<h2 class="ltx_title">4 Results</h2>{figure}</section></body></html>')

    def test_each_panel_by_itself(self, tmp_path):
        heights = _rendered_heights(self.HTML, tmp_path)
        assert set(heights) == {"S5.T8.fig1", "S5.T8.fig2"}
        assert heights["S5.T8.fig1"] > 3 * heights["S5.T8.fig2"]

    def test_each_table_under_a_row_of_captions(self, tmp_path):
        html = self.page(
            '<figure id="S4.T4" class="ltx_table"><div class="ltx_flex_figure">'
            '<div class="ltx_flex_cell"><figure id="S4.T4.fig1" class="ltx_figure '
            'ltx_figure_panel"><figcaption>Table 3: Tracking.</figcaption></figure></div>'
            '<div class="ltx_flex_cell"><figure id="S4.T4.fig2" class="ltx_figure '
            'ltx_figure_panel"><figcaption>Table 4: Memory.</figcaption></figure></div>'
            '<div class="ltx_flex_break"></div>'
            f'<div class="ltx_flex_cell">{self.SHORT.format("S4.T4.2")}</div>'
            '<div class="ltx_flex_break"></div>'
            f'<div class="ltx_flex_cell">{self.TALL.format("S4.T4.3")}</div>'
            '</div></figure>')
        heights = _rendered_heights(html, tmp_path)
        assert set(heights) == {"S4.T4.2", "S4.T4.3"}
        assert heights["S4.T4.3"] > 3 * heights["S4.T4.2"]

    def test_each_table_over_its_caption(self, tmp_path):
        html = self.page(
            '<figure id="S4.T5" class="ltx_table">'
            f'<div class="ltx_flex_figure"><div class="ltx_flex_cell">{self.SHORT.format("S4.T5.3")}'
            '</div></div><figcaption>Table 4: Single models.</figcaption>'
            f'<div class="ltx_flex_figure"><div class="ltx_flex_cell">{self.TALL.format("S4.T5.12")}'
            '</div></div><figcaption>Table 5: Ensembles.</figcaption></figure>')
        heights = _rendered_heights(html, tmp_path)
        assert set(heights) == {"S4.T5.3", "S4.T5.12"}
        assert heights["S4.T5.12"] > 3 * heights["S4.T5.3"]

    def test_one_that_cannot_be_split_is_pictured_whole(self, tmp_path):
        # Its captions go into the picture, so the one it is captioned with is
        # there whichever table it names.
        alone = self.page('<figure id="S4.T1" class="ltx_table">'
                          + self.SHORT.format("S4.T1.1") + '</figure>')
        whole = self.page('<figure id="S4.T2" class="ltx_table">'
                          '<figcaption>Table 1: Robustness.</figcaption>'
                          + self.SHORT.format("S4.T2.5")
                          + '<figcaption>Table 2: Arithmetic.</figcaption></figure>')
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        table_only = _rendered_heights(alone, tmp_path / "a")["S4.T1"]
        assert _rendered_heights(whole, tmp_path / "b")["S4.T2"] > table_only + 40


class TestACaptionThatAlreadyNamesItsTable:
    """A caption is rewritten only when it is not the table's: bare, cut short,
    or another table's. The source's own text can be noisier than what the
    page shows — the current render prints a formula and its TeX twice."""

    TEX = ('Table 1: Accuracy of every model on the held-out split, averaged over '
           'five seeds and three prompts; models reach <math alttext="75\\%"><semantics>'
           '<mn>75%</mn><annotation encoding="application/x-tex">75\\%</annotation>'
           '</semantics></math> accuracy.')
    CLEAN = ('Table 1: Accuracy of every model on the held-out split, averaged over '
             'five seeds and three prompts; models reach 75% accuracy.')

    def html(self):
        return section_html("S4", 4, "Results", table_html("S4.T1", self.TEX))

    def test_in_place_it_is_not_recaptioned(self):
        blocks = [heading("h4", "4 Results"), para("p4", "Table 1에 결과가 있다."),
                  img("i1", self.CLEAN)]
        assert et.plan_relocation(blocks, et.parse_tables(self.html()), ME)["recaption"] == []

    def test_a_move_keeps_it(self):
        blocks = [heading("h4", "4 Results"), img("i1", self.CLEAN),
                  para("p4", "Table 1에 결과가 있다.")]
        plan = et.plan_relocation(blocks, et.parse_tables(self.html()), ME)
        assert [it["caption"] for mv in plan["moves"] for it in mv["items"]] == [self.CLEAN]

    def test_a_caption_cut_short_is_completed(self):
        cut = ("Table 1: Accuracy of every model on the held-out split, averaged over "
               "five seeds and three")
        assert len(cut) > 80        # the caption key reads the first 80 characters
        source = [table("S4.T1", self.CLEAN)]
        blocks = [heading("h4", "4 Results"), para("p4", "Table 1에 결과가 있다."),
                  img("i1", cut)]
        assert [r["caption"] for r in et.plan_relocation(blocks, source, ME)["recaption"]] \
            == [self.CLEAN]


class TestAZeroWidthSpaceIsNoDifference:
    """The current render puts U+200B into the maths of a caption; no caption on
    a page carries one, so the key kept the image from being recognised."""

    def test_the_image_is_its_table(self):
        source = [table("S4.T4", "Table 4: F​1 score on the validation set.")]
        blocks = [heading("h1", "1 Introduction"), para("p0", "본문."),
                  img("i4", "Table 4: F1 score on the validation set."),
                  heading("h4", "4 Experiments"), para("p4", "Table 4에 결과를 보고한다.")]
        plan = et.plan_relocation(blocks, source, ME)
        assert moved(plan) == {"i4": "p4"}
        # ...and it keeps the caption it has: that one is the table's already.
        assert plan["moves"][0]["items"][0]["caption"] == \
            "Table 4: F1 score on the validation set."


class TestWhichPagesItRefuses:

    def test_a_page_the_owner_listed_in_heal_skip(self):
        assert et.relocation_refusal("ab-cd", {"properties": {}}, {"abcd"})

    def test_a_page_that_is_not_a_paper(self):
        lecture = {"properties": {"Venue": {"type": "select",
                                            "select": {"name": "Learn"}}}}
        assert et.relocation_refusal("ab-cd", lecture, set())

    def test_an_ordinary_paper_page(self):
        assert et.relocation_refusal("ab-cd", {"properties": {}}, set()) is None
