"""Where a figure rendered from a PDF ends, on the layouts papers actually use.

The crop walks away from the caption until body text stops it. Two layouts
stopped it at something that was not this figure's body text:

* A two-column page. Prose in the OTHER column ends just above a right-column
  caption, and the walk stopped there, so the figure was never rendered.
* A page of plots. Body size was taken from that page alone, where tick labels
  are most of the text, so a one-line axis title above the caption read as a
  heading and the walk stopped before it took a single step.
"""
import fitz
import pytest

import extract_pdf_media as pm

LETTER = (612, 792)
LEFT = (54, 290)
RIGHT = (306, 542)
PROSE = ("Body prose fills the column with justified lines of ordinary words so "
         "that every line runs the full measure of the column except the last "
         "line of the paragraph. ") * 3


def _paragraph(page, x0, x1, top, size=10):
    page.insert_textbox(fitz.Rect(x0, top, x1, top + 400), PROSE, fontsize=size,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    return max((b["bbox"] for b in page.get_text("dict")["blocks"]
                if b["bbox"][0] == x0), key=lambda box: box[1])


def prose(page, x0, x1, bottom, size=10):
    """A justified paragraph whose block ends at `bottom`; returns its box."""
    scratch = fitz.open().new_page(width=page.rect.width, height=page.rect.height)
    probe = _paragraph(scratch, x0, x1, 0, size)
    box = _paragraph(page, x0, x1, bottom - probe[3], size)
    assert abs(box[3] - bottom) < 0.5, box
    return box


def two_column_page(doc, figure_x, caption_x, caption):
    """Prose in both columns; one figure with its caption at y 270.

    The left column's prose ends 11 pt above the caption, as it does when a
    figure sits at the top of the right column.
    """
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_textbox(fitz.Rect(LEFT[0], 113, LEFT[1], 300), PROSE,
                        fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
    page.insert_textbox(fitz.Rect(LEFT[0], 290, LEFT[1], 500), PROSE,
                        fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
    page.insert_textbox(fitz.Rect(RIGHT[0], 300, RIGHT[1], 500), PROSE,
                        fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
    page.draw_rect(fitz.Rect(figure_x[0], 90, figure_x[1], 238),
                   color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((figure_x[0] + 60, 252), "Learner steps", fontsize=8)
    page.insert_text((caption_x, 278), caption, fontsize=9)
    return page


def save(doc, tmp_path):
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


def blocks_starting(page, text):
    return [b["bbox"] for b in page.get_text("dict")["blocks"]
            if b.get("type") == 0 and pm._block_text(b).startswith(text)]


class TestTwoColumns:

    def test_the_layout_is_the_one_described(self, tmp_path):
        doc = fitz.open()
        page = two_column_page(doc, (330, 540), RIGHT[0],
                               "Figure 2. Scores in the two settings.")
        left = max((b["bbox"] for b in page.get_text("dict")["blocks"]
                    if b["bbox"][2] < 300 and b["bbox"][3] < 270),
                   key=lambda box: box[3])
        caption = blocks_starting(page, "Figure 2.")[0]
        assert 5 < caption[1] - left[3] < 15

    def test_a_figure_in_the_right_column_is_rendered(self, tmp_path):
        doc = fitz.open()
        two_column_page(doc, (330, 540), RIGHT[0],
                        "Figure 2. Scores in the two settings.")
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        box = media[("figure", 2)]["box"]
        assert box[1] <= 90 and box[3] >= 238

    def test_it_stays_in_its_column(self, tmp_path):
        doc = fitz.open()
        two_column_page(doc, (330, 540), RIGHT[0],
                        "Figure 2. Scores in the two settings.")
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert media[("figure", 2)]["box"][0] >= RIGHT[0] - pm._PAD

    def test_a_full_width_figure_still_stops_at_either_columns_prose(self, tmp_path):
        # The left column's prose ends below a drawing in the right column; a
        # caption across both columns is bounded by it all the same.
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        left = prose(page, LEFT[0], LEFT[1], 200)
        prose(page, RIGHT[0], RIGHT[1], 150)
        page.draw_rect(fitz.Rect(400, 160, 500, 195), color=(0, 0, 0))
        page.draw_rect(fitz.Rect(80, 210, 520, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((LEFT[0], 420),
                         "Figure 1. A figure across both columns, under prose "
                         "that ends at different heights in each column.",
                         fontsize=9)
        assert blocks_starting(page, "Figure 1.")[0][2] > RIGHT[0]
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert media[("figure", 1)]["box"][1] >= left[3]


class TestTheCaptionsColumn:
    """Prose beside a caption bounds the float to the caption's side of it."""

    def test_a_line_reaching_into_the_other_column_is_not_climbed(self, tmp_path):
        # An author line spans both columns above a right-column teaser, with
        # the abstract beside the figure: the crop must not take either.
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 110), "First Author   Second Author   Third Author",
                         fontsize=10)
        page.insert_textbox(fitz.Rect(LEFT[0], 130, LEFT[1], 330), PROSE,
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        page.insert_textbox(fitz.Rect(LEFT[0], 300, LEFT[1], 500), PROSE,
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        page.draw_rect(fitz.Rect(330, 140, 540, 380), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 400), "Figure 1. A teaser.", fontsize=9)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        box = media[("figure", 1)]["box"]
        assert box[0] >= RIGHT[0] - pm._PAD
        assert 140 - pm._PAD - 1 <= box[1] <= 140

    def test_figures_side_by_side_are_cropped_apart(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x, number in ((80, 1), (320, 2)):
            page.draw_rect(fitz.Rect(x, 100, x + 210, 250), color=(0, 0, 0),
                           fill=(0.2, 0.4, 0.8))
            page.insert_textbox(fitz.Rect(x, 258, x + 210, 300),
                                f"Figure {number}: A caption long enough to wrap "
                                "onto a second line under its figure.", fontsize=8)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        left, right = media[("figure", 1)]["box"], media[("figure", 2)]["box"]
        assert left[0] <= 80 and left[2] < 320
        assert right[0] > 290 and right[2] >= 530


class TestAPageOfTables:

    def test_body_size_prose_under_a_table_ends_it(self, tmp_path):
        # The page's own print is the table's; a two-line paragraph at body
        # size below it has a short last line, so its measure alone does not
        # make it prose.
        doc = fitz.open()
        body = doc.new_page(width=LETTER[0], height=LETTER[1])
        for y in (150, 330, 510, 690):
            prose(body, 72, 540, y)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((200, 80), "Table 1: Hyperparameters.", fontsize=9)
        page.draw_line((150, 90), (460, 90))
        for i in range(14):
            page.insert_text((160, 104 + 10 * i), f"parameter number {i}", fontsize=7)
            page.insert_text((380, 104 + 10 * i), f"{0.5 * i:.3f}", fontsize=7)
        page.draw_line((150, 240), (460, 240))
        page.insert_text((72, 262), "The rest of the paragraph that the table "
                         "interrupted runs on here, across the whole", fontsize=10)
        page.insert_text((72, 274), "measure, and ends.", fontsize=10)
        page.insert_text((200, 300), "L(x) = f(x) + g(x)", fontsize=10)
        page.draw_line((150, 320), (460, 320))
        assert pm._body_size(page.get_text("dict")["blocks"]) == 7.0
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert media[("table", 1)]["box"][3] < 254

    def test_body_text_close_under_the_last_rule_ends_the_table(self, tmp_path):
        # Body-size text 5 pt under the bottom rule, within its width, with no
        # drawing below it.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 5: Ablations.", fontsize=8)
        bottom = ruled_table(page, 150, 460, 76, 5)
        page.insert_text((160, bottom + 14), "the text that the table interrupted",
                         fontsize=10)
        page.insert_text((160, bottom + 26), "goes on.", fontsize=10)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 5)]["box"]
        assert box[3] < bottom + 8

    def test_structure_as_near_on_both_sides_is_the_table_below(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 80)
        page.insert_text((150, 213), "Table 6: A table under its caption.", fontsize=8)
        caption = blocks_starting(page, "Table 6")[0]
        page.draw_rect(fitz.Rect(150, 100, 460, caption[1] - 6), color=(0, 0, 0))
        ruled_table(page, 150, 460, caption[3] + 6, 4)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 6)]["box"]
        assert box[1] >= caption[1]


class TestAPageOfPlots:

    def plots(self, doc):
        """Two subplots, each with ticks at 5 pt and an axis title at 9 pt."""
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (80, 330):
            page.draw_rect(fitz.Rect(x, 100, x + 200, 300), color=(0, 0, 0))
            for i, tick in enumerate(("0", "100k", "200k", "300k", "400k", "500k")):
                page.insert_text((x + 35 * i, 312), tick, fontsize=5)
            for i, tick in enumerate(("0", "25000", "50000", "75000", "100000")):
                page.insert_text((x - 25, 295 - 45 * i), tick, fontsize=5)
            page.insert_text((x + 70, 328), "Learner steps", fontsize=9)
        page.insert_text((80, 345), "Figure 3. Scores of both methods.", fontsize=9)
        return page

    def test_the_page_is_mostly_tick_labels(self, tmp_path):
        doc = fitz.open()
        page = self.plots(doc)
        assert pm._body_size(page.get_text("dict")["blocks"]) == 5.0

    def test_an_axis_title_above_the_caption_does_not_end_the_figure(self, tmp_path):
        doc = fitz.open()
        body = doc.new_page(width=LETTER[0], height=LETTER[1])
        for y in (150, 330, 510, 690):
            prose(body, 72, 540, y)
        self.plots(doc)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        box = media[("figure", 3)]["box"]
        assert box[1] <= 100 and box[3] >= 328

    def test_body_size_is_the_documents(self, tmp_path):
        doc = fitz.open()
        body = doc.new_page(width=LETTER[0], height=LETTER[1])
        for y in (150, 330, 510, 690):
            prose(body, 72, 540, y)
        self.plots(doc)
        assert pm.document_body_size(fitz.open(save(doc, tmp_path))) == 10.0

    def test_body_size_is_the_documents_when_the_plots_come_first(self, tmp_path):
        doc = fitz.open()
        self.plots(doc)
        body_pages(doc)
        assert pm.document_body_size(fitz.open(save(doc, tmp_path))) == 10.0


def body_pages(doc, pages=1):
    """Pages of 10 pt prose, so the document's body size is 10 pt."""
    for _ in range(pages):
        body = doc.new_page(width=LETTER[0], height=LETTER[1])
        for y in (150, 330, 510, 690):
            prose(body, 72, 540, y)


class TestColumnWalls:
    """Only prose in another column confines a float to the caption's column."""

    def plot_with_titles(self, doc):
        """A single-column plot whose axis and legend titles are set at 12 pt,
        beside a caption narrower than the plot."""
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(110, 80, 500, 300), color=(0, 0, 0))
        page.draw_rect(fitz.Rect(150, 100, 420, 280), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((128, 240), "Reward mean", fontsize=12, rotate=90)
        page.insert_text((440, 160), "Model", fontsize=12)
        for i, tick in enumerate(("0", "250k", "500k", "750k")):
            page.insert_text((150 + 80 * i, 292), tick, fontsize=9)
        page.insert_text((200, 318), "Figure 13: Tuning both models.", fontsize=10)
        return page

    def test_a_plots_own_titles_are_not_column_walls(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        self.plot_with_titles(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 13)]["box"]
        assert box[0] <= 110 and box[1] <= 80 and box[2] >= 500

    def test_content_reaching_just_past_a_wall_is_kept(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        wall = prose(page, LEFT[0], LEFT[1], 430)
        page.draw_rect(fitz.Rect(wall[2] - 1, 200, 540, 380),
                       color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 398), "Figure 2. A right-column figure.",
                         fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert box[0] <= wall[2] - 1

    def test_prose_just_beside_the_caption_is_no_wall(self, tmp_path):
        # A figure and its caption at the left of the page, a paragraph
        # starting 3 pt right of the caption, beside both.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(72, 100, 247, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((72, 320), "Figure 9: A small figure.", fontsize=9)
        caption = blocks_starting(page, "Figure 9")[0]
        page.insert_textbox(fitz.Rect(caption[2] + 3, 90, 540, 400), PROSE,
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 9)]["box"]
        assert box[2] > caption[2] + 3

    def test_small_print_beside_the_caption_is_no_wall(self, tmp_path):
        # A paragraph of 8 pt print left of a caption under a figure across the
        # page.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(72, 100, 540, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_textbox(fitz.Rect(72, 310, 300, 480), PROSE, fontsize=8,
                            align=fitz.TEXT_ALIGN_JUSTIFY)
        page.insert_text((330, 320), "Figure 9: A figure.", fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 9)]["box"]
        assert box[0] <= 72

    def test_content_in_the_other_column_is_left_out(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        wall = prose(page, LEFT[0], LEFT[1], 430)
        page.draw_rect(fitz.Rect(wall[2] - 40, 250, wall[2] - 4, 300),
                       color=(0, 0, 0))
        page.draw_rect(fitz.Rect(RIGHT[0] + 10, 200, 540, 380), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 398), "Figure 2. A right-column figure.",
                         fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert box[0] >= RIGHT[0] + 10 - pm._PAD - 1


class TestAnAxisTitleAtBodySize:

    def test_it_does_not_end_the_figure(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (80, 330):
            page.draw_rect(fitz.Rect(x, 100, x + 200, 300), color=(0, 0, 0))
            for i, tick in enumerate(("0", "100k", "200k", "300k", "400k", "500k")):
                page.insert_text((x + 35 * i, 312), tick, fontsize=5)
            for i, tick in enumerate(("0", "25000", "50000", "75000", "100000")):
                page.insert_text((x - 25, 295 - 45 * i), tick, fontsize=5)
            page.insert_text((x + 70, 328), "Learner steps", fontsize=10)
        page.insert_text((80, 345), "Figure 3. Scores of both methods.", fontsize=9)
        assert pm._body_size(page.get_text("dict")["blocks"]) == 5.0
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[1] <= 100 and box[3] >= 328


def ruled_table(page, x0, x1, top, rows, size=8, rule_x=None):
    """A booktabs table: rules over, under the header and under the body, and
    `rows` lines of cells between them. Returns the bottom rule's y."""
    rx0, rx1 = rule_x or (x0, x1)
    page.draw_line((rx0, top), (rx1, top))
    page.insert_text((x0 + 4, top + size + 2), "Model", fontsize=size)
    page.insert_text((x1 - 40, top + size + 2), "Error", fontsize=size)
    page.draw_line((rx0, top + size + 5), (rx1, top + size + 5))
    y = top + size + 5
    for i in range(rows):
        y += size + 2
        page.insert_text((x0 + 4, y), f"network {i}", fontsize=size)
        page.insert_text((x1 - 40, y), f"{5 + i * 0.37:.2f}", fontsize=size)
    page.draw_line((rx0, y + 4), (rx1, y + 4))
    return y + 4


class TestATableAtTheTopOfAColumn:
    """A two-column page: a table at the top of the left column, then the last
    two lines of a paragraph, then the column's prose."""

    def page(self, doc):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((100, 60), "Table 4: Error on the small set.", fontsize=8)
        bottom = ruled_table(page, LEFT[0] + 8, LEFT[1] - 8, 66, 4)
        page.insert_text((LEFT[0], bottom + 22), "the accuracy by a large margin "
                         "at a minimal increase", fontsize=10)
        page.insert_text((LEFT[0], bottom + 34), "model.", fontsize=10)
        prose(page, LEFT[0], LEFT[1], 500)
        prose(page, RIGHT[0], RIGHT[1], 300)
        return page, bottom

    def test_the_paragraph_tail_under_it_is_not_part_of_it(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        _, bottom = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < bottom + 12

    def test_a_table_set_at_body_size_keeps_its_rows(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((100, 60), "Table 2: Error at body size.", fontsize=10)
        bottom = ruled_table(page, LEFT[0] + 8, LEFT[1] - 8, 66, 6, size=10,
                             rule_x=(LEFT[0], LEFT[1]))
        prose(page, LEFT[0], LEFT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[3] >= bottom


class TestATableCaptionedBelow:

    def test_its_body_is_above_the_caption(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 150, 460, 80, 8)
        page.insert_text((72, bottom + 14), "Table E.1: Participant details for "
                         "each experiment.", fontsize=10)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", "E.1")]["box"]
        assert box[1] <= 80 and bottom <= box[3] <= bottom + 10

    def test_a_figure_under_its_caption_is_not_taken(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 150, 460, 80, 8)
        page.insert_text((72, bottom + 14), "Table E.1: Participant details for "
                         "each experiment.", fontsize=10)
        picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
        picture.set_rect(picture.irect, (40, 90, 200))
        page.insert_image(fitz.Rect(150, bottom + 40, 460, bottom + 240),
                          pixmap=picture)
        page.insert_text((72, bottom + 260), "Figure E.1: Time spent per article.",
                         fontsize=10)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", "E.1")]["box"]
        assert box[1] <= 80 and box[3] <= bottom + 10

    def test_the_nearer_table_is_its_own(self, tmp_path):
        # Table 2's caption sits under Table 1's bottom rule and over its own
        # top rule; its own is the nearer one.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 60), "Table 1: The first table.", fontsize=8)
        first = ruled_table(page, 150, 460, 66, 4)
        page.insert_text((150, first + 20), "Table 2: The second table.", fontsize=8)
        second = ruled_table(page, 150, 460, first + 24, 4)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] >= first + 20 and box[3] >= second

    def test_a_caption_with_no_table_beside_it_renders_nothing(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 200)
        page.insert_text((72, 230), "Table 3: A caption whose table is elsewhere.",
                         fontsize=10)
        prose(page, 72, 540, 450)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 3) not in media
        assert [(u["kind"], u["label"], u["page"]) for u in unrendered] == [
            ("table", 3, 2)]


class TestAFullWidthTableAboveAColumnFigure:
    """A right-column figure under a table across both columns whose caption is
    the last line of the table's own text block."""

    def test_the_figure_does_not_climb_into_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        rows = "\n".join(f"method {i}    {30 + i}.1    {40 + i}.9    {20 + i}.4"
                         for i in range(6))
        page.insert_textbox(fitz.Rect(64, 72, 531, 160),
                            rows + "\nTable 7. Results on the validation set.",
                            fontsize=8)
        page.draw_line((64, 84), (531, 84))
        page.draw_line((64, 136), (531, 136))
        for x in (300, 420):
            for y in range(72, 146, 10):
                page.draw_line((x, y), (x, y + 10))
        page.draw_rect(fitz.Rect(RIGHT[0] + 4, 180, 540, 340), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 360), "Figure 8. Results on the test set.",
                         fontsize=9)
        prose(page, LEFT[0], LEFT[1], 340)
        prose(page, LEFT[0], LEFT[1], 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 8)]["box"]
        assert box[1] >= 160


class TestBodyTextOverNeighbouringSizes:
    """Body prose printed at 10.8, 10.9 and 11 pt, references at 10 pt: 10 pt
    holds more characters than any one of the body's sizes."""

    def doc(self):
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for size, bottom in ((10.8, 200), (10.9, 420), (11.0, 640)):
            prose(page, 72, 540, bottom, size=size)
        refs = doc.new_page(width=LETTER[0], height=LETTER[1])
        for i in range(16):
            refs.insert_text((72, 80 + 14 * i), f"[{i}] An author. A reference "
                             "work of some length. A venue, 2020.", fontsize=10)
        return doc

    def test_the_body_size_is_the_prose(self):
        assert abs(pm.document_body_size(self.doc()) - 10.9) <= 0.1 + 1e-9

    def test_a_table_set_at_body_size_is_rendered(self, tmp_path):
        doc = self.doc()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 150, 460, 100, 6, size=10.9)
        page.insert_text((150, bottom + 14), "Table 4: Scores on the held-out set.",
                         fontsize=10)
        prose(page, 72, 540, 500, size=11.0)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert box[1] <= 100 and box[3] >= bottom


def fragmented_lines(page, x0, top, rows, size=10):
    """A justified paragraph of `rows` lines, every third printed as three
    fragments on one baseline; returns the paragraph's right edge."""
    line = "ordinary words of body prose set across the column"
    width = fitz.get_text_length(line, fontsize=size)
    for i in range(rows):
        y = top + i * (size + 2)
        if i % 3:
            page.insert_text((x0, y), line, fontsize=size)
            continue
        page.insert_text((x0, y), "ordinary words", fontsize=size)
        page.insert_text((x0 + width / 2 - 20, y), "of body", fontsize=size)
        tail = "the column"
        page.insert_text((x0 + width - fitz.get_text_length(tail, fontsize=size), y),
                         tail, fontsize=size)
    return x0 + width


class TestJustifiedProseInFragments:

    def test_it_bounds_a_table_in_the_other_column(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((LEFT[0], 70), "Table 3: Scores on the small set.",
                         fontsize=8)
        ruled_table(page, LEFT[0] + 8, LEFT[1] - 8, 76, 5)
        fragmented_lines(page, RIGHT[0], 82, 12)
        prose(page, LEFT[0], LEFT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[2] < RIGHT[0]


class TestNarrowPrintIsNoBarrier:

    def test_a_listing_figure_keeps_all_its_lines(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((56, 100), (286, 100))
        y = 112
        for lines in (["a = read(input)    # n, d", "b = read(output)   # n, d",
                       "a = scale(a)       # n, d"],
                      ["scores = mul(a, b.T) * k", "ranks = order(n)        "],
                      ["cost_a = measure(scores, ranks, axis=0)", "cost_b = 0",
                       "cost = (cost_a + cost_b) / 2", "step(cost)", "end"]):
            for text in lines:
                page.insert_text((56, y), text, fontsize=7.7)
                y += 9.5
            y += 12
        page.insert_text((56, y + 8), "Figure 3. Pseudocode for the core of the "
                         "method.", fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[1] <= 100

    def test_a_plots_annotations_are_kept(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(150, 280, 380, 420), color=(0, 0, 0))
        page.draw_line((160, 400), (370, 300))
        for y, lines in ((300, ("Series one rises", "only very slowly")),
                         (355, ("Series two rises", "faster than one"))):
            for i, text in enumerate(lines):
                page.insert_text((385, y + 9 * i), text, fontsize=7.8)
        page.insert_text((90, 440), "Figure 3. As the budget grows, the first quantity "
                         "grows faster than the second one does here.",
                         fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[2] >= 440


class TestARulelessTableWithGroupRows:
    """A table captioned below whose rules are raster strips, not drawings, and
    whose groups open with a row of one cell set at body size."""

    def test_its_group_rows_are_rows(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 80)
        strip = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1), False)
        cols = (100, 260, 340, 410, 480)
        rows = [("Model", "Set A", "Set B", "Set C", "Set D"), None,
                ("Group one (with the first setup):",),
                ("pairs", "90.4/78.7", "84.0", "92.9", "64.2"),
                ("sentences", "88.7/76.2", "82.9", "93.2", "63.0"), None,
                ("(with the second setup):",),
                ("documents", "90.6/79.4", "84.7", "92.5", "64.8"),
                ("full", "91.2/80.1", "85.1", "93.1", "66.1"), None]
        y, groups = 110, []
        for row in rows:
            if row is None:
                page.insert_image(fitz.Rect(94, y - 6, 514, y - 5.4), pixmap=strip)
                y += 4
            elif len(row) == 1:
                groups.append((y, row[0]))
                y += 14
            else:
                for x, cell in zip(cols, row):
                    page.insert_text((x, y), cell, fontsize=10)
                y += 14
        for at, text in groups:                 # each a block of its own
            page.insert_text((cols[0], at), text, fontsize=10)
        page.insert_text((100, y + 14), "Table 2: Results on held-out sets.",
                         fontsize=10)
        prose(page, 72, 540, 420)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 101 and box[3] >= y - 14


class TestStackedTablesCaptionedBelow:

    def test_each_takes_the_table_above_it(self, tmp_path):
        # Table 1's caption is nearer Table 2's top rule than its own bottom
        # rule.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 80)
        first = ruled_table(page, 150, 460, 100, 6)
        page.insert_text((150, first + 16), "Table 1: The first table.", fontsize=8)
        second = ruled_table(page, 150, 460, first + 21, 6)
        page.insert_text((150, second + 16), "Table 2: The second table.",
                         fontsize=8)
        prose(page, 72, 540, 500)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        one, two = media[("table", 1)]["box"], media[("table", 2)]["box"]
        assert one[1] <= 100 and one[3] <= first + 8
        assert two[1] >= first + 4 and two[3] >= second


class TestAColumnBesideAnotherFloat:
    """A table at the top of the left column beside a plot at the top of the
    right one; the right column's prose starts under the plot's caption."""

    def test_the_table_stays_in_its_column(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((LEFT[0], 70), "Table 1: Accuracy on the benchmark.",
                         fontsize=8)
        ruled_table(page, LEFT[0] + 8, LEFT[1] - 8, 76, 5)
        prose(page, LEFT[0], LEFT[1], 420)
        page.draw_rect(fitz.Rect(330, 60, 540, 260), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 278), "Figure 4: Scores over training.",
                         fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[2] < RIGHT[0]


class TestATableAboveAFigureInOneColumn:
    """A gridded table captioned above, then a figure of two plots with
    gridlines captioned below, then prose."""

    def page(self, doc, note=False):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 1: Comparison of accuracy.", fontsize=8)
        bottom = ruled_table(page, 150, 460, 76, 6)
        for x in (150, 300, 460):
            page.draw_line((x, 76), (x, bottom))
        if note:
            page.insert_text((150, bottom + 9), "* on the test split", fontsize=7)
        for x in (150, 310):
            page.draw_rect(fitz.Rect(x, bottom + 30, x + 150, bottom + 150),
                           color=(0, 0, 0))
            for y in (bottom + 70, bottom + 110):        # gridlines
                page.draw_line((x, y), (x + 150, y), width=0.3)
            for i, tick in enumerate(("0", "100", "200", "300")):
                page.insert_text((x + 45 * i, bottom + 158), tick, fontsize=6)
        page.insert_text((150, bottom + 175), "Figure 4: Trajectories of both "
                         "runs.", fontsize=9)
        prose(page, 72, 540, 700)
        return bottom

    def test_the_table_ends_at_its_last_rule(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[3] <= bottom + 7

    def test_a_note_under_the_last_rule_stays_with_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom = self.page(doc, note=True)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert bottom + 9 <= box[3] <= bottom + 18

    def test_the_figure_does_not_climb_into_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] >= bottom


class TestTheCropIsChecked:

    def page(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 300)
        page.insert_text((72, 330), "Figure 2: Another float.", fontsize=9)
        page.draw_rect(fitz.Rect(150, 340, 460, 420), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        return doc, page

    def test_a_crop_holding_body_prose_is_refused(self):
        doc, page = self.page()
        body = pm.body_text(doc)
        far = (72, 700, 300, 710)
        assert pm._crop_problem(page, fitz.Rect(60, 250, 550, 310), "table", 9,
                                far, body) == pm.HOLDS_PROSE
        assert pm._crop_problem(page, fitz.Rect(60, 335, 550, 425), "figure", 9,
                                far, body) is None

    def test_a_crop_holding_another_caption_is_refused(self):
        doc, page = self.page()
        assert pm._crop_problem(page, fitz.Rect(60, 315, 550, 425), "figure", 9,
                                (72, 700, 300, 710),
                                pm.body_text(doc)) == pm.HOLDS_CAPTION

    def test_a_figure_of_text_alone_is_reported(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 300)
        page.insert_text((72, 320), "A run-in heading of a few words.", fontsize=9)
        page.insert_text((72, 331), "and then a short line", fontsize=9)
        page.insert_text((72, 350), "Figure 5: A figure set beside its caption.",
                         fontsize=9)
        prose(page, 72, 540, 600)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 5) not in media
        assert [(u["kind"], u["label"], u["page"], u["problem"])
                for u in unrendered] == [("figure", 5, 2, pm.NO_GRAPHICS)]


def two_sided(doc, pages, figure_top):
    """`pages` pages under alternating running heads, each with a rule under
    its head at y 48 and a figure starting at `figure_top`."""
    for i in range(pages):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        head = "A STUDY OF THINGS" if i % 2 == 0 else "FIRST AND SECOND AUTHOR"
        page.insert_text((72, 40), head, fontsize=9)
        page.draw_line((72, 48), (540, 48), width=0.5)
        page.draw_rect(fitz.Rect(150, figure_top, 460, 250), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 266), f"Figure {i + 1}: A figure on page {i + 1}.",
                         fontsize=9)
        prose(page, 72, 540, 700)


class TestAlternatingRunningHeads:

    def test_neither_head_is_cropped(self, tmp_path):
        doc = fitz.open()
        two_sided(doc, 4, 60)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert all(media[("figure", n)]["box"][1] >= 48.5 for n in (1, 2, 3, 4))

    def test_the_padding_stops_at_the_head_rule(self, tmp_path):
        doc = fitz.open()
        two_sided(doc, 4, 52)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert all(media[("figure", n)]["box"][1] >= 48.5 for n in (1, 2, 3, 4))


class TestEachCaptionIsTriedInTurn:

    def test_a_sentence_opening_on_the_label_does_not_take_its_place(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_textbox(fitz.Rect(72, 60, 540, 160),
                            "Figure 4 (a) presents the results of the first run "
                            "and the second run on every task. " + PROSE[:300],
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        page.draw_rect(fitz.Rect(150, 200, 460, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 418), "Figure 4: (a) First run. (b) Second run.",
                         fontsize=9)
        prose(page, 72, 540, 650)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] <= 200 and box[3] >= 400

    def test_a_caption_with_no_crop_gives_way_to_the_next(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        page.insert_text((72, 170), "Figure 6: Overview of the method.", fontsize=9)
        prose(page, 72, 540, 300)
        page.draw_rect(fitz.Rect(150, 330, 460, 500), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 518), "Figure 6: Overview of the method.", fontsize=9)
        prose(page, 72, 540, 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 6)]["box"]
        assert box[1] <= 330 and box[3] >= 500

    def test_a_figure_caption_with_nothing_beside_it_is_reported(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 300)
        page.insert_text((72, 320), "Figure 7: A caption whose figure is elsewhere.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 7) not in media
        assert [(u["kind"], u["label"], u["page"], u["problem"])
                for u in unrendered] == [("figure", 7, 2, pm.NOTHING_BESIDE)]


class TestMoreBounds:

    def test_a_table_at_body_size_in_one_block_keeps_its_rows(self, tmp_path):
        # Five long cells a row, every row one block's line objects.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 70), "Table 8: Results on every benchmark.", fontsize=10)
        page.draw_line((72, 76), (540, 76))
        for i in range(6):
            for j, x in enumerate((76, 170, 264, 358, 452)):
                page.insert_text((x, 90 + 12 * i), f"benchmark {i}{j} 9{j}.{i}",
                                 fontsize=10)
        page.draw_line((72, 166), (540, 166))
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 8)]["box"]
        assert box[3] >= 166

    def test_a_three_line_paragraph_across_the_column_ends_a_figure(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(250, 80, 360, 150), color=(0, 0, 0))
        line = ("words of the paragraph that runs the full measure of the page "
                "and then some more words to fill it out")
        assert fitz.get_text_length(line, fontsize=10) > 440
        for i, text in enumerate((line, line, "and ends.")):
            page.insert_text((72, 170 + 12 * i), text, fontsize=10)
        page.draw_rect(fitz.Rect(150, 200, 460, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 415), "Figure 2: Under a short paragraph.", fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert box[1] >= 196

    def test_a_legend_title_beside_the_plot_is_kept(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(110, 100, 400, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((410, 200), "Model", fontsize=12)
        page.insert_text((200, 318), "Figure 13: Tuning both models.", fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 13)]["box"]
        assert box[2] >= 440

    def test_stacked_tables_end_before_a_figure_under_them(self, tmp_path):
        # Captions above: Table 1, Table 2 with rules of the same width, then
        # a figure in the same column.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 1: The first table.", fontsize=8)
        first = ruled_table(page, 150, 460, 76, 4)
        page.insert_text((150, first + 20), "Table 2: The second table.", fontsize=8)
        second = ruled_table(page, 150, 460, first + 26, 4)
        page.draw_rect(fitz.Rect(150, second + 30, 460, second + 200),
                       color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((150, second + 216), "Figure 5: Under both tables.",
                         fontsize=9)
        prose(page, 72, 540, 740)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert media[("table", 2)]["box"][3] <= second + 8
        assert media[("figure", 5)]["box"][1] >= second

    def test_a_sentence_on_the_label_under_another_drawing_waits(self, tmp_path):
        # The sentence comes first and has an uncaptioned drawing above it.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(250, 40, 360, 100), color=(0, 0, 0))
        page.insert_textbox(fitz.Rect(72, 120, 540, 190),
                            "Figure 4 (a) presents the results of the first run "
                            "and the second run on every task. " + PROSE[:250],
                            fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
        page.draw_rect(fitz.Rect(150, 200, 460, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 418), "Figure 4: (a) First run. (b) Second run.",
                         fontsize=9)
        prose(page, 72, 540, 650)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] >= 180 and box[3] >= 400


class TestProseBoundsTheWalk:

    def test_an_inline_drawing_does_not_carry_a_figure_into_prose(self, tmp_path):
        # A glyph drawn as a path at the start of the prose's last line hangs
        # down to the figure's frame.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        wall = prose(page, 72, 540, 166)
        page.draw_rect(fitz.Rect(72, 155, 97, 180), color=(0, 0, 0))
        page.draw_rect(fitz.Rect(72, 180, 540, 279), color=(0, 0, 0))
        page.draw_rect(fitz.Rect(100, 190, 500, 270), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((72, 295), "Figure 1: A figure under the prose.", fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert box[1] >= 173 > wall[3]

    def test_the_padding_stops_at_the_prose_above(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        wall = prose(page, 72, 540, 177)
        page.draw_rect(fitz.Rect(150, 180, 460, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 316), "Figure 1: A figure close under the prose.",
                         fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert box[1] >= wall[3]

    def test_a_row_of_tick_labels_is_no_barrier(self, tmp_path):
        # Ticks, axis title and plot title in one block of 8.2 pt print as wide
        # as the column, under the plot of a left-column figure.
        doc = fitz.open()
        for _ in range(2):
            body = doc.new_page(width=LETTER[0], height=LETTER[1])
            for x0, x1 in (LEFT, RIGHT):
                for y in (250, 500, 750):
                    prose(body, x0, x1, y)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(70, 70, 280, 280), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        for i, tick in enumerate(("-10", "-5", "0", "5", "10", "15", "20", "25")):
            page.insert_text((LEFT[0] + 32 * i, 290), tick, fontsize=8.2)
        page.insert_text((140, 299), "Score difference (%)", fontsize=8.2)
        page.insert_text((LEFT[0], 308), "Difference of one model's scores "
                         "from the other model's", fontsize=8.2)
        page.insert_text((LEFT[0], 330), "Figure 11. One model measured against the "
                         "other.", fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 11)]["box"]
        assert box[1] <= 70


class TestWhoseTableItIs:

    def test_rows_in_the_captions_own_block_are_the_table(self, tmp_path):
        # The caption, a bold group header and the first rows share one block.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((230, 88), "Table 13: Answers to a short survey",
                         fontsize=10)
        page.insert_text((178, 102), "The instructions were easy to follow.",
                         fontsize=10, fontname="hebo")
        for i, (answer, share) in enumerate((("Option one", "61.0%"),
                                             ("Option two", "39.0%"),
                                             ("Option three", "0%"))):
            page.insert_text((173, 114 + 10 * i), answer, fontsize=10)
            page.insert_text((413, 114 + 10 * i), share, fontsize=10)
        page.draw_line((170, 140), (400, 140))
        page.draw_line((170, 142), (400, 142))
        prose(page, 72, 540, 400)
        assert len([b for b in page.get_text("dict")["blocks"]
                    if b["bbox"][1] < 145]) == 1
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 13)]["box"]
        assert box[1] <= 93 and box[2] >= 430

    def test_a_rule_under_the_caption_ends_it(self, tmp_path):
        # The caption has no full stop; the table's header follows its top rule.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 80), "Table 3: Results on the held-out set", fontsize=9)
        page.draw_line((150, 86), (460, 86))
        page.insert_text((154, 96), "Model   Accuracy   Recall", fontsize=9)
        page.draw_line((150, 100), (460, 100))
        for i in range(4):
            page.insert_text((154, 110 + 10 * i), f"model {i}", fontsize=8)
            page.insert_text((400, 110 + 10 * i), f"{60 + i}.0", fontsize=8)
        page.draw_line((150, 144), (460, 144))
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[1] <= 88

    def test_a_figure_caption_above_claims_nothing_under_it(self, tmp_path):
        # Right column: a figure across the page, its caption, Table 7 and its
        # caption, then a plot close under that caption and the plot's caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(72, 60, 540, 150), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((280, 162), "Training steps", fontsize=8)
        page.insert_textbox(fitz.Rect(72, 166, 540, 200), "Figure 6: Curves "
                            "of every model on the small set; the dashed lines "
                            "are the first measure and the solid ones the second.",
                            fontsize=9)
        first = ruled_table(page, RIGHT[0] + 8, RIGHT[1] - 8, 205, 3)
        page.insert_text((RIGHT[0], first + 14), "Table 7: Results on the first set.",
                         fontsize=9)
        caption = blocks_starting(page, "Table 7")[0]
        page.draw_rect(fitz.Rect(RIGHT[0] + 8, caption[3] + 3, RIGHT[1] - 8,
                                 caption[3] + 120), color=(0, 0, 0))
        page.insert_text((RIGHT[0], caption[3] + 136), "Figure 7: Scores on the "
                         "second set.", fontsize=9)
        prose(page, LEFT[0], LEFT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 7)]["box"]
        assert box[1] <= 205 and box[3] <= first + 8

    def test_a_figure_caption_above_does_not_claim_the_table_below_it(self, tmp_path):
        # A figure across the page, its caption, then in the right column two
        # tables captioned below.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(72, 60, 540, 160), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_textbox(fitz.Rect(72, 166, 540, 200), "Figure 6: Curves "
                            "of every model on the small set; the dashed lines "
                            "are the first measure and the solid ones the second.",
                            fontsize=9)
        first = ruled_table(page, RIGHT[0] + 8, RIGHT[1] - 8, 205, 3)
        page.insert_text((RIGHT[0], first + 14), "Table 7: Results on the first set.",
                         fontsize=9)
        second = ruled_table(page, RIGHT[0] + 8, RIGHT[1] - 8, first + 24, 3)
        page.insert_text((RIGHT[0], second + 14), "Table 8: Results on the second "
                         "set.", fontsize=9)
        prose(page, LEFT[0], LEFT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 7)]["box"]
        assert box[1] <= 205 and box[3] <= first + 8

    def test_the_nearer_caption_owns_a_table_between_two(self, tmp_path):
        # Captions above: Table 7's rules end just above Table 8's caption,
        # Table 8's start under it; Table 9's caption, further below them, has
        # a header line before its own rules.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((LEFT[0], 26), "Table 7: Results on the first set.",
                         fontsize=9)
        ruled_table(page, LEFT[0] + 4, LEFT[1] - 4, 30, 3)
        page.insert_text((LEFT[0], 100), "Table 8: Results with each setting.",
                         fontsize=9)
        first = ruled_table(page, LEFT[0] + 4, LEFT[1] - 4, 104, 4)
        page.insert_text((LEFT[0], first + 22), "Table 9: Results with each "
                         "sample count.", fontsize=9)
        page.insert_text((LEFT[0] + 8, first + 34), "samples   train   test   score",
                         fontsize=8)
        ruled_table(page, LEFT[0] + 4, LEFT[1] - 4, first + 38, 4)
        prose(page, RIGHT[0], RIGHT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 8)]["box"]
        assert box[1] <= 104 and box[3] >= first

    def test_a_figure_caption_close_above_a_table_does_not_own_it(self, tmp_path):
        # Figure 6's caption ends 3 pt above Table 7's top rule; Table 7's
        # caption is 9 pt under its bottom rule.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(72, 60, 540, 160), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((72, 175), "Figure 6: Training curves of every model on the "
                         "small set, dashed for training error.", fontsize=9)
        figure = blocks_starting(page, "Figure 6")[0]
        first = ruled_table(page, RIGHT[0] + 8, RIGHT[1] - 8, figure[3] + 3, 3)
        page.insert_text((RIGHT[0], first + 18), "Table 7: Results on the first set.",
                         fontsize=9)
        second = ruled_table(page, RIGHT[0] + 8, RIGHT[1] - 8, first + 30, 3)
        page.insert_text((RIGHT[0], second + 14), "Table 8: Results on the second "
                         "set.", fontsize=9)
        prose(page, LEFT[0], LEFT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 7)]["box"]
        assert box[1] <= figure[3] + 3 and first <= box[3] < first + 12

    def test_a_bold_label_on_the_captions_first_line_keeps_its_text(self, tmp_path):
        # "Figure 11" is a line object of its own, in bold, on the baseline of
        # the regular text that follows; another caption stands right of it
        # further down.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (92, 319):
            page.draw_rect(fitz.Rect(x, 72, x + 203, 204), color=(0, 0, 0),
                           fill=(0.2, 0.4, 0.8))
        width = fitz.get_text_length("Figure 11", fontname="hebo", fontsize=10)
        page.insert_text((90, 220), "Figure 11", fontname="hebo", fontsize=10)
        page.insert_text((90 + width + 12, 220), "When either of the two inputs is "
                         "held fixed, the output follows a", fontsize=10)
        page.insert_text((90, 232), "smooth curve in the other, as both fits show.",
                         fontsize=10)
        page.insert_text((255, 320), "Table 3 Fitted values.", fontsize=10)
        page.draw_line((200, 330), (410, 330))
        page.insert_text((210, 342), "a   0.25   0.50", fontsize=10)
        page.draw_line((200, 348), (410, 348))
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 11)]["box"]
        assert box[0] <= 92 and box[2] >= 522

    def test_a_paragraphs_last_line_above_a_gap_is_not_the_figure(self, tmp_path):
        # The page opens with the last line of a paragraph from the page
        # before; the figure starts 12 pt under it.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 77), "of labels benefits more from training longer.",
                         fontsize=10)
        tail = blocks_starting(page, "of labels")[0]
        page.draw_rect(fitz.Rect(150, tail[3] + 12, 460, 230), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 246), "Figure 5: Accuracy against training length.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 5)]["box"]
        assert box[1] >= tail[3]

    def test_the_padding_stops_at_a_caption_beside(self, tmp_path):
        # Table 2's caption starts 3 pt right of Table 1's rules.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((100, 70), "Table 1: Results on the first set.", fontsize=8)
        ruled_table(page, 100, 300, 76, 6, rule_x=(100, 300))
        page.insert_textbox(fitz.Rect(303, 80, 520, 140), "Table 2: Results on the "
                            "second set, side by side with the first.", fontsize=8)
        ruled_table(page, 310, 520, 145, 4)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[2] <= 303

    def test_a_long_block_of_code_is_no_prose(self, tmp_path):
        # Five comment lines of one length in a fixed-pitch face open a
        # listing figure in the left column.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((56, 66), (286, 66))
        y = 80
        for i in range(5):
            page.insert_text((56, y), f"# v{i}[n, d] - a batch of paired records",
                             fontsize=7.7, fontname="cour")
            y += 9.5
        y += 12
        for text in ("a = read(inputs)", "b = read(outputs)", "cost = (a + b) / 2"):
            page.insert_text((56, y), text, fontsize=7.7, fontname="cour")
            y += 9.5
        page.insert_text((56, y + 10), "Figure 3. Pseudocode for the core of the "
                         "method.", fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[1] <= 66

    def test_a_caption_with_its_own_table_above_claims_nothing_below(self, tmp_path):
        # Captions below: Table 14, Table 15, then numbered equations close
        # under Table 15's caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        first = ruled_table(page, 150, 460, 80, 5)
        page.insert_text((150, first + 14), "Table 14: The first table.", fontsize=9)
        second = ruled_table(page, 150, 460, first + 22, 5)
        page.insert_text((150, second + 14), "Table 15: The second table.", fontsize=9)
        for i in range(4):
            page.insert_text((200, second + 26 + 12 * i), f"z{i} = layer(z{i - 1})",
                             fontsize=10)
            page.insert_text((440, second + 26 + 12 * i), f"({18 + i})", fontsize=10)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 15)]["box"]
        assert box[1] <= first + 22 and box[3] <= second + 8

    def test_an_axis_title_far_above_the_caption_is_the_figures(self, tmp_path):
        # The plot's x-axis title, at body size, ends 13 pt above the caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(150, 100, 460, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((250, 316), "Score per problem", fontsize=10)
        title = blocks_starting(page, "Score per")[0]
        page.insert_text((150, title[3] + 13 + 8), "Figure 2: Score of each "
                         "model.", fontsize=9)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert box[1] <= 100 and box[3] >= title[3]


