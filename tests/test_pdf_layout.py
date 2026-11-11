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


def _paragraph(page, x0, x1, top):
    page.insert_textbox(fitz.Rect(x0, top, x1, top + 400), PROSE, fontsize=10,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    return max((b["bbox"] for b in page.get_text("dict")["blocks"]
                if b["bbox"][0] == x0), key=lambda box: box[1])


def prose(page, x0, x1, bottom):
    """A justified paragraph whose block ends at `bottom`; returns its box."""
    scratch = fitz.open().new_page(width=page.rect.width, height=page.rect.height)
    probe = _paragraph(scratch, x0, x1, 0)
    box = _paragraph(page, x0, x1, bottom - probe[3])
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
        page.draw_rect(fitz.Rect(wall[2] - pm._COLUMN_TOL / 2, 200, 540, 380),
                       color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
        page.insert_text((RIGHT[0], 398), "Figure 2. A right-column figure.",
                         fontsize=9)
        box = pm.render_media(save(doc, tmp_path), str(tmp_path / "out"))[
            ("figure", 2)]["box"]
        assert box[0] <= wall[2] - pm._COLUMN_TOL / 2

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
