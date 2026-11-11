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
        bottom, top = self.page(doc, grid, caption=False)
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