class TestWhichColumnHoldsTheFloat:

    def test_a_figure_across_both_columns_keeps_its_width(self, tmp_path):
        # A chain of boxes and arrows across the page over a short caption at
        # the left; the right column's prose starts under the caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (90, 230, 370):
            page.draw_rect(fitz.Rect(x, 60, x + 90, 140), color=(0, 0, 0))
            page.draw_line((x + 90, 100), (x + 140, 100))
        page.insert_text((LEFT[0], 160), "Fig. 1. A block.", fontsize=8)
        prose(page, LEFT[0], LEFT[1], 500)
        prose(page, RIGHT[0], RIGHT[1], 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert box[2] >= 510

    def test_a_table_beside_uncaptioned_rows_keeps_to_its_column(self, tmp_path):
        # Left: a table captioned below. Right: rows of another table whose
        # caption is not a block of its own; prose further down the right.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, LEFT[0] + 8, LEFT[1] - 8, 70, 8)
        page.insert_text((LEFT[0] + 20, bottom + 12), "Table 11: Training from "
                         "scratch.", fontsize=9)
        for i in range(10):
            page.insert_text((RIGHT[0] + 4, 80 + 11 * i), f"method {i}   model {i}",
                             fontsize=8)
            page.insert_text((RIGHT[1] - 40, 80 + 11 * i), f"{60 + i}.0", fontsize=8)
        prose(page, LEFT[0], LEFT[1], 500)
        prose(page, RIGHT[0], RIGHT[1], 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 11)]["box"]
        assert box[2] < RIGHT[0]

    def test_a_table_beside_reaching_under_this_caption_is_not_taken(
            self, tmp_path):
        # Table 1's last column runs 2 pt past the left edge of Table 2's
        # caption, which stands beside Table 1 lower down.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((118, 80), "Table 1: Results on the first set.", fontsize=9)
        for i in range(12):
            page.insert_text((114, 112 + 11 * i), f"model {i}", fontsize=8)
            page.insert_text((330, 112 + 11 * i), f"{30 + i}.5", fontsize=8)
        page.insert_textbox(fitz.Rect(343, 92, 503, 141), "Table 2: Results on "
                            "the second set, with each part of the objective.",
                            fontsize=9)
        for y in (145, 158, 241):
            page.draw_line((343, y), (504, y))
        for i in range(6):
            page.insert_text((349, 170 + 11 * i), f"objective {i}", fontsize=8)
            page.insert_text((460, 170 + 11 * i), f"{7 + i}.1", fontsize=8)
        prose(page, 102, 510, 420)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[0] >= 337 and box[3] <= 250


    def test_a_split_line_far_under_the_last_rule_is_not_the_table(self, tmp_path):
        # A justified line printed in two pieces 26 pt under the bottom rule.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 2: Each objective.", fontsize=9)
        bottom = ruled_table(page, 150, 460, 76, 4)
        page.insert_text((150, bottom + 34), "training.", fontsize=10)
        page.insert_text((210, bottom + 34), "However, the simpler objective "
                         "worked better", fontsize=10)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[3] <= bottom + 8

    def test_a_list_reaching_just_past_the_column_bound_is_left_out(self, tmp_path):
        # A narrow paragraph at the top left puts the column bound at its
        # right edge; lower down, a list beside the figure ends 3 pt past it.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        wall = prose(page, 72, 294, 300)
        for i, item in enumerate(("1. A small room with one goal.",
                                  "2. Two rooms joined by a door.",
                                  "3. Two rooms joined by a long hall.")):
            page.insert_text((143, 520 + 14 * i), item, fontsize=10)
        listing = blocks_starting(page, "1. A small")[0]
        page.draw_rect(fitz.Rect(326, 505, 484, 580), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((349, 600), "Figure 7: Five steps.", fontsize=10)
        assert wall[2] + 2 < listing[2] < wall[2] + 6
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 7)]["box"]
        assert box[0] >= 319


    def test_prose_under_the_next_caption_leaves_it_no_float_below(self, tmp_path):
        # Captions below: Table 2, then Table 3's rows (no rules) nearer under
        # Table 2's caption, its caption, prose, and a footnote rule at the
        # foot of the column.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        first = ruled_table(page, LEFT[0] + 10, LEFT[1] - 10, 60, 8)
        page.insert_textbox(fitz.Rect(LEFT[0], first + 14, LEFT[1], first + 50),
                            "Table 2: Results on the first set; the ensemble "
                            "is our best system.", fontsize=10)
        caption = blocks_starting(page, "Table 2")[0]
        y = caption[3] + caption[1] - first - 2
        for i in range(8):
            y += 10
            page.insert_text((LEFT[0] + 14, y), f"system {i}", fontsize=8)
            page.insert_text((LEFT[1] - 50, y), f"{70 + i}.5", fontsize=8)
        page.insert_text((LEFT[0], y + 18), "Table 3: Results on the second set.",
                         fontsize=10)
        prose(page, LEFT[0], LEFT[1], 600)
        page.draw_line((LEFT[0], 700), (LEFT[0] + 80, 700))
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 60 and box[3] <= caption[1]


