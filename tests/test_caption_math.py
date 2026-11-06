"""A formula in arXiv's HTML is written out more than once; its text is read once.

LaTeXML renders a formula as presentation MathML, which is what a reader sees,
and attaches the same formula again for machines: its TeX in
`<annotation encoding="application/x-tex">` and, in some renders, content MathML
in `<annotation-xml>`. Stripping only the tags kept every copy, so a caption
reached the page as `5×55\\times 5` and a source span was measured with each
formula counted two or three times. The snippets here are made up; only the
markup has the shape of a real render.
"""
import pytest

import extract_paper_figures as ef
import extract_paper_tables as et
import link_references as lr
import verify_sections as vs


def formula(presentation, tex, content=""):
    """One inline LaTeXML formula: rendered form, optional content MathML, TeX."""
    xml = (f'<annotation-xml encoding="MathML-Content">{content}</annotation-xml>'
           if content else "")
    return (f'<math alttext="{tex}" class="ltx_Math" display="inline"><semantics>'
            f'<mrow>{presentation}</mrow>{xml}'
            f'<annotation encoding="application/x-tex">{tex}</annotation>'
            '</semantics></math>')


GRID = formula("<mn>5</mn><mo>×</mo><mn>5</mn>", r"5\times 5")
START = formula("<msub><mi>x</mi><mn>0</mn></msub><mo>=</mo><mn>0</mn>", "x_{0}=0",
                content='<apply><eq/><apply><csymbol cd="ambiguous">subscript'
                        '</csymbol><ci>x</ci><cn type="integer">0</cn></apply>'
                        '<cn type="integer">0</cn></apply>')
BOUND = formula("<mi>p</mi><mo>&lt;</mo><mn>0.05</mn>", "p&lt;0.05")

CAPTION = f"Figure 2: Rollouts on a {GRID} grid from {START}, all at {BOUND}."
CLEAN = "Figure 2: Rollouts on a 5×5 grid from x0=0, all at p<0.05."


class TestACaptionReadsEachFormulaOnce:

    def test_the_rendered_formula_is_kept_and_its_copies_dropped(self):
        assert ef._clean(CAPTION) == CLEAN

    def test_the_words_between_two_formulas_survive(self):
        # `<annotation-xml` opens like `<annotation`. Read as one, it would run
        # to the NEXT formula's `</annotation>` and take the words between.
        only_xml = ('<math><semantics><mi>a</mi><annotation-xml encoding='
                    '"MathML-Content"><ci>a</ci></annotation-xml></semantics></math>')
        assert ef._clean(f"{only_xml} and then {GRID}") == "a and then 5×5"

    def test_a_figure_is_captioned_with_it(self):
        html = (f'<figure id="S1.F2" class="ltx_figure"><img src="x2.png">'
                f'<figcaption class="ltx_caption">{CAPTION}</figcaption>\n</figure>')
        figs = ef.parse_figures(html, "https://render.example/html/paper/")
        assert [f["caption"] for f in figs] == [CLEAN]

    def test_a_table_is_captioned_with_it(self):
        html = (f'<figure id="S4.T1" class="ltx_table"><figcaption class="ltx_caption">'
                f'Table 1: Success on a {GRID} grid.</figcaption>'
                '<table><tr><td>1</td></tr></table></figure>')
        assert [t["caption"] for t in et.parse_tables(html)] == [
            "Table 1: Success on a 5×5 grid."]

    def test_a_section_title_reads_it_once(self):
        html = ('<section id="S3" class="ltx_section"><h2 class="ltx_title">'
                '<span class="ltx_tag ltx_tag_section">3 </span>'
                f'Scaling to a {GRID} grid</h2><p>Body.</p></section>')
        assert [s["title"] for s in ef.source_sections(html)] == [
            "Scaling to a 5×5 grid"]

    def test_a_reference_entry_is_written_with_it(self):
        # The injected reference list is the source's entry text, verbatim.
        html = ('<ul class="ltx_biblist"><li id="bib.bib1" class="ltx_bibitem">'
                '<span class="ltx_tag ltx_tag_bibitem">[1]</span>'
                f'<span class="ltx_bibblock">A. Author. Planning on a {GRID} grid.'
                ' In Venue, 2020.</span></li></ul>')
        assert [e["text"] for e in lr.parse_bibliography(html)] == [
            "A. Author. Planning on a 5×5 grid. In Venue, 2020."]


