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


def test_the_label_rule_is_the_html_injectors():
    caption = "Figure B.1. An appendix figure."
    assert pm.caption_kind(caption) == ("figure", ef.figure_label("", caption))