class TestTheWalkKeepsWhatItCrosses:

    def test_a_block_whose_end_a_step_crossed_is_whole(self, tmp_path):
        # One block of ragged rows; bars beside all but its first row run 1 pt
        # past its foot, and the caption sits under both.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        rows = ["Requirements on the first group of items"] + [
            f"{i}) Handle " + "inputs " * (i % 3 + 1) for i in range(1, 12)]
        page.insert_textbox(fitz.Rect(64, 70, 281, 260), "\n".join(rows), fontsize=9)
        block = blocks_starting(page, "Requirements")[0]
        y = block[1] + 12
        while y < block[3] - 8:
            page.draw_line((60, y), (60, y + 9))
            y += 10
        page.draw_line((60, block[3] - 8), (60, block[3] + 1))
        page.insert_text((55, block[3] + 20), "Table 1. A summary of the "
                         "requirements.", fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[1] <= block[1]

    def test_a_figure_caption_with_a_table_under_it_owns_its_plot(self, tmp_path):
        # Captions below: Table 31's rows, its caption, a plot 1 pt under it,
        # the plot's caption, then Table 32's ruled body right under that.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 110, 500, 70, 10)
        page.insert_text((72, bottom + 14), "Table 31: Performance on each task.",
                         fontsize=10)
        caption = blocks_starting(page, "Table 31")[0]
        page.draw_rect(fitz.Rect(110, caption[3] + 1, 500, caption[3] + 140),
                       color=(0, 0, 0))
        page.insert_text((72, caption[3] + 156), "Figure 27: Gains with more training.",
                         fontsize=10)
        figure = blocks_starting(page, "Figure 27")[0]
        last = ruled_table(page, 140, 470, figure[3] + 4, 6)
        page.insert_text((72, last + 14), "Table 32: Results on each benchmark.",
                         fontsize=10)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 31)]["box"]
        assert box[1] <= 70 and box[3] <= caption[1]

    def test_a_paragraph_split_at_its_math_is_not_the_table(self, tmp_path):
        # Above a table captioned below, a two-line paragraph wider than the
        # table whose first line PyMuPDF prints in two pieces 8 pt apart.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        first = "We turn to the effect of the size on the scores and change"
        page.insert_text((108, 470), first, fontsize=10)
        page.insert_text((108 + fitz.get_text_length(first, fontsize=10) + 8, 470),
                         "{a, b},", fontsize=10)
        page.insert_text((108, 482), "and just a for a comparison.", fontsize=10)
        bottom = ruled_table(page, 131, 481, 495, 6)
        page.insert_text((108, bottom + 14), "Table 6: Scores with each size.",
                         fontsize=10)
        prose(page, 108, 504, 720)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 6)]["box"]
        assert box[1] >= 482

    def test_a_table_in_two_ruled_parts_is_whole(self, tmp_path):
        # Captioned above: part (a) with a line of small print and its rules,
        # 12 pt of space, part (b) the same, then prose.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((111, 100), "Table 2: Error of both models on each part.",
                         fontsize=10)
        page.insert_text((111, 116), "(a) The first part is where the value exceeds "
                         "a threshold.", fontsize=7)
        first = ruled_table(page, 190, 420, 122, 2)
        page.insert_text((111, first + 18), "(b) The second part is where the value "
                         "exceeds a threshold.", fontsize=7)
        second = ruled_table(page, 185, 425, first + 24, 2)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[3] >= second

    def test_a_ruled_table_of_paragraphs_keeps_its_last_row(self, tmp_path):
        # Captioned above: rows of question and answer between full rules; each
        # answer is four or more justified lines of 9 pt.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((108, 100), "Table 15: Examples of answers produced by "
                         "the model.", fontsize=10)
        y = 108
        page.draw_line((108, y), (516, y))
        for i in range(3):
            page.insert_text((114, y + 12), f"Question {i}: where would you find "
                             "the answer?", fontsize=9)
            page.insert_textbox(fitz.Rect(114, y + 18, 510, y + 80), PROSE[:340],
                                fontsize=9, align=fitz.TEXT_ALIGN_JUSTIFY)
            y += 80
            page.draw_line((108, y), (516, y))
        prose(page, 72, 540, 650)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 15)]["box"]
        assert box[3] >= y

    def test_header_rows_in_the_captions_block_stay_with_the_table(self, tmp_path):
        # Captions above, each printed in one block with its table's header
        # row under a rule; two such tables stacked, rules of one width.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        y = 100
        tops = []
        for n in (9, 10):
            page.insert_textbox(fitz.Rect(152, y - 12, 469, y + 30),
                                f"Table {n}: Input lengths.\n"
                                "Model   Split   Count   Mean   Max", fontsize=10)
            page.draw_line((143, y + 2.5), (469, y + 2.5))
            page.draw_line((143, y + 18), (469, y + 18))
            tops.append(y + 2.5)
            for i in range(5):
                page.insert_text((152, y + 32 + 11 * i), f"run {i}", fontsize=10)
                page.insert_text((400, y + 32 + 11 * i), f"{1600 + i}", fontsize=10)
            page.draw_line((143, y + 82), (469, y + 82))
            y += 150
        prose(page, 72, 540, 600)
        assert len([b for b in page.get_text("dict")["blocks"]
                    if b["bbox"][1] < 120]) == 1
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        nine, ten = media[("table", 9)]["box"], media[("table", 10)]["box"]
        assert nine[1] <= tops[0] + 1 and nine[3] < tops[1] - 10
        assert ten[1] <= tops[1] + 1 and ten[3] >= tops[1] + 78

    def test_a_header_row_in_another_captions_block_is_no_caption(self):
        # Table 5's caption and header row share one block, a rule between.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_textbox(fitz.Rect(152, 88, 469, 130), "Table 5: Second.\n"
                            "Model   Split   Count   Mean   Max", fontsize=10)
        page.draw_line((143, 102.5), (469, 102.5))
        far, body = (72, 700, 300, 710), pm.body_text(doc)
        assert pm._crop_problem(page, fitz.Rect(140, 101, 480, 125), "table", 4,
                                far, body) is None
        assert pm._crop_problem(page, fitz.Rect(140, 85, 480, 125), "table", 4,
                                far, body) == pm.HOLDS_CAPTION


class TestTablesOfProse:
    """A table captioned below whose rows are a prompt line, a response set as
    body prose between rules, and a line of scores; then a numbered heading and
    a list."""

    def page(self, doc):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((72, 335), (540, 335))
        page.insert_text((78, 347), "Question: how do I do the thing I asked?",
                         fontsize=10)
        page.draw_line((72, 353), (540, 353))
        page.draw_line((72, 426), (540, 426))
        page.insert_text((78, 438), "Scores:", fontsize=10)
        page.insert_text((140, 438), "First = 0.95, Second = 0.20", fontsize=10)
        line = ("Answer: the reply runs across the cell in a full line of ordinary "
                "words.")
        for i in range(5):
            page.insert_text((78, 368 + 12 * i), line, fontsize=10)
        page.draw_line((72, 445), (540, 445))
        page.insert_text((72, 462), "Table 35: Examples where the two scores disagree.",
                         fontsize=10)
        page.insert_text((72, 500), "B.2.3", fontsize=10)
        page.insert_text((110, 500), "Further examples of answers", fontsize=10)
        for i in range(4):
            page.insert_text((97, 524 + 16 * i), f"- item {i}: words and words",
                             fontsize=10)
        prose(page, 72, 540, 720)
        return page

    def test_one_is_reported_not_cut_to_its_last_row(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        self.page(doc)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 35) not in media
        assert [(u["label"], u["problem"]) for u in unrendered] == [
            (35, pm.HOLDS_PROSE)]

    def test_one_row_of_cells_is_no_table(self, tmp_path):
        # A caption between prose and a numbered heading: the heading's two
        # pieces are one row of cells, and nothing else is.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 440)
        page.insert_text((72, 462), "Table 41: Examples of short answers.", fontsize=10)
        page.insert_text((72, 500), "B.2.3", fontsize=10)
        page.insert_text((110, 500), "Further examples of answers", fontsize=10)
        prose(page, 72, 540, 650)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 41) not in media
        assert [(u["label"], u["problem"]) for u in unrendered] == [
            (41, pm.NO_TABLE_BESIDE)]

    def test_a_float_reaching_past_prose_under_the_caption_is_not_stepped_on(
            self, tmp_path):
        # Captioned below across the page, a small box on the caption's last
        # line; under it, prose in the left column and a drawing in the right
        # one, starting 1 pt above the prose.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 80, 530, 120, 6)
        page.insert_textbox(fitz.Rect(LEFT[0], bottom + 4, RIGHT[1], bottom + 40),
                            "Table 1: Ablations of every part of the model, each "
                            "against the default setting on the held-out set "
                            "of the main benchmark.", fontsize=9)
        caption = blocks_starting(page, "Table 1")[0]
        page.draw_rect(fitz.Rect(507, caption[3] - 9, 529, caption[3] + 2),
                       color=(0, 0, 0))
        wall = prose(page, LEFT[0], LEFT[1], caption[3] + 180)
        page.draw_rect(fitz.Rect(RIGHT[0] + 20, wall[1] - 1, RIGHT[1] - 20,
                                 wall[1] + 80), color=(0, 0, 0))
        assert 15 < wall[1] - caption[3] < 40
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[1] <= 120 and box[3] <= caption[1] + 0.5


class TestAGridOnTheColumnLine:
    """A two-column page: a gridded table in the left column whose right border
    is stroked on the column line, in overlapping pieces, with its column's
    prose under it; in the right column a table captioned above, its note, and
    a figure of two plots captioned below."""

    def page(self, doc):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((80, 60), "Table 5: Timing of each part.", fontsize=8)
        rows = [100 + 12 * i for i in range(16)]
        for y in rows:
            page.draw_line((LEFT[0], y), (LEFT[1] + 0.3, y), width=0.4)
            page.insert_text((LEFT[0] + 6, y + 9), "step", fontsize=8)
            page.insert_text((LEFT[1] - 40, y + 9), "1.25", fontsize=8)
            for x in (LEFT[0], LEFT[1] + 0.3):
                page.draw_line((x, y), (x, y + 10), width=0.4)
                page.draw_line((x, y + 5), (x, y + 14), width=0.4)
        prose(page, LEFT[0], LEFT[1], 560)
        page.insert_text((RIGHT[0] + 12, 70), "Table 6: Comparison of strategies.",
                         fontsize=8)
        bottom = ruled_table(page, RIGHT[0] + 12, RIGHT[1], 78, 6)
        page.insert_text((RIGHT[0] + 6, bottom + 9),
                         "First row shows the results without the last step.",
                         fontsize=7)
        for x in (RIGHT[0] + 12, RIGHT[0] + 124):
            page.draw_rect(fitz.Rect(x, 270, x + 110, 400), color=(0, 0, 0))
        page.insert_text((RIGHT[0] + 6, 420), "Figure 13: Comparison of strategies.",
                         fontsize=8)
        return page, bottom

    def test_the_layout_is_the_one_described(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page, _ = self.page(doc)
        wall = max((b["bbox"] for b in page.get_text("dict")["blocks"]
                    if b["bbox"][2] <= LEFT[1] + 1), key=lambda box: box[3])
        caption = blocks_starting(page, "Figure 13")[0]
        assert wall[1] < caption[3] and abs(wall[2] - LEFT[1]) < 1

    def test_the_figure_does_not_climb_its_neighbours_border(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        _, bottom = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 13)]["box"]
        assert box[1] >= bottom + 10 and box[0] >= RIGHT[0]

    def test_the_table_does_not_follow_its_neighbours_border(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        _, bottom = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 6)]["box"]
        assert bottom <= box[3] < 270


