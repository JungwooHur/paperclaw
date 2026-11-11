"""Figures and tables whose printed label carries a letter, rendered from a PDF.

Appendix and supplementary floats print `Figure S2`, `Figure B.1`, `Table A.1`
or `Figure A1`. The PDF path read only `Figure <digits>`, so none of them was
rendered, and the injector keyed every float on an integer. The label is read by
the same rule the HTML injectors use, and placed by the same mention rule.
"""
import fitz

import extract_paper_figures as ef
import extract_pdf_media as pm
import verify_sections as vs

PAGE = (612, 792)
BOX = (60, 60, 500, 250)


def pdf(tmp_path, pages):
    """Per page, a list of (caption, box): a drawn float with its caption under a
    figure and over a table."""
    doc = fitz.open()
    for floats in pages:
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        for caption, box in floats:
            page.draw_rect(fitz.Rect(*box), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
            below = "Table" not in caption
            page.insert_text((box[0], box[3] + 18 if below else box[1] - 10),
                             caption, fontsize=8)
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


def render(tmp_path, pages, **kw):
    return pm.render_media(pdf(tmp_path, pages), str(tmp_path / "out"), **kw)


class TestLetteredLabelsAreRendered:

    def test_a_supplementary_figure(self, tmp_path):
        media = render(tmp_path, [[("Figure S2: A supplementary figure.", BOX)]])
        assert ("figure", "S2") in media

    def test_an_appendix_figure_with_a_dotted_label(self, tmp_path):
        media = render(tmp_path, [[("Figure B.1. An appendix figure.", BOX)]])
        assert ("figure", "B.1") in media

    def test_an_appendix_table(self, tmp_path):
        media = render(tmp_path, [[("Table A.1: An appendix table.", BOX)]])
        assert ("table", "A.1") in media

    def test_an_appendix_figure_without_a_dot(self, tmp_path):
        media = render(tmp_path, [[("Figure A1: An appendix figure.", BOX)]])
        assert ("figure", "A1") in media

    def test_the_crop_is_the_float(self, tmp_path):
        media = render(tmp_path, [[("Figure S2: A supplementary figure.", BOX)]])
        box = media[("figure", "S2")]["box"]
        assert box[0] <= 60 and box[1] <= 60 and box[2] >= 500 and box[3] >= 250


class TestTheCaptionStylesNumbersAccept:
    """A lettered, dotted or roman label is read in every style a number is."""

    def test_a_pipe_after_the_label(self, tmp_path):
        media = render(tmp_path, [[("Figure A1 | Grid over cycle length.", BOX)]])
        assert ("figure", "A1") in media

    def test_no_separator(self, tmp_path):
        media = render(tmp_path, [[("Figure S3 Caption that runs straight on.", BOX)]])
        assert ("figure", "S3") in media

    def test_upper_case(self, tmp_path):
        media = render(tmp_path, [[("FIGURE S4. An upper-case caption.", BOX)]])
        assert ("figure", "S4") in media

    def test_a_dotted_number(self, tmp_path):
        media = render(tmp_path, [[("Figure 3.1: A chapter-numbered figure.", BOX)]])
        assert ("figure", "3.1") in media

    def test_a_roman_table_number(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.insert_text((250, 60), "TABLE IV", fontsize=8)
        page.insert_text((200, 72), "RESULTS ON THE REAL ROBOT", fontsize=8)
        page.draw_rect(fitz.Rect(*BOX[:1], 80, *BOX[2:]), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        path = tmp_path / "paper.pdf"
        doc.save(path)
        media = pm.render_media(str(path), str(tmp_path / "out"))
        assert ("table", 4) in media


class TestASentenceIsNotACaption:
    """A body paragraph that opens with a float label is a mention of it."""

    def test_an_earlier_mention_does_not_take_the_label(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.insert_textbox(fitz.Rect(60, 400, 500, 600),
                            "Table II examines the performance of the method on "
                            "the real robot across every setting we tried.",
                            fontsize=10)
        page.draw_rect(fitz.Rect(60, 610, 500, 700), color=(0, 0, 0))
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.insert_text((250, 60), "TABLE II", fontsize=8)
        page.draw_rect(fitz.Rect(60, 80, 500, 250), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        path = tmp_path / "paper.pdf"
        doc.save(path)
        media = pm.render_media(str(path), str(tmp_path / "out"))
        assert media[("table", 2)]["page"] == 2

    def test_a_mention_above_the_caption_on_its_page(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.insert_textbox(fitz.Rect(60, 60, 300, 300),
                            "Figure 6 shows these results for both data sets, "
                            "and the variance across annotators is small. "
                            + "Every line of this paragraph runs the full measure "
                            "of its column, as body prose does. " * 4,
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        page.draw_rect(fitz.Rect(320, 60, 520, 250), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((320, 268), "Figure 6: Variance across annotators.",
                         fontsize=8)
        path = tmp_path / "paper.pdf"
        doc.save(path)
        box = pm.render_media(str(path), str(tmp_path / "out"))[("figure", 6)]["box"]
        assert box[0] >= 300 and box[1] <= 60


class TestNumberedLabelsAreUnchanged:

    def test_a_numbered_figure_keeps_an_integer_key(self, tmp_path):
        media = render(tmp_path, [[("Figure 1: A body figure.", BOX)]])
        assert list(media) == [("figure", 1)]

    def test_a_lettered_label_is_not_the_number_it_ends_in(self, tmp_path):
        media = render(tmp_path, [
            [("Figure 2: A body figure.", (60, 60, 500, 250))],
            [("Figure B.2: An appendix figure.", (60, 60, 500, 300))]])
        assert media[("figure", 2)]["page"] == 1
        assert media[("figure", "B.2")]["page"] == 2

    def test_only_selects_by_printed_label(self, tmp_path):
        pages = [[("Figure 2: A body figure.", (60, 60, 500, 250))],
                 [("Figure S2: A supplementary figure.", (60, 60, 500, 300))]]
        assert list(render(tmp_path, pages, only={"S2"})) == [("figure", "S2")]
        assert list(render(tmp_path, pages, only={2})) == [("figure", 2)]


class TestAnotherLetteredCaptionIsABoundary:

    def test_a_figure_stacked_under_another_does_not_swallow_it(self, tmp_path):
        media = render(tmp_path, [[
            ("Figure S1: The upper figure.", (60, 60, 500, 200)),
            ("Figure S2: The lower figure.", (60, 240, 500, 400))]])
        assert media[("figure", "S2")]["box"][1] > 218


def para(bid, text, kind="paragraph"):
    return {"id": bid, "type": kind, kind: {"rich_text": [{"plain_text": text}]}}


def image(bid, caption):
    return {"id": bid, "type": "image",
            "image": {"caption": [{"plain_text": caption}]}}


class TestPlacement:

    BLOCKS = [
        para("intro", "그림 2에서 보듯 학습이 안정적이다."),
        para("supp", "자세한 결과는 Figure S2에 있다."),
        para("tab", "Table A.1에 하이퍼파라미터를 정리했다."),
        para("app-a", "Appendix A Training details", "heading_1"),
        para("app-b", "Appendix B More results", "heading_1"),
    ]

    def test_a_lettered_figure_goes_after_its_first_mention(self):
        assert pm._anchor_for("figure", "S2", self.BLOCKS) == "supp"

    def test_a_lettered_table_goes_after_its_first_mention(self):
        assert pm._anchor_for("table", "A.1", self.BLOCKS) == "tab"

    def test_an_unmentioned_one_goes_under_its_appendix(self):
        assert pm._anchor_for("figure", "B.1", self.BLOCKS) == "app-b"
        assert pm._anchor_for("figure", "A3", self.BLOCKS) == "app-a"

    def test_an_unmentioned_number_still_has_no_anchor(self):
        assert pm._anchor_for("figure", 4, self.BLOCKS) is None

    def test_placed_lettered_figures_are_found(self):
        blocks = [image("i1", "Figure 2: body"), image("i2", "Figure S1: supplementary")]
        assert pm._placed_figures(blocks) == {2: 0, "S1": 1}

    def test_an_unmentioned_one_joins_its_own_series_on_the_page(self):
        blocks = self.BLOCKS + [image("i2", "Figure 2: body"),
                                image("s1", "Figure S1: supplementary")]
        placed = pm.placements([("figure", "S5"), ("figure", 9)], blocks)
        assert placed[("figure", "S5")] == "s1"
        assert placed[("figure", 9)] == "i2"


class TestFillingOnlyWhatIsMissing:

    def test_a_lettered_figure_already_on_the_page_is_not_added_again(self, monkeypatch):
        blocks = [para("p", "본문 " * 600), image("s2", "Figure S2: supplementary")]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _: blocks)

        def no_pdf(*_):
            raise AssertionError("fetched the PDF for a figure already placed")
        monkeypatch.setattr(pm, "fetch_pdf", no_pdf)
        rep = pm.inject("p", "paper.pdf", apply=False, kinds=("figure",), only={"S2"})
        assert rep.get("skipped_existing")

    def test_the_numbered_check_is_unchanged(self, monkeypatch):
        blocks = [para("p", "본문 " * 600), image("f2", "Figure 2 Results")]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _: blocks)
        monkeypatch.setattr(pm, "fetch_pdf", lambda *_: (_ for _ in ()).throw(
            AssertionError("fetched the PDF for a figure already placed")))
        rep = pm.inject("p", "paper.pdf", apply=False, kinds=("figure",), only={2})
        assert rep.get("skipped_existing")


class TestOrderingMixedLabels:

    def test_numbered_floats_come_before_each_lettered_series(self):
        keys = [("figure", "S10"), ("figure", "S2"), ("figure", 10),
                ("figure", "A1"), ("figure", 2), ("table", 1), ("table", "A.1")]
        assert sorted(keys, key=pm._float_order) == [
            ("figure", 2), ("figure", 10), ("figure", "A1"), ("figure", "S2"),
            ("figure", "S10"), ("table", 1), ("table", "A.1")]

    def test_a_dry_run_plans_each_float_in_that_order(self, monkeypatch, tmp_path):
        blocks = [para("p", "본문 " * 600), para("m", "Figure S2와 그림 2를 보라.")]
        monkeypatch.setattr(vs, "fetch_blocks", lambda _: blocks)
        monkeypatch.setattr(pm, "fetch_pdf", lambda *_: "paper.pdf")
        media = {key: {"path": "x.png", "page": 1, "caption": "c", "box": [0, 0, 1, 1]}
                 for key in (("figure", "S2"), ("table", "A.1"), ("figure", 2))}
        monkeypatch.setattr(pm, "render_media", lambda *a, **k: dict(media))
        rep = pm.inject("p", "paper.pdf", apply=False, keep_text=True)
        assert [(p["kind"], p["label"]) for p in rep["planned"]] == [
            ("figure", 2), ("figure", "S2"), ("table", "A.1")]


def test_the_label_rule_is_the_html_injectors():
    caption = "Figure B.1. An appendix figure."
    assert pm.caption_kind(caption) == ("figure", ef.figure_label("", caption))
