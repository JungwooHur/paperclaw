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
            {"num": t["num"], "caption": t["caption"], "path": "x.png"}
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


class TestWhichPagesItRefuses:

    def test_a_page_the_owner_listed_in_heal_skip(self):
        assert et.relocation_refusal("ab-cd", {"properties": {}}, {"abcd"})

    def test_a_page_that_is_not_a_paper(self):
        lecture = {"properties": {"Venue": {"type": "select",
                                            "select": {"name": "Learn"}}}}
        assert et.relocation_refusal("ab-cd", lecture, set())

    def test_an_ordinary_paper_page(self):
        assert et.relocation_refusal("ab-cd", {"properties": {}}, set()) is None