class TestATableOverAPlotWithGridlines:
    """A gridded table captioned above, then a framed plot whose gridlines run
    nearly the table's width, captioned below, then prose."""

    def page(self, doc, framed=True):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 4: Results of the experiments.", fontsize=8)
        bottom = ruled_table(page, 150, 450, 76, 6)
        for x in (150, 450):
            page.draw_line((x, 76), (x, bottom))
        top = bottom + 26
        if framed:
            page.draw_rect(fitz.Rect(160, top, 440, top + 82), color=(0, 0, 0))
        for i in range(0 if framed else 1, 7 if framed else 6):
            page.draw_line((160, top + 13.7 * i), (440, top + 13.7 * i), width=0.3)
        for i, tick in enumerate(("0", "5", "10", "15")):
            page.insert_text((160 + 90 * i, top + 92), tick, fontsize=5)
        page.insert_text((150, top + 112), "Figure 9: Results over a long run.",
                         fontsize=8)
        prose(page, 72, 540, 700)
        return bottom, top

    def test_the_table_ends_above_the_plot(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top

    def test_gridlines_with_no_frame_running_into_a_caption_are_no_part(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc, framed=False)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top


class TestATableWrappedInProse:
    """A table at the right of a single-column page, captioned below, with a
    numbered heading and the paragraph under it wrapped round its left side."""

    NARROW = ("To further compare the two methods under the shift",
              "of the inputs, we evaluate both of them on a new set",
              "of articles drawn from another source, using the very",
              "same settings that were found to work best before and",
              "without tuning any of them again for the new inputs at",
              "all, which makes this a fair test of how far each of",
              "the two methods carries over to inputs unlike its own")

    def page(self, doc):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 108, 504, 490)
        page.insert_text((108, 524), "6.3", fontsize=10)
        page.insert_text((130, 524), "Results on another kind of input",
                         fontsize=10)
        for i, line in enumerate(self.NARROW):
            page.insert_text((108, 547 + 12 * i), line, fontsize=10)
        for i in range(3):
            page.insert_text((108, 631 + 12 * i), PROSE[80 * i:80 * i + 80],
                             fontsize=10)
        for y in (523.9, 554.2, 579.4):
            page.draw_line((356, y), (496, y), width=0.5)
        page.draw_line((390, 539.1), (491, 539.1), width=0.3)
        page.insert_text((391, 534), "Rate against the reference", fontsize=9)
        for x, word in ((363, "Run"), (410, "Set A"), (450, "Set B")):
            page.insert_text((x, 549), word, fontsize=9)
        for y, row in ((564, ("Ours", "0.36", "0.31")), (575, ("Base", "0.26", "0.23"))):
            for x, cell in zip((363, 415, 455), row):
                page.insert_text((x, y), cell, fontsize=9)
        page.insert_textbox(fitz.Rect(355, 587, 504, 630),
                            "Table 1: Rates against the reference on "
                            "inputs of another kind.", fontsize=9)
        return page

    def test_the_crop_is_the_table_alone(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[0] >= 348 and box[1] >= 515 and box[3] <= 588

    def test_a_crop_holding_the_wrapped_lines_is_refused(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = self.page(doc)
        caption = blocks_starting(page, "Table 1")[0]
        assert pm._crop_problem(page, fitz.Rect(102, 518, 510, 586), "table", 1,
                                caption, pm.body_text(doc)) == pm.HOLDS_PROSE

    def test_a_table_crop_holding_a_heading_is_refused(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 90), "Results", fontsize=12)
        bottom = ruled_table(page, 150, 450, 100, 4)
        page.insert_text((150, bottom + 14), "Table 2: Scores.", fontsize=9)
        caption = blocks_starting(page, "Table 2")[0]
        body = pm.body_text(doc)
        assert pm._crop_problem(page, fitz.Rect(140, 76, 460, bottom + 3), "table",
                                2, caption, body) == pm.HOLDS_HEADING
        assert pm._crop_problem(page, fitz.Rect(140, 94, 460, bottom + 3), "table",
                                2, caption, body) is None


class TestTheCropIsCheckedLineByLine:

    def test_cells_of_prose_between_rules_are_no_prose(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        y = 108
        page.draw_line((108, y), (516, y))
        for i in range(3):
            for j in range(3):
                page.insert_text((114, y + 14 + 12 * j), PROSE[60 * j:60 * j + 60],
                                 fontsize=10)
            y += 50
            page.draw_line((108, y), (516, y))
        page.insert_text((108, y + 14), "Table 3: Examples of answers.", fontsize=10)
        caption = blocks_starting(page, "Table 3")[0]
        assert pm._crop_problem(page, fitz.Rect(102, 102, 522, y + 3), "table", 3,
                                caption, pm.body_text(doc)) is None

    def test_one_line_of_prose_length_is_no_paragraph(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 90), "Random samples drawn from the model on one set",
                         fontsize=10)
        page.draw_rect(fitz.Rect(150, 100, 460, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 320), "Figure 4: Samples.", fontsize=9)
        caption = blocks_starting(page, "Figure 4")[0]
        assert pm._crop_problem(page, fitz.Rect(140, 76, 470, 306), "figure", 4,
                                caption, pm.body_text(doc)) is None

    def test_two_lines_of_prose_beside_a_figure_are_refused(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(300, 100, 460, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((80, 200), "the paragraph beside the figure runs on and",
                         fontsize=10)
        page.insert_text((80, 212), "on in lines of ordinary words over its side",
                         fontsize=10)
        page.insert_text((300, 320), "Figure 4: Samples.", fontsize=9)
        caption = blocks_starting(page, "Figure 4")[0]
        assert pm._crop_problem(page, fitz.Rect(74, 94, 466, 306), "figure", 4,
                                caption, pm.body_text(doc)) == pm.HOLDS_PROSE

    def test_a_crop_holding_another_captions_table_is_refused(self, tmp_path):
        # Table 7 captioned above its rules, a plot under it, Figure 8's
        # caption under the plot; a crop of Figure 8 reaching up over the table.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 7: Settings.", fontsize=8)
        bottom = ruled_table(page, 150, 450, 76, 4)
        page.draw_rect(fitz.Rect(160, bottom + 30, 440, bottom + 150),
                       color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((150, bottom + 168), "Figure 8: A plot.", fontsize=8)
        caption = blocks_starting(page, "Figure 8")[0]
        body = pm.body_text(doc)
        assert pm._crop_problem(page, fitz.Rect(144, 78, 456, bottom + 156),
                                "figure", 8, caption, body) == pm.HOLDS_FLOAT
        assert pm._crop_problem(page, fitz.Rect(144, bottom + 24, 456,
                                                bottom + 156),
                                "figure", 8, caption, body) is None


def model_card(doc, column_rule_meets_row_rule=True):
    """A table set over a page, continued from the page before: a row whose
    right cell is body prose, a column rule beside it and a row rule under it;
    four short rows; its caption under the last rule; prose."""
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((68, 96), "First Field", fontsize=10)
    page.insert_text((260, 96), "Section 5 describes the analysis.", fontsize=10)
    page.insert_textbox(fitz.Rect(275, 116, 530, 280), PROSE, fontsize=10,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    page.draw_line((239, 87), (239, 285 if column_rule_meets_row_rule else 270),
                   width=0.4)
    y = 287.3
    page.draw_line((62, y), (534, y), width=0.4)
    for i in range(4):
        page.insert_text((68, y + 13), f"Field {i}", fontsize=10)
        page.insert_text((260, y + 13), f"Short entry number {i}.", fontsize=10)
        page.draw_line((239, y + 3), (239, y + 18), width=0.4)
        y += 21
        page.draw_line((62, y), (534, y), width=0.4)
    page.insert_text((62, y + 16), "Table 4: Details of the model, field by field, "
                     "in the order an earlier work sets out.", fontsize=10)
    prose(page, 62, 534, 700)
    return y


class TestATableWithAProseCell:

    def test_the_prose_cell_is_part_of_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        bottom = model_card(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert box[1] <= 87 and bottom <= box[3] <= bottom + 8

    def test_one_not_enclosed_by_its_rules_is_reported_not_cut(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        model_card(doc, column_rule_meets_row_rule=False)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 4) not in media
        assert [(u["label"], u["problem"]) for u in unrendered] == [
            (4, pm.HOLDS_PROSE)]


class TestAFigureUnderATablesNote:
    """A table across the page captioned above, three lines of small print
    under its last rule, then a figure captioned below, then prose."""

    def page(self, doc, note=True, gap=14):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((57, 84), "Table 5: Results on each dataset.", fontsize=9)
        bottom = ruled_table(page, 57, 540, 98, 8, size=7)
        end = bottom
        if note:
            for i in range(3):
                page.insert_text((64, bottom + 7 + 8 * i),
                                 f"note {i}: trained with a larger batch", fontsize=6)
            end = bottom + 25
        page.draw_rect(fitz.Rect(104, end + gap, 492, end + gap + 200),
                       color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((104, end + gap + 218), "Figure 6: Accuracy against size.",
                         fontsize=9)
        prose(page, 72, 540, 700)
        return end

    def test_the_figure_leaves_the_note_to_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        end = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 6)]["box"]
        assert box[1] >= end

    def test_the_padding_stops_at_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        end = self.page(doc, note=False, gap=3)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 6)]["box"]
        assert box[1] >= end


class TestATableAboveACaptionThatIsNotRead:
    """A table captioned below; under its caption a plot whose own caption
    opens with an axis label in the same block, so it is not read as one."""

    def test_the_rows_above_are_the_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        bottom = ruled_table(page, 54, 286, 66, 6)
        page.insert_textbox(fitz.Rect(50, bottom + 7, 287, bottom + 60),
                            "Table 3: Earlier results set against ours, all "
                            "under the same settings.", fontsize=9)
        caption = blocks_starting(page, "Table 3")[0]
        top = caption[3] + 3
        page.draw_rect(fitz.Rect(66, top, 282, top + 100), color=(0, 0, 0))
        for i, tick in enumerate(("0", "200", "400", "600")):
            page.insert_text((66 + 60 * i, top + 110), tick, fontsize=6)
        page.insert_text((140, top + 30), "large", fontsize=6)
        page.insert_text((220, top + 30), "huge", fontsize=6)
        page.insert_textbox(fitz.Rect(50, top + 114, 287, top + 170),
                            "size (M)\nFigure 8: Two ways of training "
                            "compared.", fontsize=9)
        prose(page, 54, 286, 700)
        prose(page, RIGHT[0], RIGHT[1], 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[1] <= 66 and box[3] <= caption[1] + 0.5


class TestATableUnderTheHeadRule:

    def test_its_top_rule_is_not_the_heads(self, tmp_path):
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page(width=LETTER[0], height=LETTER[1])
            page.insert_text((150, 40), "A STUDY OF THINGS", fontsize=9)
            page.draw_line((55, 44), (541, 44), width=1)
            if i == 1:
                bottom = ruled_table(page, 77, 520, 52, 4)
                page.insert_text((77, bottom + 14), "Table 2: Loss functions.",
                                 fontsize=9)
                prose(page, 72, 540, 400)
            else:
                prose(page, 72, 540, 300)
                prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert 45 <= box[1] <= 52


class TestSmallRules:

    def test_a_tie_of_windows_goes_to_the_heavier_size(self):
        assert pm._mode({10.0: 30, 10.2: 40}, 0.3) == 10.2

    def test_the_measure_is_the_widest_window_not_the_widest_bin(self):
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x0, x1, top, text in ((100, 400, 40, PROSE), (100, 402, 200, PROSE),
                                  (100, 350, 360, PROSE + PROSE[:250])):
            assert page.insert_textbox(fitz.Rect(x0, top, x1, top + 300), text,
                                       fontsize=10,
                                       align=fitz.TEXT_ALIGN_JUSTIFY) > 0
        widths = sorted(round(b["bbox"][2] - b["bbox"][0])
                        for b in page.get_text("dict")["blocks"])
        assert widths[0] == 250 and widths[1:] in ([300, 302], [301, 302], [300, 301])
        assert pm._prose_measure(doc, 10.0) in widths[1:]

    def test_a_row_between_rows_of_other_extents_is_none(self):
        cells = [((100, 100, 500, 110), set()), ((100, 122, 200, 132), set())]
        assert not pm._between_rows((150, 111, 190, 121), cells)

    def test_a_row_far_under_the_last_is_none(self):
        cells = [((100, 50, 500, 60), set()), ((100, 122, 500, 132), set())]
        assert not pm._between_rows((150, 111, 450, 121), cells)

    def test_a_drawing_past_a_barrier_does_not_cover_text(self):
        # Upward of the caption: a drawing over a barrier, the barrier, the
        # text, a drawing starting under the text's top.
        text = (100, 300, 300, 310)
        block = {"type": 0, "bbox": text, "lines": [
            {"bbox": text, "spans": [{"size": 10.0, "text": "words of text",
                                      "origin": (100, 308)}]}]}
        parts = pm._Parts()
        parts.texts = [(text, block)]
        parts.barriers = [(100, 250, 300, 260)]
        parts.drawings = [(90, 100, 310, 200), (90, 305, 310, 390)]
        assert pm._table_stops((100, 400, 300, 410), parts, pm.Body(10.0, None),
                               True, []) == [text]


class TestTheChecksDetails:

    def test_the_first_captions_problem_is_reported(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 300)
        page.insert_text((72, 320), "A run-in heading of a few words.", fontsize=9)
        page.insert_text((72, 331), "and then a short line", fontsize=9)
        page.insert_text((72, 350), "Figure 5: A figure set beside its caption.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        page.insert_text((72, 512), "Figure 5: The same label again.", fontsize=9)
        prose(page, 72, 540, 700)
        assert pm.float_crop(page, "figure", 5, body=pm.body_text(doc))[1] == (
            pm.NO_GRAPHICS)

    def test_a_drawing_only_in_the_padding_is_no_graphic(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((100, 101), (300, 101))
        page.insert_text((150, 200), "label", fontsize=8)
        assert pm._crop_problem(page, fitz.Rect(100, 100, 300, 300), "figure", 9,
                                (72, 700, 300, 710), pm.body_text(doc)) == (
            pm.NO_GRAPHICS)

    def test_a_page_background_is_no_graphic(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(page.rect, color=None, fill=(1, 1, 0.95))
        page.insert_text((150, 200), "label", fontsize=8)
        assert pm._crop_problem(page, fitz.Rect(100, 100, 300, 300), "figure", 9,
                                (72, 700, 300, 710), pm.body_text(doc)) == (
            pm.NO_GRAPHICS)


class TestTablesSideBySideOverlappingByAPoint:
    """A table captioned above whose rules run a point into the right column,
    beside the caption of a table set under it in the right column."""

    def page(self, doc):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((118, 80), "Table 1: Results on the small set.", fontsize=9)
        for y in (83, 96, 109, 147, 160, 261):
            page.draw_line((108, y), (344.1, y), width=0.5)
        for i in range(8):
            page.insert_text((114, 120 + 12 * i), f"model {i}", fontsize=7)
            page.insert_text((290, 120 + 12 * i), f"{3 + i * 0.1:.2f}", fontsize=7)
        page.insert_textbox(fitz.Rect(342.8, 91, 503, 141),
                            "Table 2: Ablations of each part of the method, "
                            "with the scores of each.", fontsize=9)
        for y in (142, 160, 200, 241):
            page.draw_line((343.1, y), (504.4, y), width=0.5)
        for i in range(5):
            page.insert_text((349, 172 + 12 * i), f"variant {i}", fontsize=7)
            page.insert_text((470, 172 + 12 * i), f"{7 + i * 0.1:.2f}", fontsize=7)
        prose(page, 108, 504, 420)
        return page

    def test_each_table_is_its_own_captions(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        self.page(doc)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        one, two = media[("table", 1)]["box"], media[("table", 2)]["box"]
        assert one[1] <= 83 and one[3] >= 261 and one[2] <= 350
        assert two[0] >= 343 and two[1] <= 142 and two[3] >= 241


def image_rule(page, x0, x1, y):
    """A rule drawn as a thin raster image."""
    picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 1), False)
    picture.set_rect(picture.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(x0, y, x1, y + 0.8), pixmap=picture,
                      keep_proportion=False)


class TestATableRuledWithImages:

    def test_its_group_rows_of_prose_length_are_its_own(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        y = 60
        image_rule(page, 76, 286, y)
        page.insert_text((82, y + 14), "Model", fontsize=10)
        page.insert_text((240, y + 14), "Score", fontsize=10)
        y += 20
        image_rule(page, 76, 286, y)
        for group in ("Models trained on the first split of the data",
                      "Models trained on the second split of it"):
            page.insert_text((82, y + 14), group, fontsize=10)
            for i in range(3):
                page.insert_text((82, y + 28 + 13 * i), f"model {i}", fontsize=10)
                page.insert_text((240, y + 28 + 13 * i), f"{80 + i}.4", fontsize=10)
            y += 62
            image_rule(page, 76, 286, y)
        page.insert_textbox(fitz.Rect(72, y + 8, 290, y + 60),
                            "Table 6: Results on the reading benchmarks.", fontsize=10)
        prose(page, 72, 290, 600)
        prose(page, RIGHT[0], RIGHT[1], 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 6)]["box"]
        assert box[1] <= 60 and box[3] >= y

    def test_a_header_line_between_its_rules_is_its_own(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        page.insert_text((94, 178), "Table 10. Parameters of the fitted model, each given "
                         "with its mean and its error band", fontsize=10)
        image_rule(page, 90, 514, 194)
        image_rule(page, 90, 514, 196)
        page.insert_text((348, 215), "Mean and error band", fontsize=10)
        image_rule(page, 90, 514, 222)
        y = 240
        for i in range(12):
            page.insert_text((96, y), f"Parameter number {i} of the model", fontsize=10)
            page.insert_text((360, y), f"p{i} = 0.{i}3 +- 0.04", fontsize=10)
            y += 15
        image_rule(page, 90, 514, y - 8)
        page.insert_text((105, y + 10), "a Fit to every data set at once", fontsize=7)
        prose(page, 72, 540, y + 200)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 10)]["box"]
        assert box[1] <= 194 and box[3] >= y - 8

    def test_a_picture_is_no_rule_around_the_print_over_it(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((108, 132), (504, 132))
        for i, line in enumerate(("First Author, Second Author and Third Author",
                                  "Fourth Author, Fifth Author, Sixth Author",
                                  "Seventh Author and Eighth Author, Ninth One",
                                  "Tenth Author and the Eleventh Author of it")):
            page.insert_text((160 + 9 * (i % 2), 164 + 12 * i), line, fontsize=10)
        picture(page, fitz.Rect(108, 213, 504, 361))
        page.insert_text((108, 418), "Figure 1: An overview of the whole system.",
                         fontsize=10)
        prose(page, 108, 504, 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert 200 <= box[1] <= 213


class TestWhatContinuesATable:
    """A table captioned above, then a ruled block as wide, then prose."""

    def page(self, doc, block, caption=True):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 4: Results of the experiments.", fontsize=8)
        bottom = ruled_table(page, 150, 450, 76, 4)
        top = bottom + 22
        block(page, top)
        if caption:
            page.insert_text((150, top + 120), "Figure 9: Results over a long run.",
                             fontsize=8)
        prose(page, 72, 540, 720)
        return bottom, top

    def test_a_framed_plot_with_a_legend_is_no_part(self, tmp_path):
        def plot(page, top):
            page.draw_rect(fitz.Rect(160, top, 440, top + 60), color=(0, 0, 0))
            for i in range(1, 4):
                page.draw_line((160, top + 15 * i), (440, top + 15 * i), width=0.3)
            for i, name in enumerate(("first run", "second run")):
                page.insert_text((160, top + 12 + 15 * i), name, fontsize=8)
                page.insert_text((240, top + 12 + 15 * i), f"{i}.5", fontsize=8)
            page.draw_rect(fitz.Rect(160, top + 66, 440, top + 100), color=(0, 0, 0),
                           fill=(0.2, 0.4, 0.8))
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc, plot)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top

    def test_labelled_gridlines_running_into_a_caption_are_no_part(self, tmp_path):
        def plot(page, top):
            for i in range(5):
                page.draw_line((160, top + 20 * i), (440, top + 20 * i), width=0.3)
            for i in range(3):
                page.insert_text((160, top + 14 + 20 * i), f"run {i}", fontsize=8)
                page.insert_text((300, top + 14 + 20 * i), f"{i}.5", fontsize=8)
            for i, tick in enumerate(("0", "5", "10")):
                page.insert_text((150 + 140 * i, top + 92), tick, fontsize=5)
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc, plot)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top

    def test_an_algorithm_box_is_no_part(self, tmp_path):
        def algorithm(page, top):
            page.draw_line((160, top), (440, top))
            page.draw_line((160, top + 14), (440, top + 14))
            page.insert_text((154, top + 10), "Algorithm 1 Training", fontsize=8)
            for i in range(4):
                page.insert_text((160, top + 26 + 10 * i), f"step {i}: update the "
                                 "weights", fontsize=8)
            page.draw_line((160, top + 60), (440, top + 60))
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc, algorithm, caption=False)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top

    def test_rows_of_other_print_are_no_part(self, tmp_path):
        def grid(page, top):
            page.draw_line((160, top), (440, top))
            for i in range(3):
                page.insert_text((160, top + 9 + 8 * i), f"label {i}", fontsize=5)
                page.insert_text((400, top + 9 + 8 * i), f"{i}", fontsize=5)
            page.draw_line((160, top + 36), (440, top + 36))
        doc = fitz.open()
        body_pages(doc)
        bottom, top = self.page(doc, grid, caption=True)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top


class TestAGridBesideACaptionWithNoTable:

    def test_its_border_is_no_structure(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((80, 60), "Table 5: Timing of each part.", fontsize=8)
        rows = [100 + 12 * i for i in range(22)]
        for y in rows:
            page.draw_line((LEFT[0], y), (LEFT[1] + 0.3, y), width=0.4)
            page.insert_text((LEFT[0] + 6, y + 9), "step", fontsize=8)
            for x in (LEFT[0], LEFT[1] + 0.3):
                page.draw_line((x, y), (x, y + 10), width=0.4)
                page.draw_line((x, y + 5), (x, y + 14), width=0.4)
        prose(page, LEFT[0], LEFT[1], 530)
        prose(page, RIGHT[0], RIGHT[1], 420)
        page.insert_text((RIGHT[0], 442), "Table 6: A caption whose table is elsewhere.",
                         fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 700)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 6) not in media
        assert [(u["label"], u["problem"]) for u in unrendered] == [
            (6, pm.NO_TABLE_BESIDE)]


def test_the_captions_own_lines_are_no_prose_in_its_crop():
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(150, 100, 460, 300), color=(0, 0, 0),
                   fill=(0.2, 0.4, 0.8))
    page.insert_textbox(fitz.Rect(150, 304, 460, 350),
                        "Figure 4: Samples drawn from the model on the first set "
                        "and on the second one, side by side, each of them drawn "
                        "with the same seed.", fontsize=10)
    caption = blocks_starting(page, "Figure 4")[0]
    assert pm._crop_problem(page, fitz.Rect(144, 94, 466, caption[3]), "figure", 4,
                            caption, pm.body_text(doc)) is None


def test_small_print_running_on_past_the_caption_beside_a_figure_is_left_out(tmp_path):
    # A left-column figure; the right column is one long block of small print
    # (a reference list) from above the figure to the foot of the page.
    doc = fitz.open()
    body_pages(doc, pages=4)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(60, 60, 290, 200), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((LEFT[0], 220), "Figure 14: Failure modes of the model.", fontsize=9)
    prose(page, LEFT[0], LEFT[1], 500)
    assert page.insert_textbox(fitz.Rect(RIGHT[0], 50, RIGHT[1], 740),
                               "[4] A. Author and B. Author, A title of a work, "
                               "in a venue, 2015. " * 70, fontsize=7) > 0
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 14)]["box"]
    assert box[2] <= LEFT[1] + pm._PAD and box[1] <= 60


class TestSmallPrintProseInTheOtherColumn:
    """A two-column first page: a 9 pt abstract (body 10 pt) in the left
    column beside a right-column figure, a heading under it."""

    def page(self, doc):
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        abstract = prose(page, LEFT[0], LEFT[1], 400, size=9)
        page.insert_text((LEFT[0], 430), "1. Introduction", fontsize=12)
        page.draw_rect(fitz.Rect(330, 100, 530, 380), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 410), "Figure 1: Results of the experiments.",
                         fontsize=9)
        prose(page, RIGHT[0], RIGHT[1], 700)
        return page, abstract

    def test_the_layout_is_the_one_described(self):
        doc = fitz.open()
        page, abstract = self.page(doc)
        caption = blocks_starting(page, "Figure 1")[0]
        assert 100 < abstract[1] and abstract[3] < caption[1]
        block = [b for b in page.get_text("dict")["blocks"]
                 if tuple(b["bbox"]) == abstract][0]
        body = pm.body_text(doc)
        assert pm._is_barrier(block, body) and not pm._is_prose(block, body)

    def test_the_figure_leaves_the_abstract_out(self, tmp_path):
        doc = fitz.open()
        self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert box[0] >= RIGHT[0] - pm._PAD and box[1] <= 100 and box[3] >= 380

    def test_a_crop_holding_the_abstract_is_refused(self):
        doc = fitz.open()
        page, _ = self.page(doc)
        caption = blocks_starting(page, "Figure 1")[0]
        assert pm._crop_problem(page, fitz.Rect(48, 94, 536, caption[1]), "figure",
                                1, caption, pm.body_text(doc)) == pm.HOLDS_PROSE


def test_heading_size_lines_beside_a_short_caption_are_no_prose():
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(100, 160, 560, 300), color=(0, 0, 0))
    for y in (130, 146):
        page.insert_text((300, y), "the title of the panel at the right side",
                         fontsize=12)
    page.insert_text((100, 320), "Figure 5: A short caption.", fontsize=9)
    caption = blocks_starting(page, "Figure 5")[0]
    assert caption[2] < 300
    assert pm._crop_problem(page, fitz.Rect(94, 94, 566, caption[1]), "figure", 5,
                            caption, pm.body_text(doc)) is None


