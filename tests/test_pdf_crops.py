"""What a figure rendered from a PDF contains.

A journal paper came through with its figures missing entirely, and rendering
them from the PDF showed three faults in the crop:

* The right edge was cut off. The crop was clamped 40 pt inside the page edge,
  an arXiv-shaped margin; a journal's text block runs to within about 32 pt of
  the edge, so a figure spanning the column lost its last box.
* The caption was inside the image. The image block already carries the caption
  as text, so it was shown twice — and a caption set in two columns under a
  full-width figure was cut down the middle.
* Every Extended Data figure and table was skipped, because only captions that
  open with `Fig.` / `Table` were recognised. Their numbers restart at 1, so
  they also need a key of their own or they would collide with the main ones.
"""
import fitz

import extract_pdf_media as pm

PAGE = (595.3, 790.9)


def pdf(tmp_path, pages):
    """A PDF with, per page, one drawn float and its caption — under a figure,
    over a table, the way journals set them."""
    doc = fitz.open()
    for caption, box in pages:
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.draw_rect(fitz.Rect(*box), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        below = "Table" not in caption
        page.insert_text((box[0], box[3] + 18 if below else box[1] - 10),
                         caption, fontsize=8)
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


def render(tmp_path, pages):
    return pm.render_media(pdf(tmp_path, pages), str(tmp_path / "out"))


class TestTheCropKeepsTheWholeFigure:

    def test_a_figure_reaching_the_text_edge_is_not_cut(self, tmp_path):
        media = render(tmp_path, [
            ("Fig. 1 | A wide figure.", (40, 60, 562, 250))])
        assert media[("figure", 1)]["box"][2] >= 562

    def test_a_drawing_past_the_page_edge_does_not_widen_it(self, tmp_path):
        path = pdf(tmp_path, [("Fig. 1 | A figure over a bleed.", (100, 60, 480, 250))])
        doc = fitz.open(path)
        doc[0].draw_rect(fitz.Rect(-60, -40, 765, 250), color=(0.9, 0.9, 0.9))
        doc.save(path, incremental=True, encryption=0)
        box = pm.render_media(path, str(tmp_path / "out"))[("figure", 1)]["box"]
        assert box[0] >= 90 and box[2] <= 490

    def test_the_caption_is_not_part_of_the_image(self, tmp_path):
        media = render(tmp_path, [
            ("Fig. 1 | A figure with its caption below.", (60, 60, 500, 250))])
        # The caption's baseline is 18 pt under the figure; its top is above it.
        assert media[("figure", 1)]["box"][3] < 250 + 10


class TestExtendedData:

    def test_an_extended_data_figure_is_rendered(self, tmp_path):
        media = render(tmp_path, [
            ("Extended Data Fig. 3 | A supplementary figure.", (60, 60, 500, 250))])
        assert ("ed_figure", 3) in media

    def test_it_does_not_collide_with_the_main_figure_of_that_number(self, tmp_path):
        media = render(tmp_path, [
            ("Fig. 1 | The main figure.", (60, 60, 500, 250)),
            ("Extended Data Fig. 1 | The extended one.", (60, 60, 500, 300))])
        assert ("figure", 1) in media and ("ed_figure", 1) in media
        assert media[("figure", 1)]["page"] == 1
        assert media[("ed_figure", 1)]["page"] == 2

    def test_its_caption_is_kept_whole(self, tmp_path):
        media = render(tmp_path, [
            ("Extended Data Table 2 | Detailed results.", (60, 60, 500, 250))])
        assert media[("ed_table", 2)]["caption"].startswith("Extended Data Table 2")


def block(text):
    return {"id": text, "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "plain_text": text,
                                         "text": {"content": text}}]}}


class TestWhereAnExtendedDataFigureGoes:

    def test_a_main_figure_is_not_anchored_on_an_extended_data_mention(self):
        blocks = [block("자세한 결과는 Extended Data Fig. 3에 있습니다."),
                  block("Fig. 3은 학습 과정을 보여줍니다.")]
        assert pm._anchor_for("figure", 3, blocks) == blocks[1]["id"]

    def test_an_extended_data_figure_is_anchored_on_its_own_mention(self):
        blocks = [block("Fig. 3은 학습 과정을 보여줍니다."),
                  block("자세한 결과는 Extended Data Fig. 3에 있습니다.")]
        assert pm._anchor_for("ed_figure", 3, blocks) == blocks[1]["id"]

    def test_a_range_names_every_figure_in_it(self):
        blocks = [block("증명은 Extended Data Figs. 7–9에 있습니다.")]
        assert pm._anchor_for("ed_figure", 8, blocks) == blocks[0]["id"]

    def test_an_extended_data_table_is_not_a_figure(self):
        blocks = [block("Extended Data Table 1을 보세요.")]
        assert pm._anchor_for("ed_figure", 1, blocks) is None
        assert pm._anchor_for("ed_table", 1, blocks) == blocks[0]["id"]


def test_the_container_and_ci_read_pdfs_with_the_same_pymupdf():
    # The crop depends on the library's text and drawing extraction, so a
    # version skew between the image, CI and the host healer is a different
    # crop. Both pins live here: tests/../container/Dockerfile and
    # requirements-dev.txt.
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    pin = re.compile(r"pymupdf==([\d.]+)", re.I)
    image = pin.findall((root / "container" / "Dockerfile").read_text())
    ci = pin.findall((root / "requirements-dev.txt").read_text())
    assert image and image == ci