class TestTheAuditReadsAFormulaAsThePageStoresIt:
    """`verify_sections` compares a section's length against its source span,
    and the page side counts a formula as its TeX (an equation block's
    expression, an inline equation's text). So the source side reads each
    formula once, as its TeX: every extra copy made a maths-heavy section look
    shorter than it is, and the rendered form alone is a different length."""

    def test_a_formula_is_counted_once_as_its_tex(self):
        html = ('<section id="S2"><p class="ltx_p">We start from '
                f'{START} on a {GRID} grid, at {BOUND}.</p></section>')
        text = " ".join(vs.source_text_from_html(html).split())
        assert text == r"We start from x_{0}=0 on a 5\times 5 grid, at p<0.05."

    def test_a_comparison_in_tex_is_not_read_as_a_tag(self):
        # The TeX stays entity-encoded until the one unescape that follows the
        # tag strip: decoded first, `<q and r>` reads as a tag and goes.
        less = formula("<mi>p</mi><mo>&lt;</mo><mi>q</mi>", "p&lt;q")
        more = formula("<mi>r</mi><mo>&gt;</mo><mi>s</mi>", "r&gt;s")
        html = f'<p class="ltx_p">Keep {less} and {more} words.</p>'
        text = vs.source_text_from_html(html)
        assert " ".join(text.split()) == "Keep p<q and r>s words."

    def test_a_formula_without_its_tex_is_read_once_as_rendered(self):
        bare = ('<math display="inline"><semantics><mi>a</mi><annotation-xml '
                'encoding="MathML-Content"><ci>a</ci></annotation-xml></semantics></math>')
        text = vs.source_text_from_html(f'<p class="ltx_p">Set {bare} first.</p>')
        assert " ".join(text.split()) == "Set a first."

    def test_a_section_whose_heading_carries_tex_is_located(self):
        # A page heading keeps a formula as TeX, and the locator looks for the
        # heading's words in the source text.
        policy = formula("<msub><mi>π</mi><mi>θ</mi></msub>", r"\pi_{\theta}")
        prose = ("The policy is trained on the demonstrations collected above "
                 "and evaluated on every task of the benchmark. ") * 6

        def section(n, title):
            return (f'<section id="S{n}" class="ltx_section">'
                    f'<h2 class="ltx_title">{n} {title}</h2>'
                    f'<p class="ltx_p">{prose}</p></section>')

        html = (section(1, "Introduction")
                + section(2, f"Fine-tuning {policy} on demonstrations")
                + section(3, "Conclusion"))
        heading = r"2 Fine-tuning \pi_{\theta} on demonstrations (2 시연으로 미세조정)"
        sections = [("1", "Introduction"),
                    (vs.section_key(heading), vs.english_title(heading)),
                    ("3", "Conclusion")]
        chars = vs.source_section_chars(vs.source_text_from_html(html), sections)
        assert chars["2"] is not None


ME = "integration-user"
BODY = [{"id": f"p{i}", "type": "paragraph",
         "paragraph": {"rich_text": [{"plain_text": "본문 문장입니다. " * 20}]}}
        for i in range(8)]