def test_small_print_across_the_caption_beside_the_plot_is_kept(tmp_path):
    # An 8 pt paragraph (body 10 pt) right of a plot, both over one caption.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(100, 100, 300, 300), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    assert page.insert_textbox(fitz.Rect(320, 150, 520, 270), PROSE[:300], fontsize=8,
                               align=fitz.TEXT_ALIGN_JUSTIFY) > 0
    page.insert_text((100, 320), "Figure 8: A plot and a note on it, set side by side "
                     "under one caption.", fontsize=9)
    prose(page, 72, 540, 700)
    note = [b for b in page.get_text("dict")["blocks"] if b["bbox"][0] >= 320][0]
    assert pm._is_barrier(note, pm.body_text(doc)) and note["bbox"][3] < 300
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 8)]["box"]
    assert box[1] <= 100 and box[2] >= note["bbox"][2]


def test_a_running_heads_row_takes_only_the_lines_beside_it():
    lines = [((175, 56, 420, 64), "A report"), ((122, 61.4, 182, 72.5), "North"),
             ((128, 68.7, 175, 79.9), "extreme highs"),
             ((139, 76.1, 164, 87.2), "Jun-Aug")]
    assert pm._row_at(lines, 0) == (56, 72.5)
    assert pm._row_at(lines, 3) == (68.7, 87.2)


def clipped_fill(page, clip, top):
    """A filled triangle from y `top` down to the bottom of `clip` (x0, y0, x1,
    y1), drawn through `clip` as its clip path."""
    doc, height = page.parent, page.rect.height
    x0, y0, x1, y1 = clip
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, (f"q {x0} {height - y1} {x1 - x0} {y1 - y0} re W n "
                             f"0.7 0.8 0.9 rg {x0} {height - y1} m "
                             f"{x1} {height - top} l {x1} {height - y1} l f Q").encode())
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(
        f"{c} 0 R" for c in page.get_contents() + [xref]) + "]")


class TestAFigureUnderTheRunningHead:
    """Pages under one running head; on the last, a figure whose title rows
    run from the head's row down to the plot."""

    def doc(self, titles=True, plot_top=100):
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page(width=LETTER[0], height=LETTER[1])
            page.insert_text((175, 62), "A REPORT ON WEATHER FORECASTS", fontsize=8)
            prose(page, 72, 540, 700)
        if titles:
            for y in (70, 77.3, 84.7):
                for x in (122, 255, 409):
                    page.insert_text((x, y), "title of a column", fontsize=8)
        page.draw_rect(fitz.Rect(300, plot_top, 520, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((100, 420), "Figure 46: Scores of every region.", fontsize=9)
        return doc

    def test_the_layout_is_the_one_described(self):
        page = self.doc()[-1]
        lines = pm._page_lines(page)
        head, first, last = lines[0][0], lines[1][0], lines[9][0]
        assert first[1] < head[3] and last[1] - head[3] > pm._FURNITURE_GAP
        assert all(b[1] < a[3] for (a, _), (b, _) in zip(lines[1:9], lines[2:10]))

    def test_the_title_rows_are_kept(self, tmp_path):
        box = pm.render_media(save(self.doc(), tmp_path), str(tmp_path / "out"))[
            ("figure", 46)]["box"]
        assert box[1] <= 61.4 and box[3] >= 400

    def test_a_drawing_its_clip_keeps_under_the_band_is_cut_to_the_band(self, tmp_path):
        doc = self.doc(titles=False)
        clipped_fill(doc[-1], (300, 100, 520, 400), 40)
        assert [tuple(d["rect"])[1] for d in doc[-1].get_drawings()] == [100, 40]
        heads, feet = pm.learn_furniture(doc)
        band = pm.furniture_band(doc[-1], heads, feet)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 46)]["box"]
        assert band[0] <= box[1] <= 100

    def test_a_clip_ends_with_its_own_drawing(self, tmp_path):
        # A clipped fill under the caption, then one its clip leaves visible
        # across the band.
        doc = self.doc(titles=False)
        clipped_fill(doc[-1], (300, 500, 520, 600), 500)
        clipped_fill(doc[-1], (300, 60, 520, 400), 60)
        heads, feet = pm.learn_furniture(doc)
        assert pm.furniture_band(doc[-1], heads, feet)[0] > 60
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 46)]["box"]
        assert box[1] <= 60

    def test_the_band_cuts_no_element_across_it(self, tmp_path):
        doc = self.doc(titles=False, plot_top=60)
        heads, feet = pm.learn_furniture(doc)
        band = pm.furniture_band(doc[-1], heads, feet)
        assert 60 < band[0] < 400
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 46)]["box"]
        assert box[1] <= 60


class TestAnAlgorithmAboveAFigure:
    """The right column: a ruled algorithm box, then a figure and its caption;
    prose in the left column."""

    def page(self, doc):
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, LEFT[0], LEFT[1], 400)
        prose(page, LEFT[0], LEFT[1], 700)
        page.draw_line((RIGHT[0], 56), (RIGHT[1], 56))
        page.insert_text((RIGHT[0] + 4, 66), "Algorithm 1: Update rule",
                         fontsize=10)
        page.draw_line((RIGHT[0], 70), (RIGHT[1], 70))
        for i in range(12):
            page.insert_text((RIGHT[0] + 4, 84 + 16 * i), f"{i + 1}  update the state",
                             fontsize=10)
        page.draw_line((RIGHT[0], 282), (RIGHT[1], 282))
        page.draw_rect(fitz.Rect(320, 330, 530, 440), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 470), "Figure 3: Two views of the scene.",
                         fontsize=8)
        prose(page, RIGHT[0], RIGHT[1], 700)
        return page

    def test_the_figure_starts_under_the_algorithm(self, tmp_path):
        doc = fitz.open()
        self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[1] >= 282 and box[1] <= 330 and box[3] >= 440

    def test_a_crop_holding_the_algorithm_is_refused(self):
        doc = fitz.open()
        page = self.page(doc)
        caption = blocks_starting(page, "Figure 3")[0]
        assert pm._crop_problem(page, fitz.Rect(300, 50, 548, caption[1]), "figure",
                                3, caption, pm.body_text(doc)) == pm.HOLDS_FLOAT


_GLYPH_NAMES = {" ": "space", ":": "colon", ".": "period", **{
    str(i): name for i, name in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine"))}}


def descending_caption_page(doc, x, y, text, size):
    """A new page whose first text is `text` at baseline (x, y), set in a Type 3
    font whose metrics end at the baseline while its glyphs, boxes, reach
    `size` / 4 under it."""
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    glyph = doc.get_new_xref()
    doc.update_object(glyph, "<<>>")
    doc.update_stream(glyph, b"500 0 0 -250 450 600 d1 0 -250 450 850 re f")
    names = {c: _GLYPH_NAMES.get(c, c) for c in set(text)}
    font = doc.get_new_xref()
    doc.update_object(font, (
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 700] "
        "/FontMatrix [0.001 0 0 0.001 0 0] /CharProcs << "
        + " ".join(f"/{n} {glyph} 0 R" for n in sorted(set(names.values())))
        + " >> /Encoding << /Differences ["
        + " ".join(f"{ord(c)} /{n}" for c, n in sorted(names.items()))
        + "] >> /FirstChar 32 /LastChar 122 /Widths [" + " ".join(["500"] * 91)
        + "] >>"))
    content = doc.get_new_xref()
    doc.update_object(content, "<<>>")
    doc.update_stream(content, f"BT /T3 {size} Tf {x} {LETTER[1] - y} Td ({text}) Tj "
                      "ET".encode())
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /T3 {font} 0 R >> >>")
    doc.xref_set_key(page.xref, "Contents", f"{content} 0 R")
    return page


class TestACaptionWhoseGlyphsPassItsBox:

    def page(self, doc):
        body_pages(doc)
        page = descending_caption_page(doc, 150, 70, "Table 2: Results of every run.",
                                       8)
        ruled_table(page, 150, 450, 74, 4)
        prose(page, 72, 540, 400)
        return page

    def test_the_layout_is_the_one_described(self):
        doc = fitz.open()
        page = self.page(doc)
        caption = blocks_starting(page, "Table 2")[0]
        assert caption[3] == 70
        pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=fitz.Rect(150, 70, 450, 73),
                              colorspace=fitz.csGRAY)
        ink = [y for y in range(pix.height)
               if min(pix.samples[y * pix.width:(y + 1) * pix.width]) < 128]
        assert ink and 71.9 <= 70 + (ink[-1] + 1) / 4 <= 72.1

    def test_the_crop_starts_under_the_glyphs(self, tmp_path):
        doc = fitz.open()
        self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert 72 <= box[1] <= 73.5


class TestWhatTheR3RulesRequire:

    def test_one_row_of_the_tables_cells_is_no_part(self, tmp_path):
        def row(page, top):
            page.draw_line((160, top), (440, top))
            page.insert_text((160, top + 10), "network 9", fontsize=8)
            page.insert_text((400, top + 10), "4.75", fontsize=8)
            page.draw_line((160, top + 14), (440, top + 14))
        doc = fitz.open()
        body_pages(doc)
        bottom, top = TestWhatContinuesATable().page(doc, row, caption=True)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] < top

    def test_prose_lines_inside_a_frame_are_content(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(150, 100, 460, 300), color=(0, 0, 0))
        for y in (140, 152):
            page.insert_text((156, y), "a sample of the text that the model writes",
                             fontsize=10)
        page.insert_text((150, 320), "Figure 6: Samples of the model.", fontsize=9)
        caption = blocks_starting(page, "Figure 6")[0]
        assert pm._crop_problem(page, fitz.Rect(144, 94, 466, caption[1]), "figure",
                                6, caption, pm.body_text(doc)) is None

    def test_a_rule_in_the_head_bounds_no_prose_lines(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((72, 50), (540, 50))
        for y in (110, 122):
            page.insert_text((80, y), "a line of the prose above the table it opens",
                             fontsize=10)
        page.draw_line((72, 130), (540, 130))
        page.draw_rect(fitz.Rect(80, 140, 520, 300), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((80, 320), "Figure 6: Under two lines of prose.", fontsize=9)
        caption = blocks_starting(page, "Figure 6")[0]
        body = pm.body_text(doc)
        rect = fitz.Rect(74, 94, 526, caption[1])
        assert pm._crop_problem(page, rect, "figure", 6, caption, body,
                                (52, 760)) == pm.HOLDS_PROSE

    def test_one_vertical_rule_makes_no_grid(self):
        box, float_ = (110, 150, 290, 200), (100, 100, 500, 300)
        rule = (299.5, 100, 300.5, 300)
        assert pm._grid_columns(box, [float_], [rule]) == []
        assert pm._grid_columns(box, [float_], [rule, (299.5, 120, 300.5, 180)]) == [
            (rule, float_)]

    def test_a_note_mostly_past_the_float_is_not_its_own(self):
        box = (100, 100, 300, 200)
        assert pm._with_notes(box, [(250, 203, 500, 213)]) == box
        assert pm._with_notes(box, [(100, 203, 290, 213)]) == (100, 100, 300, 213)

    def test_a_float_grazing_the_caption_can_be_foreign(self):
        caption = (100, 300, 300, 310)
        wall = (330, 150, 540, 350)
        parts = pm._Parts()
        parts.drawings = [(100, 200, 400, 200.5), (100, 300.5, 400, 301)]
        parts.barriers, parts.walls = [wall], [wall]
        parts.elements = parts.drawings + [wall]
        assert pm._foreign_floats(caption, parts) == [(100, 200, 400, 301)]
        parts.drawings = [(100, 309, 400, 309.5), (100, 400, 400, 400.5)]
        parts.elements = parts.drawings + [wall]
        assert pm._foreign_floats(caption, parts) == [(100, 309, 400, 400.5)]


def algorithm_box(page, x0, x1, top, bottom, inner=None):
    """A ruled algorithm box: rules at `top`, under its title and at `bottom`,
    numbered lines between, and a rule from x0 to `inner` under its fifth line
    when given."""
    page.draw_line((x0, top), (x1, top))
    page.insert_text((x0 + 4, top + 10), "Algorithm 1: Training", fontsize=8)
    page.draw_line((x0, top + 14), (x1, top + 14))
    y = top + 26
    for i in range(int((bottom - top - 30) // 10)):
        page.insert_text((x0 + 4, y), f"{i + 1}  update the weights", fontsize=8)
        if inner and i == 4:
            page.draw_line((x0, y + 2), (inner, y + 2))
        y += 10
    page.draw_line((x0, bottom), (x1, bottom))


class TestWhatTheR4RulesRequire:

    def test_the_foot_band_cuts_no_table_across_it(self, tmp_path):
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page(width=LETTER[0], height=LETTER[1])
            prose(page, 72, 540, 400)
            page.insert_text((300, 770), f"{i + 1}", fontsize=9)
        page.insert_text((150, 600), "Table 3: Results of every run.", fontsize=8)
        ruled_table(page, 150, 450, 606, 8)
        page.draw_line((300, 606), (300, 762))
        heads, feet = pm.learn_furniture(doc)
        assert 700 < pm.furniture_band(page, heads, feet)[1] < 762
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[3] >= 762

    def test_an_algorithm_box_open_at_the_foot_leaves_the_table_under_it(self, tmp_path):
        # The next rule of the algorithm's extent under its title is Table 2's.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((150, 60), (450, 60))
        page.insert_text((154, 70), "Algorithm 1: Training", fontsize=8)
        page.draw_line((150, 74), (450, 74))
        for i in range(4):
            page.insert_text((154, 86 + 10 * i), f"{i + 1}  update the weights",
                             fontsize=8)
        page.insert_text((150, 150), "Table 2: Results of the experiments.", fontsize=8)
        bottom = ruled_table(page, 150, 450, 156, 4)
        prose(page, 72, 540, 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 156 and box[3] >= bottom

    def test_a_figure_under_an_algorithm_box_open_at_the_foot_is_refused(
            self, tmp_path):
        # The next rule of the algorithm's extent under its title is Table 5's,
        # under Figure 3's caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((150, 60), (450, 60))
        page.insert_text((154, 70), "Algorithm 1: Training", fontsize=8)
        page.draw_line((150, 74), (450, 74))
        for i in range(4):
            page.insert_text((154, 86 + 10 * i), f"{i + 1}  update the weights",
                             fontsize=8)
        page.draw_rect(fitz.Rect(160, 140, 440, 260), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 280), "Figure 3: Under the algorithm.", fontsize=8)
        page.insert_text((150, 300), "Table 5: Under the figure.", fontsize=8)
        ruled_table(page, 150, 450, 306, 4)
        prose(page, 72, 540, 700)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 3) not in media and ("table", 5) in media
        assert [u["label"] for u in unrendered] == [3]

    def table_under(self, page, text_bottom):
        top = text_bottom + 4
        bottom = ruled_table(page, 72, 540, top, 4)
        page.insert_text((72, bottom + 14), "Table 3: Results under the text.",
                         fontsize=9)
        prose(page, 72, 540, 700)
        return top

    def test_a_paragraph_opening_on_an_algorithm_is_no_algorithm_box(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        para = prose(page, 72, 540, 200)
        page.add_redact_annot(fitz.Rect(para))
        page.apply_redactions()
        assert page.insert_textbox(fitz.Rect(para[0], para[1], para[2], para[3] + 40),
                                   "Algorithm 1 summarizes the method. " + PROSE[:280],
                                   fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY) > 0
        block = blocks_starting(page, "Algorithm 1")[0]
        assert pm._is_prose([b for b in page.get_text("dict")["blocks"]
                             if tuple(b["bbox"]) == block][0], pm.body_text(doc))
        top = self.table_under(page, block[3])
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[1] <= top

    def test_a_line_opening_on_an_algorithm_far_over_rules_is_no_algorithm_box(
            self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        page.insert_text((72, 180), "Algorithm 1 converges in a few steps.", fontsize=10)
        top = self.table_under(page, 220)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[1] <= top

    def test_a_shorter_rule_in_the_algorithm_does_not_close_it(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, LEFT[0], LEFT[1], 400)
        prose(page, LEFT[0], LEFT[1], 700)
        algorithm_box(page, RIGHT[0], RIGHT[1], 56, 282, inner=470)
        page.draw_rect(fitz.Rect(320, 330, 530, 440), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 470), "Figure 3: Two views of the scene.",
                         fontsize=8)
        prose(page, RIGHT[0], RIGHT[1], 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert 282 <= box[1] <= 330

    def test_the_padding_stops_at_the_rule_over_the_algorithms_title(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 4: Results of the experiments.", fontsize=8)
        bottom = ruled_table(page, 150, 450, 76, 4)
        algorithm_box(page, 150, 450, bottom + 4, bottom + 80)
        prose(page, 72, 540, 720)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert bottom <= box[3] <= bottom + 4

    def test_an_algorithm_in_the_other_column_does_not_clamp_the_padding(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        algorithm_box(page, LEFT[0], LEFT[1], 100, 287)
        prose(page, LEFT[0], LEFT[1], 700)
        page.draw_rect(fitz.Rect(320, 290, 530, 400), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 420), "Figure 7: A right-column figure.", fontsize=8)
        prose(page, RIGHT[0], RIGHT[1], 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 7)]["box"]
        assert box[1] <= 290 - pm._PAD + 0.5

    def test_a_framed_tables_caption_glyphs_stay_out_of_its_crop(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = descending_caption_page(doc, 160, 70, "Table 2: Results of every run.",
                                       8)
        page.draw_rect(fitz.Rect(140, 55, 470, 160), color=(0, 0, 0))
        ruled_table(page, 150, 450, 76, 4)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert 72 <= box[1] <= 75.5

    def test_the_glyphs_give_way_to_the_tables_print_beside_the_caption(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = descending_caption_page(doc, 150, 70, "Table 2: Results of every run.",
                                       8)
        page.draw_rect(fitz.Rect(400, 71, 410, 75), color=(0, 0, 0), fill=(0, 0, 0))
        ruled_table(page, 150, 450, 76, 4)
        prose(page, 72, 540, 400)
        assert blocks_starting(page, "Table 2")[0][2] < 400
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 71

    def test_the_other_columns_print_does_not_stop_the_glyphs(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = descending_caption_page(doc, LEFT[0], 70,
                                       "Table 2: Results of every run.", 8)
        ruled_table(page, LEFT[0], LEFT[1], 76, 4)
        _paragraph(page, RIGHT[0], RIGHT[1], 71)
        prose(page, LEFT[0], LEFT[1], 400)
        assert 70 < min(b["bbox"][1] for b in page.get_text("dict")["blocks"]
                        if b["bbox"][0] >= RIGHT[0]) < 72
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert 72 <= box[1] <= 76

    def test_glyphs_touching_the_tables_top_rule_keep_the_whole_rule(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = descending_caption_page(doc, 150, 70, "Table 2: Results of every run.",
                                       8)
        ruled_table(page, 150, 450, 72.5, 4)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 72


def heading_over_a_plot(doc, heading):
    """A page of prose ending at y 190, the line `heading(page)` sets at
    baseline y 218, a plot of 5 pt tick labels from y 228 to 420 and its
    caption at y 440; the document's body size is 10 pt."""
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    prose(page, 72, 540, 190)
    heading(page)
    page.draw_rect(fitz.Rect(100, 228, 500, 420), color=(0, 0, 0))
    for i in range(10):
        for j in range(4):
            page.insert_text((110 + 38 * i, 245 + 45 * j), f"0.{i}{j}", fontsize=5)
    page.insert_text((72, 440), "Figure 4: Scores over the second task.", fontsize=9)
    prose(page, 72, 540, 700)
    return page


def small_caps(page):
    page.insert_text((72, 218), "R", fontsize=10)
    page.insert_text((72 + fitz.get_text_length("R", fontsize=10), 218),
                     "ESULTS ON THE SECOND TASK", fontsize=8)


class TestAHeadingAtBodySize:
    """A one-line heading set at body size directly over a plot."""

    HEADINGS = {
        "numbered": lambda page: page.insert_text(
            (72, 218), "A.2 Results on the second task", fontsize=10),
        "numbered apart": lambda page: (
            page.insert_text((72, 218), "A.2", fontsize=10),
            page.insert_text((96, 218), "Results on the second task", fontsize=10)),
        "small caps": small_caps,
    }

    def test_the_layout_is_the_one_described(self):
        for heading in self.HEADINGS.values():
            doc = fitz.open()
            page = heading_over_a_plot(doc, heading)
            lines = [b for b in page.get_text("dict")["blocks"]
                     if 200 < b["bbox"][1] < 228 and b.get("type") == 0]
            assert len(lines) == 1 and 228 - lines[0]["bbox"][3] < pm._NOTE_GAP
            assert not pm._is_barrier(lines[0], pm.Body(10.0 + pm._HEADING_SIZE_DELTA,
                                                         None))

    def test_it_ends_the_figure(self, tmp_path):
        for name, heading in self.HEADINGS.items():
            doc = fitz.open()
            page = heading_over_a_plot(doc, heading)
            line = [b["bbox"] for b in page.get_text("dict")["blocks"]
                    if 200 < b["bbox"][1] < 228][0]
            box = pm.render_media(save(doc, tmp_path), str(tmp_path / name))[
                ("figure", 4)]["box"]
            assert line[3] <= box[1] <= 228 and box[3] >= 420, name


    def test_bold_column_titles_over_a_figure_are_its_own(self, tmp_path):
        doc = fitz.open()
        page = heading_over_a_plot(doc, lambda page: [
            page.insert_text((x, 218), "Human player", fontsize=10, fontname="hebo")
            for x in (110, 310)])
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] <= min(b[1] for b in blocks_starting(page, "Human"))

    def test_a_label_in_capitals_of_one_size_is_its_own(self, tmp_path):
        doc = fitz.open()
        page = heading_over_a_plot(doc, lambda page: page.insert_text(
            (72, 218), "ENCODER OUTPUT", fontsize=10))
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] <= blocks_starting(page, "ENCODER")[0][1]

    def test_a_small_numbered_label_is_its_own(self, tmp_path):
        doc = fitz.open()
        page = heading_over_a_plot(doc, lambda page: page.insert_text(
            (72, 222), "2 Layers", fontsize=5))
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] <= blocks_starting(page, "2 Layers")[0][1]

    def test_a_numbered_row_of_cells_is_no_heading(self):
        for cells in (("1 Baseline", "45.2"), ("1", "Baseline", "45.2")):
            doc = fitz.open()
            page = doc.new_page(width=LETTER[0], height=LETTER[1])
            for i, cell in enumerate(cells):
                page.insert_text((72 + 80 * i, 100), cell, fontsize=10)
            row = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
            assert len(row) == 1 and len(row[0]["lines"]) == len(cells)
            assert pm._SECTION_NUMBER.match(pm._block_text(row[0]))
            assert not pm._is_heading(row[0], pm.Body(10.0, None)), cells

    def test_a_line_in_a_small_capitals_face_is_a_heading(self):
        def line(font):
            return {"lines": [{"bbox": (72, 90, 150, 100), "spans": [
                {"text": "Introduction", "size": 10.0, "font": font, "flags": 4,
                 "origin": (72, 98)}]}]}
        body = pm.Body(10.0, None)
        assert pm._is_heading(line("CMCSC10"), body)
        assert not pm._is_heading(line("CMR10"), body)


def test_a_tick_label_as_the_pages_top_line_stays_whole(tmp_path):
    # Every page opens on a number, so the learned head is a number.
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((300, 40), f"{i + 1}", fontsize=9)
        prose(page, 72, 540, 400)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(88, 38, 300, 120), color=(0.8, 0.8, 0.8))
    page.draw_rect(fitz.Rect(100, 40, 300, 110), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((92, 42), "1", fontsize=5)
    page.insert_text((92, 80), "0", fontsize=5)
    page.insert_text((72, 140), "Figure 1: A plot at the top of the page.", fontsize=9)
    prose(page, 72, 540, 400)
    heads, feet = pm.learn_furniture(doc)
    tick = pm._page_lines(page)[0][0]
    assert heads == {"#"} and tick[3] <= pm.furniture_band(page, heads, feet)[0]
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 1)]["box"]
    assert box[1] <= tick[1] and box[3] >= 120


def test_a_tick_label_touching_the_plot_as_the_pages_top_line_stays_whole(tmp_path):
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((300, 40), f"{i + 1}", fontsize=9)
        prose(page, 72, 540, 400)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(100, 40, 300, 110), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((97, 42), "1", fontsize=5)
    page.insert_text((92, 80), "0", fontsize=5)
    page.insert_text((72, 140), "Figure 1: A plot at the top of the page.", fontsize=9)
    prose(page, 72, 540, 400)
    tick = pm._page_lines(page)[0][0]
    assert 0 < 100 - tick[2] < pm._RULE_TOUCH
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 1)]["box"]
    assert box[1] <= tick[1]


def test_a_tall_tick_label_as_the_pages_top_line_stays_whole(tmp_path):
    # Its box starts 10 pt over the plot it overlaps.
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((300, 40), f"{i + 1}", fontsize=9)
        prose(page, 72, 540, 400)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(88, 40, 300, 120), color=(0.8, 0.8, 0.8))
    page.draw_rect(fitz.Rect(100, 40, 300, 110), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((90, 41), "1", fontsize=10)
    page.insert_text((92, 80), "0", fontsize=5)
    page.insert_text((72, 140), "Figure 1: A plot at the top of the page.", fontsize=9)
    prose(page, 72, 540, 400)
    tick = pm._page_lines(page)[0][0]
    assert 40 - tick[1] > pm._PAD
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 1)]["box"]
    assert box[1] <= tick[1]


def test_a_running_head_across_the_plots_top_is_no_print_of_it(tmp_path):
    doc = TestAFigureUnderTheRunningHead().doc(titles=False, plot_top=60)
    head = pm._page_lines(doc[-1])[0][0]
    assert head[0] < 300 < head[2] and head[3] > 60
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 46)]["box"]
    assert box[0] >= 300 - pm._PAD and box[1] >= 60 - 0.5


def image_rect(page, rect):
    page.insert_image(rect, pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False))


def filled_rect(page, rect):
    page.draw_rect(rect, color=(0, 0, 0), fill=(0.2, 0.4, 0.8))


def test_a_table_stacked_over_a_figure_ends_at_its_last_rule(tmp_path):
    for name, graphic in (("image", image_rect), ("drawing", filled_rect)):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=9)
        bottom = ruled_table(page, 72, 540, 66, 4)
        graphic(page, fitz.Rect(72, bottom + 3, 540, bottom + 130))
        page.insert_text((72, bottom + 142), "Figure 5: Time of every part, drawn.",
                         fontsize=9)
        prose(page, 72, 540, 700)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / name))[
            ("table", 5)]["box"]
        assert bottom <= box[3] <= bottom + 3, name


def test_a_figure_left_short_of_its_frame_rule_keeps_it(tmp_path):
    # A heading whose box ends 0.2 pt under the rule over the plot ends the walk.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((72, 96), "Results", fontsize=14)
    page.draw_line((72, 100), (540, 100))
    page.draw_rect(fitz.Rect(100, 102, 500, 300), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((72, 320), "Figure 2: A plot under a rule.", fontsize=9)
    prose(page, 72, 540, 700)
    heading = blocks_starting(page, "Results")[0]
    assert 100 < heading[3] < 100.5
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 2)]["box"]
    assert box[1] <= 99.5


class TestATableOverAFigure:
    """A table captioned above, stacked over a figure captioned below."""

    def page(self, doc, table):
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=9)
        bottom = table(page)
        filled_rect(page, fitz.Rect(72, bottom + 3, 540, bottom + 130))
        page.insert_text((72, bottom + 142), "Figure 5: Time of every part, drawn.",
                         fontsize=9)
        prose(page, 72, 540, 700)
        return page, bottom

    def test_rows_under_its_header_rules_are_its_own(self, tmp_path):
        def table(page):
            page.draw_line((72, 66), (540, 66))
            page.insert_text((76, 76), "Model", fontsize=8)
            page.insert_text((500, 76), "Error", fontsize=8)
            page.draw_line((72, 79), (540, 79))
            for i in range(4):
                page.insert_text((76, 89 + 10 * i), f"network {i}", fontsize=8)
                page.insert_text((500, 89 + 10 * i), f"{5 + i * 0.37:.2f}", fontsize=8)
            return 121
        doc = fitz.open()
        page, bottom = self.page(doc, table)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 5)]["box"]
        assert 118 <= box[3] <= bottom + 3

    def test_its_note_is_its_own(self, tmp_path):
        def table(page):
            rule = ruled_table(page, 72, 540, 66, 4)
            page.insert_text((76, rule + 9), "* averaged over three runs", fontsize=7)
            return rule + 11
        doc = fitz.open()
        page, bottom = self.page(doc, table)
        note = blocks_starting(page, "* averaged")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 5)]["box"]
        assert note[3] <= box[3] <= bottom + 3


def test_a_ruled_block_past_a_heading_is_no_part(tmp_path):
    # The heading stands left of the caption's x-span, so the walk passes it.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((300, 70), "Table 4: Results.", fontsize=8)
    bottom = ruled_table(page, 150, 450, 76, 4)
    page.insert_text((150, bottom + 20), "Next part", fontsize=14)
    top = bottom + 30
    ruled_table(page, 200, 400, top, 3)
    prose(page, 72, 540, 700)
    caption = blocks_starting(page, "Table 4")[0]
    heading = blocks_starting(page, "Next part")[0]
    assert heading[2] < caption[0]
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 4)]["box"]
    assert bottom <= box[3] < top


class TestTwoRuledBlocksUnderOneCaption:
    """A table captioned above, then a narrower ruled block of three columns."""

    def page(self, doc, between=None, gap=12, size=8):
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((85, 100), "Table 1: Scores on the first (top) and the "
                         "second (bottom) task.", fontsize=9)
        first = ruled_table(page, 85, 511, 106, 4)
        if between:
            between(page, first)
        second = ruled_table(page, 135, 462, first + gap, 4, size=size)
        for i in range(5):
            page.insert_text((300, first + gap + 2 * size + 7 + (size + 2) * i),
                             f"{i}", fontsize=size)
        prose(page, 72, 540, 700)
        return first, second

    def test_the_layout_is_the_one_described(self):
        doc = fitz.open()
        self.page(doc)
        floats = pm._ruled_floats([tuple(d["rect"]) for d in doc[-1].get_drawings()])
        assert len(floats) == 2
        a, b = sorted(floats, key=lambda f: f[1])
        assert min(a[2], b[2]) - max(a[0], b[0]) < pm._SAME_EXTENT * (a[2] - a[0])
        assert b[1] - a[3] > pm._NOTE_GAP

    def test_the_second_block_joins_the_table(self, tmp_path):
        doc = fitz.open()
        first, second = self.page(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 1)]["box"]
        assert box[1] <= 106 and box[3] >= second

    def test_one_row_of_the_tables_cells_joins_it(self, tmp_path):
        def row(page, top):
            page.draw_line((160, top), (440, top))
            page.insert_text((160, top + 10), "network 9", fontsize=8)
            page.insert_text((400, top + 10), "4.75", fontsize=8)
            page.draw_line((160, top + 14), (440, top + 14))
        doc = fitz.open()
        body_pages(doc)
        bottom, top = TestWhatContinuesATable().page(doc, row, caption=False)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 4)]["box"]
        assert box[3] >= top + 14

    def refused(self, doc, tmp_path):
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("table", 1) not in media and ("table", 4) not in media
        return [u["problem"] for u in unrendered]

    def test_a_graphic_between_them_refuses_the_crop(self, tmp_path):
        doc = fitz.open()
        self.page(doc, lambda page, first: filled_rect(
            page, fitz.Rect(140, first + 10, 460, first + 20)), gap=30)
        assert self.refused(doc, tmp_path) == [pm.RUNS_ON]

    def test_a_block_of_other_print_refuses_the_crop(self, tmp_path):
        def grid(page, top):
            page.draw_line((160, top), (440, top))
            for i in range(3):
                page.insert_text((160, top + 9 + 8 * i), f"label {i}", fontsize=5)
                page.insert_text((400, top + 9 + 8 * i), f"{i}", fontsize=5)
            page.draw_line((160, top + 36), (440, top + 36))
        doc = fitz.open()
        body_pages(doc)
        TestWhatContinuesATable().page(doc, grid, caption=False)
        assert self.refused(doc, tmp_path) == [pm.RUNS_ON]


def test_a_table_running_off_the_page_reaches_the_page_edge(tmp_path):
    a4 = (595.3, 841.9)
    doc = fitz.open()
    for _ in range(2):
        prose(doc.new_page(width=a4[0], height=a4[1]), 72, 520, 700)
    page = doc.new_page(width=a4[0], height=a4[1])
    for y in (85, 105, 150):
        page.draw_line((62, y), (636, y))
    head = "Score with the longest prompt "
    page.insert_text((592 - fitz.get_text_length(head, fontsize=10), 98), head + "W",
                     fontsize=10)
    for i in range(4):
        page.insert_text((70, 118 + 10 * i), f"model {i}", fontsize=8)
        page.insert_text((300, 118 + 10 * i), f"{40 + i}.5", fontsize=8)
    page.insert_text((62, 165), "Table 16: Accuracy under four formulations.", fontsize=9)
    prose(page, 72, 520, 500)
    line = blocks_starting(page, "Score with")[0]
    assert line[2] > a4[0] + 2
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 16)]["box"]
    assert abs(box[2] - a4[0]) < 0.5


def clipped_bar(page, clip, x, top):
    """A 0.2 pt wide bar at `x` from y `top` down to the bottom of `clip`
    (x0, y0, x1, y1), drawn through `clip` as its clip path."""
    doc, height = page.parent, page.rect.height
    x0, y0, x1, y1 = clip
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, (f"q {x0} {height - y1} {x1 - x0} {y1 - y0} re W n "
                             f"0.2 0.4 0.8 rg {x} {height - y1} 0.2 {y1 - top} re f Q"
                             ).encode())
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(
        f"{c} 0 R" for c in page.get_contents() + [xref]) + "]")


def test_bars_clipped_on_the_page_leave_the_running_head_out(tmp_path):
    # Their paths start 600 pt over the page; their clip ends them at y 60.
    doc = fitz.open()
    two_sided(doc, 4, 60)
    for x in (200, 250, 300):
        clipped_bar(doc[-1], (150, 60, 460, 250), x, -600)
    assert min(d["rect"].y0 for d in doc[-1].get_drawings()) == -600
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 4)]["box"]
    assert box[1] >= 48.5


def clipped_line(page, clip, x0, x1, y):
    """A 0.4 pt high bar from x `x0` to `x1` at y `y`, drawn through `clip`
    (x0, y0, x1, y1) as its clip path."""
    doc, height = page.parent, page.rect.height
    cx0, cy0, cx1, cy1 = clip
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, (f"q {cx0} {height - cy1} {cx1 - cx0} {cy1 - cy0} re W n "
                             f"0 0 0 rg {x0} {height - y - 0.4} {x1 - x0} 0.4 re f Q"
                             ).encode())
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(
        f"{c} 0 R" for c in page.get_contents() + [xref]) + "]")


def test_a_gridline_clipped_on_the_page_is_its_visible_part(tmp_path):
    # Its path starts 600 pt left of the page; its clip leaves x 130-450.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    prose(page, 72, 540, 80)
    page.draw_rect(fitz.Rect(150, 100, 450, 300), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    clipped_line(page, (130, 100, 450, 300), -600, 450, 200)
    page.insert_text((150, 320), "Figure 6: A plot with a gridline.", fontsize=9)
    prose(page, 72, 540, 700)
    assert min(d["rect"].x0 for d in page.get_drawings()) == -600
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 6)]["box"]
    assert box[0] <= 130 and box[1] >= 80


class TestWhatTheR5RulesRequire:

    def test_a_booktabs_table_headed_algorithm_is_a_table(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 70), "Table 3: Error of each method.", fontsize=8)
        page.draw_line((150, 76), (450, 76))
        page.insert_text((154, 86), "Algorithm", fontsize=8)
        page.insert_text((410, 86), "Error", fontsize=8)
        page.draw_line((150, 89), (450, 89))
        for i in range(4):
            page.insert_text((154, 99 + 10 * i), f"network {i}", fontsize=8)
            page.insert_text((410, 99 + 10 * i), f"{5 + i * 0.37:.2f}", fontsize=8)
        page.draw_line((150, 133), (450, 133))
        prose(page, 72, 540, 400)
        assert blocks_starting(page, "Algorithm")
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 3)]["box"]
        assert box[1] <= 76 and box[3] >= 133

    def test_a_rule_beside_an_algorithms_title_does_not_close_it(self, tmp_path):
        # The left table's header rule is the first rule under the title.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((LEFT[0], 94), "Table 2: Results.", fontsize=8)
        bottom = ruled_table(page, LEFT[0], LEFT[1], 100, 4)
        algorithm_box(page, RIGHT[0], RIGHT[1], 100, 287)
        prose(page, LEFT[0], LEFT[1], 700)
        prose(page, RIGHT[0], RIGHT[1], 700)
        title = blocks_starting(page, "Algorithm 1")[0]
        assert title[3] - 1 <= 113 < 114
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= 100 and box[3] >= bottom

    def algorithms(self, page, caption_box=(0, 0, 1, 1)):
        doc = page.parent
        return pm._algorithms(page.get_text("dict")["blocks"],
                              pm._page_rules(page), pm.body_text(doc), caption_box)

    def test_an_algorithm_with_no_rule_over_its_title_starts_at_the_title(self):
        # A rule of its extent stands 20 pt over the title.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((150, 100), (450, 100))
        page.insert_text((154, 130), "Algorithm 1: Training", fontsize=8)
        page.draw_line((150, 134), (450, 134))
        page.insert_text((154, 146), "1  update the weights", fontsize=8)
        page.draw_line((150, 150), (450, 150))
        title = blocks_starting(page, "Algorithm 1")[0]
        assert self.algorithms(page) == [(150, title[1], 450, 150)]

    def test_stacked_algorithms_close_at_their_own_rules(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        algorithm_box(page, 150, 450, 100, 160)
        algorithm_box(page, 150, 450, 170, 230)
        assert self.algorithms(page) == [(150, 100, 450, 160), (150, 170, 450, 230)]

    def test_an_algorithm_running_over_a_caption_ends_at_its_top(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_line((150, 60), (450, 60))
        page.insert_text((154, 70), "Algorithm 1: Training", fontsize=8)
        page.draw_line((150, 74), (450, 74))
        page.insert_text((154, 86), "1  update the weights", fontsize=8)
        page.draw_line((150, 200), (450, 200))
        caption = (150, 150, 300, 160)
        assert self.algorithms(page, caption) == [(150, 60, 450, 150)]

    def test_ragged_small_print_beside_the_caption_is_no_prose(self):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(100, 100, 300, 300), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((320, 150), "a long line of legend text that runs on and on "
                         "past forty characters", fontsize=6)
        page.insert_text((320, 158), "a shorter line of legend text, forty-odd chars",
                         fontsize=6)
        page.insert_text((100, 320), "Figure 5: A short caption.", fontsize=9)
        caption = blocks_starting(page, "Figure 5")[0]
        note = [b for b in page.get_text("dict")["blocks"] if b["bbox"][0] >= 320][0]
        assert not pm._is_barrier(note, pm.body_text(doc))
        assert all(n >= pm._PROSE_LINE_CHARS for _, n in pm._lines(note))
        assert pm._crop_problem(page, fitz.Rect(94, 94, 566, caption[1]), "figure", 5,
                                caption, pm.body_text(doc)) is None

    def test_the_glyphs_give_way_to_small_print_beside_the_caption(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = descending_caption_page(doc, 150, 70, "Table 2: Results of every run.",
                                       8)
        assert page.insert_textbox(fitz.Rect(300, 70.5, 420, 122), PROSE[:115],
                                   fontsize=8, align=fitz.TEXT_ALIGN_JUSTIFY) > 0
        ruled_table(page, 150, 450, 124, 4)
        prose(page, 72, 540, 400)
        caption = blocks_starting(page, "Table 2")[0]
        note = [b for b in page.get_text("dict")["blocks"] if b["bbox"][0] >= 300][0]
        body = pm.body_text(doc)
        assert pm._is_barrier(note, body) and not pm._is_prose(note, body)
        assert caption[2] < 300 and caption[3] < note["bbox"][1] < 72
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert box[1] <= note["bbox"][1]


def test_a_fully_clipped_hairline_is_no_rule_between_caption_lines(tmp_path):
    # Figure 6's caption is one block of two lines; a hairline whose clip path
    # leaves none of it visible lies between them, over Figure 7's plot.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(100, 60, 500, 180), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    assert page.insert_textbox(fitz.Rect(100, 190, 500, 230), "Figure 6: Scores of "
                               "every run on the first task, drawn over every step "
                               "of the training of each network, with the mean of "
                               "the three seeds as a line.", fontsize=9) > 0
    first, second = [ln["bbox"] for ln in blocks_with(page, "Figure 6")[0]["lines"]]
    clipped_line(page, (0, 0, 5, 5), 100, 480, (first[3] + second[1]) / 2 - 0.2)
    page.draw_rect(fitz.Rect(100, 222, 500, 380), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((100, 400), "Figure 7: Scores on the second task.", fontsize=9)
    prose(page, 72, 540, 700)
    rule = [d["rect"] for d in page.get_drawings() if d["rect"].height < 1][0]
    assert rule.y0 >= first[3] - 0.5 and rule.y1 <= second[1] + 0.5
    assert second[3] < 222
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("figure", 7)]["box"]
    assert box[1] >= second[3] - 0.5 and box[3] >= 380


def test_every_rule_lies_in_a_path_box_of_the_bbox_log():
    # Rules stroked thin and wide, filled, and half clipped.
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_line((72, 100), (540, 100), width=0.4)
    page.draw_line((72, 140), (540, 140), width=2)
    page.draw_rect(fitz.Rect(72, 180, 540, 180.5), color=None, fill=(0, 0, 0))
    clipped_line(page, (72, 200, 300, 240), 72, 540, 220)
    page = fitz.open("pdf", doc.tobytes())[0]
    rules = pm._page_rules(page)
    paths = [r for t, r in page.get_bboxlog() if t.endswith("path")]
    assert len(rules) == 4
    assert all(any(b[0] <= r[0] and b[1] <= r[1] and b[2] >= r[2] and b[3] >= r[3]
                   for b in paths) for r in rules)


def test_rules_are_not_read_for_a_band_no_path_meets(monkeypatch):
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_line((72, 100), (540, 100), width=0.4)
    rules = pm._Rules(page)
    monkeypatch.setattr(pm, "_page_rules", lambda page: pytest.fail("rules read"))
    assert not rules.between(300, 310, 72, 540)
    monkeypatch.undo()
    assert rules.between(99, 101, 72, 540)


def test_a_rule_in_another_column_is_not_between_lines_of_this_one():
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(72, 80, 290, 130), color=(0, 0, 0))
    page.draw_line((320, 105), (540, 105), width=0.4)
    rules = pm._Rules(page)
    assert not rules.between(100, 110, 72, 290)
    assert rules.between(100, 110, 320, 540)


@pytest.mark.parametrize("rule_y", [(59.6, 60.0), (100.0, 100.4)])
def test_a_rule_at_the_edge_of_the_band_is_between(rule_y):
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(72, rule_y[0], 540, rule_y[1]), color=None, fill=(0, 0, 0))
    assert pm._Rules(page).between(60, 100, 72, 540)


@pytest.mark.parametrize("rule_y", [(59.6, 59.7), (100.3, 100.4)])
def test_a_rule_just_outside_the_band_is_between(rule_y):
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(72, rule_y[0], 540, rule_y[1]), color=None, fill=(0, 0, 0))
    assert pm._Rules(page).between(60, 100, 72, 540)


def blocks_with(page, text):
    """The text blocks one of whose lines starts with `text`."""
    return [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0
            and any("".join(s["text"] for s in ln["spans"]).startswith(text)
                    for ln in b["lines"])]