def para(bid, text):
    return {"id": bid, "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def heading(bid, text):
    return {"id": bid, "type": "heading_1",
            "heading_1": {"rich_text": [{"plain_text": text}]}}


def img(bid, caption):
    """An image this integration placed and nobody has edited since."""
    return {"id": bid, "type": "image",
            "created_by": {"id": ME}, "last_edited_by": {"id": ME},
            "image": {"type": "file",
                      "file": {"url": f"https://files.example/{bid}.png"},
                      "caption": [{"plain_text": caption}]}}


# A composite table's first panel caption, as the source writes it, and the
# caption the pre-fix window parser wrote for that table's image from it.
PANEL = f"(a) Rollouts on a {GRID} grid."
PANEL_DOUBLED = "Table 1: (a) Rollouts on a 5×55\\times 5 grid."
ROWS = "".join(f"<tr><td>row {i} {'x' * 80}</td></tr>" for i in range(120))


def table_html(caption, fid="S4.T1", shape="plain"):
    """Table `fid` captioned `caption`, in a shape the pre-fix window parser
    read its own way: `plain`, the table's own caption; `long`, whose caption
    sits past the 8,000 characters that parser read, nothing; `composite`,
    whose panel caption comes first, the panel's (`PANEL`)."""
    cap = f'<figcaption class="ltx_caption">{caption}</figcaption>'
    if shape == "long":
        return f'<figure id="{fid}" class="ltx_table"><table>{ROWS}</table>{cap}</figure>'
    if shape == "composite":
        return (f'<figure id="{fid}" class="ltx_table">'
                f'<figure id="{fid}.sf1" class="ltx_figure ltx_figure_panel">'
                f'<figcaption>{PANEL}</figcaption>'
                f'<table><tr><td>1</td></tr></table></figure>{cap}</figure>')
    return (f'<figure id="{fid}" class="ltx_table">{cap}'
            '<table><tr><td>1</td></tr></table></figure>')


# A table caption whose formula falls inside the 80 characters the caption key
# reads, and one whose formula falls past them; each as the source writes it,
# as the injector writes it now, and as a page placed before the fix shows it.
NEAR = f"Table 1: Success on a {GRID} grid."
NEAR_CLEAN = "Table 1: Success on a 5×5 grid."
NEAR_DOUBLED = "Table 1: Success on a 5×55\\times 5 grid."
LONG = ("Table 1: Success of every planner on the held-out maps, averaged over "
        "three seeds and two horizons, on a ")
FAR = f"{LONG}{GRID} grid."
FAR_DOUBLED = f"{LONG}5×55\\times 5 grid."

# Each shape with an image its table had before the fix. In a long or composite
# table the window parser read some other caption, so only the table's own
# caption read the old way (`doubled_caption`) names the first two; the last
# was written by the window parser, from the panel caption read the old way.
PLACED = [("plain", NEAR_DOUBLED), ("long", NEAR_DOUBLED),
          ("composite", NEAR_DOUBLED), ("composite", PANEL_DOUBLED)]
PLACED_IDS = ["plain", "long", "composite", "composite-by-the-window-parser"]


class TestAPagePlacedBeforeTheFix:
    """Pages placed earlier carry the doubled caption, and the fix changes the
    source caption it is compared with. Every tool that tells which table an
    image shows must still recognise those images."""

    def test_its_tables_still_read_as_placed(self, monkeypatch):
        # Otherwise every such page gets a second copy of the table.
        page = BODY + [img("i1", NEAR_DOUBLED)]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: page)
        monkeypatch.setattr(ef, "fetch_html", lambda _a: (table_html(NEAR), "stub"))
        rep = et.inject_tables("page", "arxiv-id", apply=False)
        assert rep.get("skipped_existing") is True
        assert "would_place" not in rep

    @pytest.mark.parametrize("shape, read", [
        ("plain", NEAR_DOUBLED), ("long", ""),
        ("composite", "(a) Rollouts on a 5×55\\times 5 grid.")],
        ids=["plain", "long", "composite"])
    def test_the_window_parser_read_each_shape_its_own_way(self, shape, read):
        # What the cases below rest on.
        table = et.parse_tables(table_html(NEAR, shape=shape))[0]
        assert (table["legacy_caption"], table["doubled_caption"]) == (
            read, NEAR_DOUBLED)

    @pytest.mark.parametrize("shape, on_page", PLACED, ids=PLACED_IDS)
    def test_a_forced_run_replaces_its_image(self, monkeypatch, shape, on_page):
        # Matched on the new caption only, the old image stayed beside its
        # replacement, and no caption repeats for the dedup to find.
        page = BODY + [img("i1", on_page)]
        html = table_html(NEAR, shape=shape)
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: page)
        monkeypatch.setattr(ef, "fetch_html", lambda _a: (html, "stub"))
        rep = et.inject_tables("page", "arxiv-id", apply=False, force=True)
        assert rep["would_replace"] == 1

    def test_a_table_split_out_of_a_shared_id_is_matched_by_its_own(self):
        # Two tables under one id are parsed apart; each keeps the caption it
        # was placed with, not the one the id's first table had.
        html = ('<figure id="S2.T7" class="ltx_table">'
                '<figure id="S2.T7.1" class="ltx_figure ltx_figure_panel">'
                f'<figcaption>Table 7: Success on a {GRID} grid.</figcaption>'
                '<table><tr><td>1</td></tr></table></figure>'
                '<figure id="S2.T7.2" class="ltx_figure ltx_figure_panel">'
                f'<figcaption>Table 8: Significant at {BOUND}.</figcaption>'
                '<table><tr><td>2</td></tr></table></figure></figure>')
        tables = et.parse_tables(html)
        assert [t["id"] for t in tables] == ["S2.T7.1", "S2.T7.2"]
        page = [img("i7", "Table 7: Success on a 5×55\\times 5 grid."),
                img("i8", "Table 8: Significant at p<0.05p<0.05.")]
        assert et.replaced_by(page, et.forced_keys(tables[1:], tables)) == ["i8"]

    def test_a_caption_the_table_before_borrowed_stays_the_owners(self):
        # T1 has no caption of its own, so the window parser read T2's, its
        # formula doubled as every formula was read then: the very text T2's
        # images placed before the fix carry. Counted as T1's as well, that key
        # named two tables and was dropped, and T2's image matched nothing.
        html = ('<figure id="S4.T1" class="ltx_table"><table><tr><td>1</td></tr>'
                '</table></figure>'
                + table_html(f"Table 2: Success on a {GRID} grid.", fid="S4.T2"))
        tables = et.parse_tables(html)
        doubled = "Table 2: Success on a 5×55\\times 5 grid."
        assert tables[0]["legacy_caption"] == doubled
        legacy = et._legacy_keys(tables)
        assert et._which_table(doubled, tables, legacy)["id"] == "S4.T2"
        page = [img("i2", doubled)]
        assert et.replaced_by(page, et.forced_keys(tables[1:], tables)) == ["i2"]
        assert et.replaced_by(page, et.forced_keys(tables[:1], tables)) == []

    @pytest.mark.parametrize("shape, source, on_page, written", [
        *[(shape, NEAR, on_page, NEAR_CLEAN) for shape, on_page in PLACED],
        # Past the key, the caption is one the key already identifies, and
        # `plan_relocation` keeps such a caption as it is.
        ("plain", FAR, FAR_DOUBLED, FAR_DOUBLED),
    ], ids=[f"inside-the-key-{i}" for i in PLACED_IDS] + ["past-the-key"])
    def test_relocate_still_moves_its_image(self, shape, source, on_page, written):
        assert (on_page.index("5×5") < 80) == (source is NEAR)
        blocks = [heading("h1", "1 Introduction (서론)"),
                  para("p1", "결과는 Table 1에 정리했다."),
                  para("p1b", "다음 문단."),
                  heading("h2", "2 Conclusion (결론)"),
                  para("p2", "끝."),
                  img("i1", on_page)]
        tables = et.parse_tables(table_html(source, shape=shape))
        plan = et.plan_relocation(blocks, tables, ME)
        assert plan["left"] == []
        assert [(m["after"], it["old"], it["caption"])
                for m in plan["moves"] for it in m["items"]] == [("p1", "i1", written)]