def test_a_table_whose_caption_shares_its_block_is_no_part_of_the_next(tmp_path):
    # Table 1, narrower than Table 2, has its caption line as the last line of
    # its cells' block.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    first = ruled_table(page, 200, 400, 100, 4)
    page.insert_text((200, first + 8), "Table 1: Symbols of the model.", fontsize=8)
    top = first + 30
    second = ruled_table(page, 150, 450, top, 4)
    page.insert_text((200, second + 14), "Table 2: Terms of the reward.", fontsize=8)
    prose(page, 72, 540, 700)
    merged = blocks_with(page, "Table 1")[0]
    assert len(merged["lines"]) > 1 and not blocks_starting(page, "Table 1")
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 2)]["box"]
    assert merged["bbox"][3] - 0.5 <= box[1] <= top and box[3] >= second


def test_a_table_stacked_over_labelled_images_ends_at_its_last_rule(tmp_path):
    # A row of panel titles 12 pt under the table, each running 1 pt into the
    # top of its image, and tick labels beside the images.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=9)
    bottom = ruled_table(page, 72, 540, 66, 4)
    for x in (100, 330):
        page.insert_text((x + 40, bottom + 20), "dim = 10", fontsize=6)
        image_rect(page, fitz.Rect(x, bottom + 21, x + 180, bottom + 121))
        for i in range(4):
            page.insert_text((x - 10, bottom + 30 + 25 * i), f"0.{i}", fontsize=5)
    page.insert_text((72, bottom + 135), "Figure 5: Time of every part, drawn.",
                     fontsize=9)
    prose(page, 72, 540, 700)
    title = blocks_starting(page, "dim")[0]
    image = [b["bbox"] for b in page.get_text("dict")["blocks"]
             if b.get("type") == 1][0]
    assert pm._NOTE_GAP < title[1] - bottom < 2 * pm._NOTE_GAP
    assert image[1] < title[3]
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 5)]["box"]
    assert bottom <= box[3] < title[1]


def test_a_graphic_left_out_above_a_table_stops_its_padding(tmp_path):
    # Table 3 is captioned above a drawing that ends 3 pt over Table 4.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 3: Time of every part, drawn.", fontsize=9)
    filled_rect(page, fitz.Rect(72, 66, 540, 197))
    bottom = ruled_table(page, 72, 540, 200, 4)
    page.insert_text((150, bottom + 14), "Table 4: Time of every part.", fontsize=9)
    prose(page, 72, 540, 700)
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 4)]["box"]
    assert 197 <= box[1] <= 200 and box[3] >= bottom


def test_one_small_capitals_word_does_not_make_a_line_a_heading():
    line = {"lines": [{"bbox": (72, 90, 250, 100), "spans": [
        {"text": "Results of the ", "size": 10.0, "font": "CMR10", "flags": 4,
         "origin": (72, 98)},
        {"text": "Net", "size": 10.0, "font": "CMCSC10", "flags": 4,
         "origin": (150, 98)},
        {"text": " model on the second task", "size": 10.0, "font": "CMR10",
         "flags": 4, "origin": (170, 98)}]}]}
    assert not pm._is_heading(line, pm.Body(10.0, None))


def test_a_fully_clipped_hairline_under_a_running_head_is_no_head_rule(tmp_path):
    # Its clip path leaves none of it visible; a marker of the plot's lies
    # between it and the head.
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 40), "A STUDY OF THINGS", fontsize=9)
        clipped_line(page, (0, 0, 5, 5), 72, 540, 54.5)
        filled_rect(page, fitz.Rect(140, 49, 147, 53))
        page.draw_rect(fitz.Rect(150, 58, 460, 250), color=(0, 0, 0),
                       fill=(0.2, 0.4, 0.8))
        page.insert_text((150, 266), f"Figure {i + 1}: A figure on page {i + 1}.",
                         fontsize=9)
        prose(page, 72, 540, 700)
    media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
    assert all(media[("figure", n)]["box"][1] <= 49 for n in (1, 2, 3, 4))


def test_a_prose_line_opening_on_a_label_is_no_caption(tmp_path):
    # The paragraph under the table has a later line opening on `Table 7.`.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=9)
    rule = ruled_table(page, 72, 540, 66, 4)
    page.insert_textbox(fitz.Rect(72, rule + 14, 540, 500),
                        PROSE + "\nTable 7. " + PROSE, fontsize=10,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    paragraph = blocks_with(page, "Table 7")[0]
    assert pm._is_prose(paragraph, pm.body_text(doc))
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 5)]["box"]
    assert rule <= box[3] < paragraph["bbox"][1]


def test_a_tables_note_sharing_a_block_with_the_next_caption_is_its_own(tmp_path):
    # Table 5's note and Table 6's caption, under it, are one block.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=8)
    rule = ruled_table(page, 72, 540, 66, 4)
    page.insert_text((76, rule + 9), "* averaged over three runs", fontsize=8)
    page.insert_text((150, rule + 20.5), "Table 6: Time of every step.", fontsize=8)
    ruled_table(page, 72, 540, rule + 30, 4)
    prose(page, 72, 540, 700)
    shared = blocks_with(page, "Table 6")[0]
    note = shared["lines"][0]["bbox"]
    assert pm._block_text(shared).startswith("* averaged")
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 5)]["box"]
    assert note[3] <= box[3] <= shared["lines"][1]["bbox"][1] + 0.5


def test_a_note_with_an_icon_beside_it_stays_with_an_unclaimed_table(tmp_path):
    # An image starts beside the note's line; prose, no caption, follows.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 5: Time of every part.", fontsize=9)
    rule = ruled_table(page, 72, 540, 66, 4)
    page.insert_text((90, rule + 9), "averaged over three runs", fontsize=7)
    note = blocks_starting(page, "averaged")[0]
    image_rect(page, fitz.Rect(76, note[3] - 4, 86, note[3] + 4))
    prose(page, 72, 540, 400)
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 5)]["box"]
    assert box[3] >= note[3]


def test_a_table_under_labelled_images_starts_at_its_first_rule(tmp_path):
    # Table 3 is captioned over two images, each with a title under it that
    # its image runs 1 pt into; Table 4 is captioned below.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_text((150, 60), "Table 3: Samples of every part.", fontsize=9)
    for x in (100, 330):
        image_rect(page, fitz.Rect(x, 70, x + 180, 170))
        page.insert_text((x + 60, 175), "dim = 10", fontsize=6)
    title = blocks_starting(page, "dim")[0]
    top = title[3] + 12
    bottom = ruled_table(page, 72, 540, top, 4)
    page.insert_text((150, bottom + 14), "Table 4: Time of every part.", fontsize=9)
    prose(page, 72, 540, 700)
    image = [b["bbox"] for b in page.get_text("dict")["blocks"]
             if b.get("type") == 1][0]
    assert image[3] > title[1]
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 4)]["box"]
    assert title[3] < box[1] <= top and box[3] >= bottom


def test_cells_citing_floats_are_no_captions(tmp_path):
    # The middle cell of each row cites `Table A1`...; captioned below.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_line((72, 100), (540, 100))
    for i in range(4):
        y = 112 + 10 * i
        page.insert_text((76, y), f"network {i}", fontsize=8)
        page.insert_text((300, y), f"Table A{i + 1}", fontsize=8)
        page.insert_text((500, y), f"{5 + i * 0.37:.2f}", fontsize=8)
    page.draw_line((72, 145), (540, 145))
    page.insert_text((150, 159), "Table 12: Rates of every change.", fontsize=9)
    prose(page, 72, 540, 700)
    cells = blocks_with(page, "Table A2")[0]
    assert pm._block_text(cells).startswith("network 0")
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 12)]["box"]
    assert box[1] <= 100 and box[3] >= 145


def test_a_prose_line_opening_on_a_label_does_not_cut_the_table_under_it(tmp_path):
    # The paragraph ends 4 pt over the table; a later line of it opens on
    # `Table 7:`. Captioned below.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    page.insert_textbox(fitz.Rect(72, 60, 540, 296), PROSE + "\nTable 7: " + PROSE[:200],
                        fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
    paragraph = blocks_with(page, "Table 7")[0]
    top = paragraph["bbox"][3] + 4
    bottom = ruled_table(page, 72, 540, top, 4)
    page.insert_text((150, bottom + 14), "Table 5: Time of every part.", fontsize=9)
    prose(page, 72, 540, 700)
    assert pm._is_prose(paragraph, pm.body_text(doc))
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 5)]["box"]
    assert paragraph["bbox"][3] - 0.5 <= box[1] <= top and box[3] >= bottom


def test_a_figure_over_a_tables_caption_does_not_cut_its_note(tmp_path):
    # Figure 3 over Table 1's caption; a note under the table, then Figure 4,
    # captioned below, claims what follows.
    doc = fitz.open()
    body_pages(doc)
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    filled_rect(page, fitz.Rect(150, 60, 460, 160))
    page.insert_text((150, 175), "Figure 3: Samples of every part.", fontsize=9)
    page.insert_text((150, 200), "Table 1: Comparison of accuracy.", fontsize=8)
    bottom = ruled_table(page, 150, 460, 206, 4)
    page.insert_text((150, bottom + 9), "* on the test split", fontsize=7)
    filled_rect(page, fitz.Rect(150, bottom + 30, 460, bottom + 150))
    page.insert_text((150, bottom + 165), "Figure 4: Trajectories of both runs.",
                     fontsize=9)
    prose(page, 72, 540, 700)
    note = blocks_starting(page, "*")[0]
    box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
        ("table", 1)]["box"]
    assert note[3] <= box[3] < bottom + 30


def plot_in_a_backdrop(page, backdrop_bottom):
    """A plot over a white backdrop drawn from y 211 to `backdrop_bottom`, its
    axis title at 16 pt, and Figure 3's caption at y 592."""
    page.draw_rect(fitz.Rect(54, 211, 432, backdrop_bottom), color=None,
                   fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(111, 219, 550, 532), color=(0, 0, 0))
    for i, tick in enumerate(("0.0", "0.4", "0.8", "1.2", "1.6")):
        page.insert_text((105 + 110 * i, 541), tick, fontsize=9)
    page.insert_text((276, 568), "Radius (units)", fontsize=16)
    page.insert_text((255, 592), "Figure 3: Values over the radius.", fontsize=10)


class TestABackdropPastTheCaption:

    def test_the_plot_over_it_is_cropped(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        plot_in_a_backdrop(page, 749)
        caption = blocks_starting(page, "Figure 3")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        title = blocks_starting(page, "Radius (units)")[0]
        assert pm._is_heading(blocks_with(page, "Radius (units)")[0], pm.body_text(doc))
        assert box[1] <= 219 and title[3] <= box[3] <= caption[1]
        assert box[0] <= 111 and box[2] >= 550

    def test_it_does_not_carry_the_crop_over_prose_it_holds(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(54, 60, 560, 749), color=None, fill=(1, 1, 1))
        prose(page, 72, 540, 200)
        filled_rect(page, fitz.Rect(111, 219, 550, 532))
        page.insert_text((255, 552), "Figure 3: Values over the radius.",
                         fontsize=10)
        caption = blocks_starting(page, "Figure 3")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert 200 <= box[1] <= 219 and box[3] <= caption[1]

    def test_print_over_the_figure_on_it_is_left_out(self, tmp_path):
        # A title block over the figure, at heading size, on the same backdrop.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(41, 111, 557, 498), color=None, fill=(1, 1, 1))
        page.insert_text((116, 146), "First Author and Other Authors", fontsize=12)
        page.insert_text((163, 171), "A Laboratory of Some University", fontsize=12)
        picture(page, fitz.Rect(50, 185, 545, 380))
        page.insert_text((50, 396), "Figure 1: Results on several tasks.",
                         fontsize=10)
        lab = blocks_starting(page, "A Laboratory")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert lab[3] - 0.5 <= box[1] <= 185

    @pytest.mark.parametrize("backdrop", [(0, 0, 612, 792), (54, 292, 560, 749)],
                             ids=["page-background", "under-the-heading"])
    def test_a_heading_off_a_backdrop_still_ends_the_figure(self, tmp_path,
                                                            backdrop):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(backdrop), color=None, fill=(1, 1, 1))
        prose(page, 72, 540, 250)
        page.insert_text((72, 282), "3 Results", fontsize=16)
        filled_rect(page, fitz.Rect(111, 300, 550, 500))
        page.insert_text((255, 520), "Figure 3: Values over the radius.",
                         fontsize=10)
        heading = blocks_starting(page, "3 Results")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert heading[3] - 0.5 <= box[1] <= 300


    @pytest.mark.parametrize("drawn", [False, True], ids=["image", "drawing"])
    def test_a_heading_between_two_figures_on_it_ends_the_second(self, tmp_path,
                                                                drawn):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(40, 60, 572, 740), color=None, fill=(1, 1, 1))
        (filled_rect if drawn else picture)(page, fitz.Rect(100, 80, 500, 250))
        page.insert_text((100, 270), "Figure 1: The first result.", fontsize=10)
        page.insert_text((72, 305), "4 Discussion", fontsize=16)
        picture(page, fitz.Rect(100, 320, 500, 500))
        page.insert_text((100, 515), "Figure 2: The second result.", fontsize=10)
        prose(page, 72, 540, 700)
        heading = blocks_starting(page, "4 Discussion")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert heading[3] - 0.5 <= box[1] <= 320

    def test_one_past_a_caption_over_a_heading_crops_no_heading(self, tmp_path):
        # Figure 1's backdrop runs on past its caption over a heading and Figure 2.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(54, 70, 560, 749), color=None, fill=(1, 1, 1))
        filled_rect(page, fitz.Rect(100, 80, 500, 260))
        page.insert_text((250, 290), "Radius (units)", fontsize=16)
        page.insert_text((150, 312), "Figure 1: Values over the radius.", fontsize=10)
        page.insert_text((72, 350), "3 Results", fontsize=16)
        picture(page, fitz.Rect(100, 365, 500, 560))
        page.insert_text((150, 575), "Figure 2: A picture under the heading.",
                         fontsize=10)
        prose(page, 72, 540, 720)
        heading = blocks_starting(page, "3 Results")[0]
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert ("figure", 2) in media
        assert not any(pm._overlaps(m["box"], heading) for m in media.values())

    def test_a_page_background_carries_no_crop_over_a_heading(self, tmp_path):
        # A picture, a heading and Figure 3 on a white page, with no prose.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.draw_rect(fitz.Rect(0, 0, 612, 792), color=None, fill=(1, 1, 1))
        picture(page, fitz.Rect(111, 80, 550, 250))
        page.insert_text((72, 282), "3 Results", fontsize=16)
        filled_rect(page, fitz.Rect(111, 300, 550, 500))
        page.insert_text((100, 520), "Figure 3: Values over the radius.",
                         fontsize=10)
        heading = blocks_starting(page, "3 Results")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert heading[3] - 0.5 <= box[1] <= 300


def picture(page, rect):
    """A raster image filling `rect`."""
    page.insert_image(rect, keep_proportion=False,
                      pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False))


def image_grid(page, rows, top=90):
    """Rows of two images, each under its 10 pt title from `rows` and over a
    9 pt score line, and Figure 8's caption under them; returns the caption's
    box."""
    y = top
    for titles in rows:
        for x, title in zip((60, 310), titles):
            page.insert_text((x + 4, y + 8), title, fontsize=10, fontname="tiro")
            picture(page, fitz.Rect(x, y + 11, x + 230, y + 129))
            page.insert_text((x + 80, y + 139), "score 1.00 steps 100", fontsize=9)
        y += 150
    page.insert_text((108, y + 10), "Figure 8: A grid of samples.",
                     fontsize=10)
    return blocks_starting(page, "Figure 8")[0]