class TestAPanelCaptionPlacedBeforeTheFix:
    """`--recaption` finds the figure a panel caption on the page belongs to by
    looking for that caption in each figure's markup. A panel caption placed
    before the fix carries its formulas doubled, as the source read then."""

    @pytest.mark.parametrize("on_page", [
        "(a) Rollouts on a 5×55\\times 5 grid.", "(a) Rollouts on a 5×5 grid."],
        ids=["doubled", "clean"])
    def test_it_is_still_traced_to_its_figure(self, monkeypatch, on_page):
        # Read only the old way, the markup no longer holds a clean caption.
        html = ('<figure id="S2.F2" class="ltx_figure">'
                '<figure id="S2.F2.sf1" class="ltx_figure ltx_figure_panel">'
                '<img src="x2a.png">'
                f'<figcaption>(a) Rollouts on a {GRID} grid.</figcaption></figure>'
                f'<figcaption class="ltx_caption">Figure 2: Rollouts from {START}.'
                '</figcaption></figure>')
        page = [{"id": "i1", "type": "image", "image": {"caption": [
            {"plain_text": on_page}]}}]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: page)
        monkeypatch.setattr(ef, "fetch_html",
                            lambda _a: (html, "https://render.example/html/paper/"))
        rep = ef.recaption_page("page", "arxiv-id", apply=False)
        assert (rep["recaptioned"], rep["unmatched"]) == (1, [])


class TestNothingRepairsThemOnItsOwn:
    """A caption or reference entry already doubled on a page stays doubled:
    the healers leave a page alone once its figures, tables or list are
    there. (The table injector's case is `test_its_tables_still_read_as_placed`.)
    Repairing them is a decision about pages a person may have arranged."""

    def no_writes(self, monkeypatch):
        import translate_fulltext

        def refuse(*_a, **_k):
            raise AssertionError("wrote to Notion")
        monkeypatch.setattr(translate_fulltext, "notion", refuse)
        monkeypatch.setattr(lr, "notion", refuse)

    def test_the_figure_healer_leaves_its_figures_alone(self, monkeypatch):
        self.no_writes(monkeypatch)
        page = BODY + [img("f2", "Figure 2: Rollouts on a 5×55\\times 5 grid.")]
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _p: "arxiv-id")
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: page)
        assert ef.heal_figures("page", apply=True)["skipped_existing"] is True

    def test_the_reference_healer_leaves_its_list_alone(self, monkeypatch):
        import datetime

        self.no_writes(monkeypatch)
        page = BODY + [heading("refs", "References")] + [
            para(f"r{n}", f"[{n}] A. Author. Planning on a 5×55\\times 5 grid, "
                          f"part {n}. In Venue, 2020.") for n in range(1, 4)]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _p: page)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rep = lr.heal_references("page", apply=True, created_time=now)
        assert rep["refs"] == "already present"