class TestTitlesInAnImageGrid:

    ROWS = (("Deterministic sampler with the first setting",
             "Deterministic sampler with the second setting"),
            ("Stochastic sampler with the first setting",
             "Stochastic sampler with the second setting"))

    def test_the_titles_of_its_panels_are_no_prose(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        caption = image_grid(page, self.ROWS)
        body = pm.body_text(doc)
        assert len([box for box in pm._prose_lines(
            page.get_text("dict")["blocks"], body) if box[3] < caption[1]]) == 4
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 8)]["box"]
        assert box[1] <= 90 and 369 <= box[3] <= caption[1]

    def test_prose_wider_than_the_grid_between_its_rows_is_refused(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        caption = image_grid(page, (("", ""), ("", "")))
        for y in (232, 244):
            page.insert_text((40, y), PROSE[:100], fontsize=10)
        assert pm._crop_problem(page, fitz.Rect(34, 84, 560, 375), "figure", 8,
                                caption, pm.body_text(doc)) == pm.HOLDS_PROSE

    def test_prose_over_a_lone_panel_is_refused(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        picture(page, fitz.Rect(60, 311, 290, 429))
        for y in (296, 308):
            page.insert_text((60, y), PROSE[:44], fontsize=10)
        page.insert_text((60, 445), "Figure 8: One panel.", fontsize=10)
        caption = blocks_starting(page, "Figure 8")[0]
        assert pm._crop_problem(page, fitz.Rect(54, 282, 296, 435), "figure", 8,
                                caption, pm.body_text(doc)) == pm.HOLDS_PROSE


    ROW = ((60, 311, 290, 429), (310, 311, 540, 429))

    @pytest.mark.parametrize("box,images,others,printed", [
        ((60, 298, 290, 308), ROW[:1], [], False),
        ((40, 298, 560, 308), ROW, [], False),
        ((60, 431, 290, 441), ROW, [], True),
        ((60, 431, 290, 441), ROW, [(60, 444, 290, 454)], False),
        ((60, 298, 290, 308), ROW, [(310, 310, 540, 320)], True),
    ], ids=["over-a-lone-image", "wider-than-its-image", "under-an-image-of-a-row",
            "over-another-line", "beside-a-line-in-the-next-column"])
    def test_a_line_by_a_panel_is_its_print(self, box, images, others, printed):
        assert pm._in_image_grid(box, images, [box] + others) is printed

    def test_titles_between_the_rows_of_one_column_of_panels_are_no_prose(
            self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for y, title in zip((90, 240, 390), ("",) + self.ROWS[0]):
            page.insert_text((64, y - 4), title, fontsize=10, fontname="tiro")
            picture(page, fitz.Rect(60, y, 290, y + 118))
        page.insert_text((60, 525), "Figure 8: A column of samples.", fontsize=10)
        caption = blocks_starting(page, "Figure 8")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 8)]["box"]
        assert box[1] <= 90 and 508 <= box[3] <= caption[1]

    def test_two_line_titles_between_its_rows_are_no_prose(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x, first, second in zip((60, 310), *self.ROWS):
            picture(page, fitz.Rect(x, 90, x + 230, 208))
            page.insert_text((x + 4, 224), first, fontsize=10, fontname="tiro")
            page.insert_text((x + 4, 236), second, fontsize=10, fontname="tiro")
            picture(page, fitz.Rect(x, 240, x + 230, 358))
        page.insert_text((108, 375), "Figure 8: A grid of samples.", fontsize=10)
        caption = blocks_starting(page, "Figure 8")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 8)]["box"]
        assert box[1] <= 90 and 358 <= box[3] <= caption[1]

    def test_prose_between_the_rows_of_two_figures_is_left_out(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (60, 310):
            picture(page, fitz.Rect(x, 80, x + 230, 200))
            picture(page, fitz.Rect(x, 262, x + 230, 380))
        page.insert_text((100, 215), "Figure 1: Two panels.", fontsize=10)
        for y in (240, 252):
            page.insert_text((60, y), PROSE[:95], fontsize=10)
        page.insert_text((100, 395), "Figure 2: Two more panels.", fontsize=10)
        prose(page, 60, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[1] <= 262 and box[3] >= 380

    def test_a_paragraphs_tail_over_a_row_is_left_out(self, tmp_path):
        # Two columns of prose, the left one's last two lines over a row of two
        # images across both columns.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        for x in (54, 306):
            for top, bottom in ((60, 280), (510, 740)):
                page.insert_textbox(fitz.Rect(x, top, x + 236, bottom), PROSE,
                                    fontsize=10, align=fitz.TEXT_ALIGN_JUSTIFY)
            picture(page, fitz.Rect(x, 320, x + 236, 480))
        page.insert_text((54, 300), PROSE[:44], fontsize=10)
        page.insert_text((54, 312), PROSE[44:88], fontsize=10)
        page.insert_text((200, 495), "Figure 3: Two panels across both columns.",
                         fontsize=10)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[1] <= 320 and box[3] >= 480


class TestSubcaptionParagraphs:

    def page(self, doc, size, marks=("(a) ", "(b) "), backdrop=False):
        """Two images over 7-line paragraphs at `size` opening on `marks`,
        Figure 11's caption under them and a paragraph of prose over them; all
        but the prose on a grey backdrop when `backdrop`."""
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 108, 504, 439)
        if backdrop:
            page.draw_rect(fitz.Rect(100, 445, 512, 770), color=None,
                           fill=(0.93, 0.93, 0.93))
        for x, mark in zip((117, 317), marks):
            picture(page, fitz.Rect(x, 455, x + 178, 633))
            assert page.insert_textbox(fitz.Rect(x, 638, x + 178, 738),
                                       mark + PROSE[:220], fontsize=size,
                                       align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
        page.insert_text((220, 752), "Figure 11: Two panels and their subcaptions.",
                         fontsize=10)
        return page

    def test_they_belong_to_the_figure(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = self.page(doc, 9)
        caption = blocks_starting(page, "Figure 11")[0]
        assert any(pm._is_barrier(b, pm.body_text(doc))
                   for b in page.get_text("dict")["blocks"]
                   if b.get("type") == 0 and pm._block_text(b).startswith("(a)"))
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 11)]["box"]
        assert 439 <= box[1] <= 455 and 715 <= box[3] <= caption[1]

    def test_ones_on_the_figure_s_backdrop_belong_to_it(self, tmp_path):
        # Each is print on the backdrop and a subcaption both.
        doc = fitz.open()
        body_pages(doc)
        page = self.page(doc, 9, backdrop=True)
        caption = blocks_starting(page, "Figure 11")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 11)]["box"]
        assert 439 <= box[1] <= 455 and 715 <= box[3] <= caption[1]

    @pytest.mark.parametrize("panel", [(117, 320, 495, 420), (317, 330, 495, 445)],
                             ids=["far-over-them", "beside-them"])
    def test_away_from_the_panels_they_are_no_subcaptions(self, tmp_path, panel):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 108, 504, 300)
        picture(page, fitz.Rect(panel))
        assert page.insert_textbox(fitz.Rect(117, 450, 295, 550),
                                   f"(a) {PROSE[:220]}", fontsize=9,
                                   align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
        page.insert_text((220, 565), "Figure 11: Two panels and their subcaptions.",
                         fontsize=10)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 11) not in media
        assert [u["problem"] for u in unrendered] == [pm.NOTHING_BESIDE]

    @pytest.mark.parametrize("size,marks", [(10, ("(a) ", "(b) ")), (9, ("", ""))],
                             ids=["at-body-size", "with-no-mark"])
    def test_unmarked_or_body_size_paragraphs_are_no_subcaptions(
            self, tmp_path, size, marks):
        doc = fitz.open()
        body_pages(doc)
        self.page(doc, size, marks)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 11) not in media
        assert [u["problem"] for u in unrendered] == [pm.NOTHING_BESIDE]

    def test_small_print_under_a_rule_over_a_figure_is_none(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 90), "Results", fontsize=10)
        page.draw_line((72, 93), (540, 93), color=(0, 0, 0), width=0.5)
        assert page.insert_textbox(fitz.Rect(72, 98, 540, 160), f"(a) {PROSE[:300]}",
                                   fontsize=8, align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
        picture(page, fitz.Rect(100, 175, 500, 360))
        page.insert_text((100, 375), "Figure 2: A figure under small print.",
                         fontsize=10)
        prose(page, 72, 540, 700)
        note = blocks_starting(page, "(a)")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert note[3] - 0.5 <= box[1] <= 175

    def test_a_tables_note_over_a_figure_is_none(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((60, 78), "Table 1: Measured values of the model.",
                         fontsize=10)
        bottom = ruled_table(page, 60, 540, 86, 5, size=10)
        assert page.insert_textbox(fitz.Rect(60, bottom + 8, 540, bottom + 48),
                                   f"(a) {PROSE[:260]}", fontsize=8,
                                   align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
        picture(page, fitz.Rect(100, bottom + 56, 500, bottom + 236))
        page.insert_text((100, bottom + 251), "Figure 1: A figure under the table.",
                         fontsize=10)
        prose(page, 60, 540, 720)
        note = blocks_starting(page, "(a)")[0]
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert note[3] - 0.5 <= box[1] <= bottom + 56

    def test_a_table_is_never_cut_to_its_last_row_of_subtables(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 120)
        y = 140
        for spans, marks, column_rule in (
                (((72, 292), (320, 540)), ("(a) ", "(b) "), False),
                (((72, 250), (280, 540)), ("(c) ", "(d) "), True)):
            for (x0, x1), mark in zip(spans, marks):
                bottom = ruled_table(page, x0, x1, y, 3)
                if column_rule:
                    page.draw_line((x0 + 110, bottom - 10), (x0 + 110, bottom))
                assert page.insert_textbox(fitz.Rect(x0, bottom + 4, x1, bottom + 70),
                                           mark + PROSE[:220], fontsize=8,
                                           align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
            y = bottom + 75
        under = max(b[3] for b in blocks_starting(page, "(c)") + blocks_starting(page, "(d)"))
        page.insert_text((95, under + 18), "Table 2: Ablations of every part, trained "
                         "on one set and tested on another one.", fontsize=10)
        prose(page, 72, 540, 700)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert ("table", 2) not in media or media[("table", 2)]["box"][1] <= 140


def prose_line(page, x0, x1, y, size=10, start=0):
    """One line of PROSE's words, from its `start`th on, as many as fit x0..x1,
    at baseline `y`."""
    words = PROSE.split()
    text = ""
    for word in words[start:] + words:
        if fitz.get_text_length(f"{text} {word}", fontsize=size) > x1 - x0:
            break
        text = f"{text} {word}".strip()
    page.insert_text((x0, y), text, fontsize=size)


def held_lines(page, box):
    """Text of each printed line (`pm._lines`) of the page whose middle lies in
    `box`."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for line, _ in pm._lines(b) if b.get("type") == 0 else ():
            if (box[0] < (line[0] + line[2]) / 2 < box[2]
                    and box[1] < (line[1] + line[3]) / 2 < box[3]):
                out.append(" ".join(s["text"] for ln in b["lines"]
                                    if pm._contains(line, tuple(ln["bbox"]))
                                    for s in ln["spans"]))
    return out


def plot(page, rect):
    page.draw_rect(fitz.Rect(rect), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))


class TestTheCropTakesNoBodyText:
    """A line of body text outside a float's graphics ends its crop."""

    def test_a_figure_beside_the_text_leaves_the_paragraph_out(self, tmp_path):
        # One block: a full-width line, then lines wrapped beside the figure.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose_line(page, 72, 540, 100)
        for i in range(7):
            prose_line(page, 72, 230, 112 + 12 * i, start=3 * i)
        plot(page, (250, 106, 540, 180))
        page.insert_text((250, 194), "Figure 2: A plot set beside the text.", fontsize=9)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[0] <= 250 and box[1] <= 106 and box[2] >= 540 and box[3] >= 180

    @pytest.mark.parametrize("into", [2, 4])
    def test_a_small_print_legend_reaching_into_the_plot_stays(self, tmp_path, into):
        # A 9 pt legend line across the column, its box `into` pt into the plot.
        legend = ("Proposed method   Baseline with replay   Baseline without replay   "
                  "Oracle policy   Random policy   Expert data")
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 100)
        width = fitz.get_text_length(legend, fontsize=9)
        page.insert_text((306 - width / 2, 150 + into - 2.7), legend, fontsize=9)
        plot(page, (76, 150, 536, 320))
        page.insert_text((76, 336), "Figure 2: A plot under its legend.", fontsize=9)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert any(line.startswith("Proposed method") for line in held_lines(doc[1], box))

    def test_a_crop_its_body_text_cannot_all_be_cut_from_is_refused(self, tmp_path):
        # One block: a full-width line between a rule and the figure, then lines
        # wrapped beside the figure.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 90)
        page.draw_rect(fitz.Rect(300, 104, 330, 105), color=(0, 0, 0), fill=(0, 0, 0))
        prose_line(page, 72, 540, 120)
        for i in range(8):
            prose_line(page, 72, 262, 132 + 12 * i, start=3 * i)
        plot(page, (280, 126, 540, 220))
        page.insert_text((280, 234), "Figure 2: A plot set beside the text.", fontsize=9)
        prose(page, 72, 540, 400)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 2) not in media
        assert [u["problem"] for u in unrendered] == [pm.HOLDS_PROSE]

    def test_a_refused_crop_with_lines_beside_its_figure_stays_refused(self, tmp_path):
        # A display equation over the plot; one block of lines wrapped beside it
        # running on full width under its caption.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 90)
        for x, piece in ((150, "x = a + b"), (330, "J(A) - J(B)"), (500, "(5)")):
            page.insert_text((x, 116), piece, fontsize=10)
        for i in range(10):
            prose_line(page, 72, 262, 136 + 12 * i, start=3 * i)
        for i in range(2):
            prose_line(page, 72, 540, 256 + 12 * i, start=5 * i)
        plot(page, (280, 128, 540, 200))
        page.insert_text((280, 214), "Figure 4: A plot set beside the text, with",
                         fontsize=9)
        page.insert_text((280, 224), "a caption of two lines.", fontsize=9)
        prose(page, 72, 540, 500)
        unrendered = []
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"),
                                unrendered=unrendered)
        assert ("figure", 4) not in media
        assert [u["problem"] for u in unrendered] == [pm.HOLDS_PROSE]

    def test_a_title_line_on_a_lone_image_stays(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        page.insert_text((180, 186), "Outputs of the model for one fixed input seed",
                         fontsize=10)
        picture(page, fitz.Rect(120, 189, 492, 400))
        page.insert_text((120, 416), "Figure 7: Samples of the model.", fontsize=9)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 7)]["box"]
        assert box[1] <= 176

    def test_a_long_rotated_axis_title_stays(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        page.insert_text((100, 380), "Share of the episodes that end at the goal",
                         fontsize=10, rotate=90)
        plot(page, (110, 170, 500, 390))
        page.insert_text((110, 406), "Figure 3: A plot with a long axis title.",
                         fontsize=9)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 3)]["box"]
        assert box[0] <= 90

    def test_a_teaser_leaves_the_title_block_out(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((150, 80), "A Title Set Large Over the Teaser", fontsize=17)
        page.insert_text((160, 108), "Ann Author, Ben Builder, Cy Coder and Di Debugger",
                         fontsize=10)
        page.insert_text((200, 122), "One University, Two Institute", fontsize=9)
        plot(page, (72, 140, 540, 300))
        page.insert_text((72, 316), "Figure 1: A teaser under the title block.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        body_pages(doc)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 1)]["box"]
        assert held_lines(doc[0], box) == []
        assert box[1] <= 140 and box[3] >= 300

    def test_small_print_spanning_the_column_over_a_figure_is_left_out(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 80), "A.2 Further results", fontsize=12)
        prose_line(page, 72, 540, 100, size=9)
        prose_line(page, 72, 300, 110, size=9, start=5)
        plot(page, (120, 126, 500, 300))
        page.insert_text((120, 316), "Figure 4: A plot under a short paragraph.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[1] <= 126

    def test_a_label_opening_a_line_under_prose_is_no_caption_of_what_is_above(
            self, tmp_path):
        # "Figure 3. The ..." opens a block under a line of prose and an equation;
        # Figure 3 itself is on the next page.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 300)
        page.insert_text((200, 400), "x = a + b", fontsize=10)
        page.draw_rect(fitz.Rect(180, 409, 330, 410), color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text((200, 424), "c - d", fontsize=10)
        prose_line(page, 72, 540, 440)
        page.insert_text((72, 456), "Figure 3. The curve splits the range in two.",
                         fontsize=10)
        prose(page, 72, 540, 600)
        assert len(blocks_starting(page, "Figure 3.")) == 1
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        plot(page, (150, 100, 460, 250))
        page.insert_text((150, 266), "Figure 3: A plot.", fontsize=9)
        prose(page, 72, 540, 500)
        media = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))
        assert media[("figure", 3)]["page"] == 3

    def test_subcaptions_at_body_size_under_the_panels_stay(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 150)
        for x0, subcaption in ((72, "(a) Without the first part of the method"),
                               (310, "(b) With it")):
            plot(page, (x0, 170, x0 + 220, 300))
            page.insert_text((x0, 314), subcaption, fontsize=10)
        page.insert_text((72, 334), "Figure 4: Two panels with their subcaptions.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert box[1] <= 170 and box[3] >= 316

    def test_a_figure_s_own_title_wider_than_the_column_stays(self, tmp_path):
        doc = fitz.open()
        for _ in range(2):
            body = doc.new_page(width=LETTER[0], height=LETTER[1])
            for x0, x1 in (LEFT, RIGHT):
                for y in (250, 500, 750):
                    prose(body, x0, x1, y)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((110, 100), "What the method guarantees, and what it leaves "
                         "to the solver it calls", fontsize=9)
        for x0 in (60, 310):
            plot(page, (x0, 110, x0 + 230, 300))
        page.insert_text((60, 316), "Figure 5: A figure across both columns.", fontsize=9)
        for x0, x1 in (LEFT, RIGHT):
            prose(page, x0, x1, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 5)]["box"]
        assert box[1] <= 92

    @pytest.mark.parametrize("tail, title_x, frame_x0, frame_y0", [
        ("it.", 100, 160, 142), ("of the column", 92, 160, 142),
        ("of the column and then it ends", 92, 175, 142),
        ("of the column", 92, 160, 135.7), ("of the column", 92, 160, 133.2),
    ], ids=["short-of-the-axis-title", "over-the-axis-title", "over-the-frame",
            "into-the-frame-s-top", "further-into-the-frame-s-top"])
    def test_a_paragraph_s_last_line_over_a_plot_cuts_off_none_of_its_side(
            self, tmp_path, tail, title_x, frame_x0, frame_y0):
        # The paragraph's short last line, its box 123.3..137.0, stands over the
        # plot's frame or reaches less than half its height into the frame's
        # top; the plot's tick labels and rotated axis title stand left of the
        # frame.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 100)
        prose_line(page, 72, 540, 122)
        page.insert_text((72, 134), tail, fontsize=10)
        plot(page, (frame_x0, frame_y0, 520, frame_y0 + 160))
        for i, tick in enumerate(("1.0", "0.5", "0.0")):
            page.insert_text((140, frame_y0 + 8 + 76 * i), tick, fontsize=8)
        page.insert_text((title_x, frame_y0 + 130), "Success rate", fontsize=9,
                         rotate=90)
        page.insert_text((frame_x0, frame_y0 + 178),
                         "Figure 2: A plot under a paragraph.", fontsize=9)
        prose(page, 72, 540, 620)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert sorted(held_lines(doc[1], box)) == ["0.0", "0.5", "1.0", "Success rate"]
        assert box[0] <= 72 and box[1] <= frame_y0

    @pytest.mark.parametrize("caption_y", [276, 266],
                             ids=["over-the-caption", "within-a-line-gap-over-it"])
    def test_a_line_across_the_column_under_an_image_row_stays(self, tmp_path,
                                                                caption_y):
        prompt = ("Prompt: a red kettle on a wooden table by the open window in "
                  "the soft morning light, film grain, close up")
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 110)
        for i in range(4):
            picture(page, fitz.Rect(74 + 117 * i, 130, 184 + 117 * i, 240))
        page.insert_text((72, 256), prompt, fontsize=10)
        page.insert_text((74, caption_y), "Figure 7: Samples for one prompt.",
                         fontsize=9)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 7)]["box"]
        assert held_lines(doc[1], box) == [prompt]

    @pytest.mark.parametrize("x, line, plot_x", [
        (80, "Proposed method   Baseline with replay   Baseline without replay   "
             "Oracle policy", (100, 520)),
        (96, "Return of every method over the whole training budget", (100, 520)),
        (185.4, "Return of every method over the whole training budget", (236, 376)),
    ], ids=["legend-past-the-plot-s-edge", "title-past-the-plot-s-edge",
            "title-centred-over-a-narrow-plot"])
    def test_a_lone_line_over_the_plot_stays(self, tmp_path, x, line, plot_x):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 120)
        page.insert_text((x, 150), line, fontsize=10)
        plot(page, (plot_x[0], 156, plot_x[1], 330))
        page.insert_text((plot_x[0], 346), "Figure 2: A plot under one line.", fontsize=9)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == [line]

    def test_a_lone_line_over_the_legend_of_a_plot_is_left_out(self, tmp_path):
        # The line's box ends 17 pt over the plot, the legend's 2.6 pt over it.
        legend = "ours   baseline   oracle"
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 120)
        page.insert_text((185.4, 150), "Return of every method over the whole "
                         "training budget", fontsize=10)
        page.insert_text((260, 165), legend, fontsize=8)
        plot(page, (236, 170, 376, 330))
        page.insert_text((236, 346), "Figure 2: A plot under one line.", fontsize=9)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == [legend]

    def test_a_title_of_two_lines_over_the_plot_is_left_out(self, tmp_path):
        # The lines' boxes end 11.5 pt and 0.5 pt over the plot.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 110)
        for y, line in ((141.5, "Return of every method over the whole training"),
                        (152.5, "budget, each one averaged over five seeds")):
            width = fitz.get_text_length(line, fontsize=10)
            page.insert_text((310 - width / 2, y), line, fontsize=10)
        plot(page, (100, 156, 520, 330))
        page.insert_text((100, 346), "Figure 2: A plot under two lines.", fontsize=9)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[1] <= 156

    @pytest.mark.parametrize("centred", [True, False],
                             ids=["title-centred-on-the-row", "one-line-paragraph"])
    def test_a_lone_line_over_a_row_of_plots_stays_centred_on_the_row(
            self, tmp_path, centred):
        title = "Return of every method over the whole training budget"
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 110)
        if centred:
            width = fitz.get_text_length(title, fontsize=10)
            page.insert_text((306 - width / 2, 140), title, fontsize=10)
        else:
            prose_line(page, 72, 470, 140)
        for x0 in (72, 312):
            plot(page, (x0, 148, x0 + 228, 300))
        page.insert_text((72, 316), "Figure 2: Every method over training.", fontsize=9)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == ([title] if centred else [])
        assert box[1] <= 148

    @pytest.mark.parametrize("across, caption_y", [(False, 330), (True, 334)],
                             ids=["narrower-than-the-column", "past-a-line-gap"])
    def test_a_line_over_a_body_size_caption_it_does_not_go_on_stays(
            self, tmp_path, across, caption_y):
        # The line's box ends 2.3 pt over the caption's, or 6.3 pt.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 120)
        plot(page, (150, 140, 460, 300))
        if across:
            prose_line(page, 72, 540, 314)
        else:
            page.insert_text((150, 314), "The note under the plot runs on for most "
                             "of the column", fontsize=10)
        page.insert_text((150, caption_y), "Figure 2: A plot over a note.", fontsize=10)
        prose(page, 72, 540, 560)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert len(held_lines(doc[1], box)) == 1

    def test_a_one_line_paragraph_over_a_plot_is_left_out(self, tmp_path):
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 100)
        prose_line(page, 72, 470, 122)
        plot(page, (160, 130, 520, 290))
        page.insert_text((160, 306), "Figure 2: A plot under a short paragraph.",
                         fontsize=9)
        prose(page, 72, 540, 600)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert held_lines(doc[1], box) == []
        assert box[1] <= 130

    def test_a_table_under_a_line_across_the_column_over_its_caption_is_rendered(
            self, tmp_path):
        note = ("Scores are averaged over five seeds, and the steps count all the "
                "frames of the training environment.")
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        prose(page, 72, 540, 120)
        bottom = ruled_table(page, 110, 500, 140, 3)
        page.insert_text((72, bottom + 12), note, fontsize=10)
        page.insert_text((150, bottom + 24), "Table 2: Results on the benchmark suite.",
                         fontsize=9)
        prose(page, 72, 540, 400)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("table", 2)]["box"]
        assert note in held_lines(doc[1], box)

    def test_a_cut_that_leaves_a_body_line_inside_gives_the_crop_back(self, tmp_path):
        # Small print over two stacked plots, a line of body text across the
        # column between them.
        doc = fitz.open()
        body_pages(doc)
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_text((72, 80), "A.2 Further results", fontsize=12)
        prose_line(page, 72, 540, 100, size=9)
        prose_line(page, 72, 300, 110, size=9, start=5)
        plot(page, (120, 126, 500, 200))
        prose_line(page, 72, 540, 214)
        plot(page, (120, 222, 500, 300))
        page.insert_text((120, 316), "Figure 4: Two plots and a line between them.",
                         fontsize=9)
        prose(page, 72, 540, 500)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 4)]["box"]
        assert len(held_lines(doc[1], box)) == 3


def test_a_float_s_content_holds_no_body_line():
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    prose(page, 72, 540, 200)
    body = pm.Body(10, 468)
    printed = pm._printed(page.get_text("dict")["blocks"], body)
    rest = (lambda box: False, [(72, 300, 540, 400)], [])
    assert len(pm._body_lines(printed, body, lambda box: False, *rest)) == len(printed)
    assert pm._body_lines(printed, body, lambda box: True, *rest) == []


def test_prose_three_tenths_off_the_body_size_is_body_prose_line_by_line():
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    prose(page, 72, 540, 200)
    lines = pm._prose_lines(page.get_text("dict")["blocks"], pm.Body(10.3, 468))
    assert len(lines) == len(page.get_text("dict")["blocks"][0]["lines"])


def test_prose_three_tenths_off_the_body_size_is_body_prose():
    # The size whose window holds the most print is 10.3 pt, 0.3 pt over the
    # prose at 10 pt.
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    for size, bottom in ((10.0, 200), (10.0, 380), (10.3, 520), (10.5, 660)):
        prose(page, 72, 540, bottom, size=size)
    body = pm.body_text(doc)
    assert abs(body.size - 10.3) < 1e-9
    assert [pm._is_prose(b, body) for b in page.get_text("dict")["blocks"]] == [True] * 4


class TestReadOnce:
    """`ReadOnce` holds the blocks the page's own read does, image data aside."""

    def doc(self, tmp_path):
        """Body pages, a grid of titled images, and two images over subcaptions on
        a backdrop."""
        doc = fitz.open()
        body_pages(doc)
        image_grid(doc.new_page(width=LETTER[0], height=LETTER[1]),
                   TestTitlesInAnImageGrid.ROWS)
        TestSubcaptionParagraphs().page(doc, 9, backdrop=True)
        return fitz.open(save(doc, tmp_path))

    def test_its_pages_crop_what_the_document_does(self, tmp_path):
        doc = self.doc(tmp_path)
        crops = [(page.number, crops) for page, crops in pm.float_crops(doc)]
        assert [(n, c[:2]) for n, cs in crops for c in cs if c[2] is not None] == [
            (1, ("figure", 8)), (2, ("figure", 11))]
        assert [(page.number, crops) for page, crops
                in pm.float_crops([pm.ReadOnce(p) for p in doc])] == crops

    def test_its_dict_is_the_page_s_with_each_image_its_number_and_box(self, tmp_path):
        for page in self.doc(tmp_path):
            own = page.get_text("dict")
            blocks = [b if b["type"] == 0 else {k: b[k] for k in ("number", "type", "bbox")}
                      for b in own["blocks"]]
            assert pm.ReadOnce(page).get_text("dict") == dict(own, blocks=blocks)

    def test_a_read_whose_lines_differ_gives_the_page_s_own_dict(self, tmp_path,
                                                                 monkeypatch):
        page, read = self.doc(tmp_path)[1], fitz.Page.get_text

        def get_text(page, option="text", **kwargs):
            got = read(page, option, **kwargs)
            if option == "dict" and "flags" in kwargs:
                got["blocks"][0]["lines"].pop(0)
            return got

        monkeypatch.setattr(fitz.Page, "get_text", get_text)
        assert pm.ReadOnce(page).get_text("dict") == read(page, "dict")
