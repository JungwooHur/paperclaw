"""What a section's source span counts.

Neither side counts a float caption: on the page it is an image's caption or a
paragraph of its own opening on a separated label (`Figure 4:`, `그림 4:`), in
the source a figure or table caption. Measured on one side only, a complete
translation reads as SUMMARIZED or CONTENT_LOSS, or a short one passes.
"""
import contextlib
import io
import json

import pytest

import verify_sections as vs

SENTENCE = ("The method is evaluated on a range of tasks and compared with the "
            "baselines described above. ")
CAPTION = ("Figure 1: Error of the estimator on the held-out split as the noise "
           "grows over the training run, plotted against the number of samples "
           "for several sizes of the model and of the split.")
BIBLIOGRAPHY = "\n".join(
    f"[{n}] A. Author. A title of a cited work, number {n}. In Proceedings of a "
    f"Conference, pages 1-10, 2020." for n in range(1, 30))


def prose(chars):
    """PDF-shaped prose: lines of about 75 characters, about `chars` in all."""
    words, lines, line = (SENTENCE * (chars // len(SENTENCE) + 1)).split(), [], ""
    for w in words:
        if len(line) + len(w) > 75:
            lines.append(line)
            line = ""
        line = (line + " " + w).strip()
        if sum(map(len, lines)) >= chars:
            break
    return "\n".join(lines + [line])


def close_to(got, text, slack=40):
    return got is not None and abs(got - len(text)) <= slack


INTRO, CONCLUSION = prose(6000), prose(650)


class TestBackMatterHeading:

    def source(self, heading):
        return "\n".join(["1", "Introduction", INTRO, "6", "Conclusion", CONCLUSION,
                          heading, prose(300), "References", BIBLIOGRAPHY])

    def test_every_spelling_of_acknowledgements_ends_the_last_section(self):
        for heading in ("Acknowledgement", "Acknowledgment", "Acknowledgements",
                        "Acknowledgments"):
            got = vs.source_section_chars(
                self.source(heading), [("1", "Introduction"), ("6", "Conclusion")])
            assert close_to(got["6"], CONCLUSION), heading


SECTIONS = [("2", "Method"), ("2.1", "Effect of Scale"), ("3", "Results")]


BEFORE, AFTER = prose(900), prose(900)


def latexml(before=BEFORE, after=AFTER):
    """A LaTeXML page: section 2.1 holds `before`, two figures, then `after`."""
    para = lambda text: "".join(f'<p class="ltx_p">{ln}</p>' for ln in text.splitlines())
    label, rest = CAPTION.split(": ", 1)
    figure = ('<figure id="S2.F{n}" class="ltx_figure"><img src="x{n}.png" alt="">'
              '<figcaption class="ltx_caption ltx_centering">'
              f'<span class="ltx_tag ltx_tag_figure">{label}: </span>'
              f'<span class="ltx_text" style="font-size:90%;">{rest}</span>'
              '</figcaption></figure>')
    return "".join([
        '<h2 class="ltx_title ltx_title_section">2 Method</h2>', para(prose(1200)),
        '<h3 class="ltx_title ltx_title_subsection">2.1 Effect of Scale</h3>',
        para(before), figure.format(n=1), figure.format(n=2), para(after),
        '<h2 class="ltx_title ltx_title_section">3 Results</h2>', para(prose(1500)),
    ])


class TestFigureCaptionsInHtml:

    def test_a_figure_caption_is_not_source_text(self):
        text = vs.source_text_from_html(latexml())
        assert "noise grows" not in text

    def test_the_section_measures_its_prose_only(self):
        got = vs.source_section_chars(vs.source_text_from_html(latexml()), SECTIONS)
        assert close_to(got["2.1"], BEFORE + AFTER, slack=80)

    def test_a_sentence_and_a_figure_is_still_a_located_section(self):
        html = latexml("The two figures below show the effect of scale.", "")
        got = vs.source_section_chars(vs.source_text_from_html(html), SECTIONS)
        assert got["2.1"] is not None and got["2.1"] < 100

    def test_a_body_reference_to_a_figure_is_kept(self):
        html = latexml("As Figure 1 shows, the error falls quickly.", "")
        assert "As Figure 1 shows" in vs.source_text_from_html(html)


PROMPT = ("You are a careful assistant. Read the question below and answer it in "
          "full sentences, giving the reason for each step you take.")
EQUATION = ('<table id="S2.E1" class="ltx_equation ltx_eqn_table"><tbody>'
            '<tr class="ltx_equation ltx_eqn_row ltx_align_baseline">'
            '<td class="ltx_eqn_cell ltx_align_center">'
            '<math id="S2.E1.m1" class="ltx_Math" alttext="L=\\sum_{i}\\ell_{i}" '
            'display="block"><mi>L</mi></math></td></tr></tbody></table>')
INLINE = ('The loss <math id="S2.p1.m1" class="ltx_Math" alttext="L" '
          'display="inline"><mi>L</mi></math> sums the error over every sample '
          'of the training run.')
# LaTeXML's floats, `{body}` their text: a prompt box set as a figure, a figure
# of two such panels, an algorithm, a code listing and a table of sentences.
FLOATS = {
    "figure": ('<figure id="S2.F3" class="ltx_figure"><div class="ltx_block">{body}'
               '</div><figcaption class="ltx_caption"><span class="ltx_tag '
               'ltx_tag_figure">Figure 3: </span>The prompt.</figcaption></figure>'),
    "panels": ('<figure id="S2.F4" class="ltx_figure"><div class="ltx_flex_figure">'
               + "".join(f'<div class="ltx_flex_cell ltx_flex_size_2"><figure '
                         f'id="S2.F4.sf{n}" class="ltx_figure ltx_figure_panel">{{body}}'
                         '<figcaption class="ltx_caption"><span class="ltx_tag '
                         f'ltx_tag_figure">({mark}) </span>A prompt.</figcaption>'
                         '</figure></div>' for n, mark in ((1, "a"), (2, "b")))
               + '</div><figcaption class="ltx_caption"><span class="ltx_tag '
               'ltx_tag_figure">Figure 4: </span>Two prompts.</figcaption></figure>'),
    "algorithm": ('<figure id="alg1" class="ltx_float ltx_float_algorithm '
                  'ltx_framed ltx_framed_top"><figcaption class="ltx_caption">'
                  '<span class="ltx_tag ltx_tag_float">Algorithm 1</span> The '
                  'loop</figcaption><div class="ltx_listing">{body}</div></figure>'),
    "listing": ('<figure id="lst1" class="ltx_float ltx_lstlisting"><div '
                'class="ltx_listing">{body}</div><figcaption class="ltx_caption">'
                '<span class="ltx_tag ltx_tag_float">Listing 1: </span>The '
                'prompt.</figcaption></figure>'),
    "table": ('<figure id="S2.T1" class="ltx_table"><figcaption class="ltx_caption">'
              '<span class="ltx_tag ltx_tag_table">Table 1: </span>Prompts.'
              '</figcaption><table class="ltx_tabular"><tr><td>{body}</td></tr>'
              '</table></figure>'),
}


def latexml_float(kind, before=BEFORE, after=AFTER, middle=""):
    """A LaTeXML page: section 2.1 holds `before`, a `kind` float whose body is
    sentences, `middle` (markup), then `after`."""
    para = lambda text: "".join(f'<p class="ltx_p">{ln}</p>' for ln in text.splitlines())
    return "".join([
        '<h2 class="ltx_title ltx_title_section">2 Method</h2>', para(prose(1200)),
        '<h3 class="ltx_title ltx_title_subsection">2.1 Effect of Scale</h3>',
        para(before), FLOATS[kind].format(body=para("\n".join([PROMPT] * 12))),
        middle, para(after),
        '<h2 class="ltx_title ltx_title_section">3 Results</h2>', para(prose(1500)),
    ])


class TestFloatBodiesInHtml:
    """A float is an image on the page, whatever LaTeXML sets in it, so its
    body is no section's text."""

    @pytest.mark.parametrize("kind", sorted(FLOATS))
    def test_a_float_body_is_not_source_text(self, kind):
        assert "careful assistant" not in vs.source_text_from_html(latexml_float(kind))

    @pytest.mark.parametrize("kind", sorted(FLOATS))
    def test_the_section_measures_its_prose_only(self, kind):
        got = vs.source_section_chars(
            vs.source_text_from_html(latexml_float(kind)), SECTIONS)
        assert close_to(got["2.1"], BEFORE + AFTER, slack=80)

    def test_a_sentence_and_a_float_is_still_a_located_section(self):
        html = latexml_float("figure", "The prompt below asks for reasons.", "")
        got = vs.source_section_chars(vs.source_text_from_html(html), SECTIONS)
        assert got["2.1"] is not None and got["2.1"] < 100

    def test_an_equation_and_inline_math_outside_a_float_are_kept(self):
        text = vs.source_text_from_html(latexml_float("figure", INLINE, AFTER, EQUATION))
        assert "The loss L sums the error" in text
        assert "L=\\sum_{i}\\ell_{i}" in text


def pdf_with(tmp_path, *items):
    """One PDF page of `items`: (text, fontsize) set as separate blocks."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=2400)
    y = 40
    for text, size in items:
        lines = max(1, len(text) // 80 + 1) + text.count("\n")
        h = lines * size * 1.6 + 10
        assert page.insert_textbox(fitz.Rect(60, y, 560, y + h), text,
                                   fontsize=size) >= 0, text[:30]
        y += h + 14
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


PARAGRAPH = ("Using the model on the larger sets, the accuracy stayed above the "
             "baselines on every split we tested, and the gap grew with the size of "
             "the set in all the settings we report in the tables below.")


def stacked(tmp_path, parts):
    """One PDF page: a column of body prose, then `parts` ((text, fontsize)),
    justified at one line pitch, so the PDF sets them as one block."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=1000)
    assert page.insert_textbox(fitz.Rect(60, 40, 300, 380), prose(600), fontsize=10,
                               align=fitz.TEXT_ALIGN_JUSTIFY) >= 0
    y = 420
    for text, size in parts:
        left = page.insert_textbox(fitz.Rect(60, y, 300, 1000), text, fontsize=size,
                                   lineheight=11 / size, align=fitz.TEXT_ALIGN_JUSTIFY)
        assert left >= 0, text[:30]
        y = 1000 - left
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


def blocks_below(tmp_path, caption, after):
    """One PDF page: body prose, then `caption` and `after` as two blocks set at
    body size, 12 pt apart."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=1000)
    assert page.insert_textbox(fitz.Rect(60, 40, 300, 380), prose(600),
                               fontsize=10) >= 0
    left = page.insert_textbox(fitz.Rect(60, 420, 300, 1000), caption, fontsize=10)
    assert page.insert_textbox(fitz.Rect(60, 1000 - left + 12, 300, 1000), after,
                               fontsize=10) >= 0
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return str(path)


def lines_at(tmp_path, lines):
    """One PDF page: body prose, then `lines` ((text, fontsize, width or None))
    set at an 11 pt pitch from the left margin, each stretched to
    `width` points when one is given."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=800)
    assert page.insert_textbox(fitz.Rect(60, 40, 315, 380), prose(700),
                               fontsize=10) >= 0
    for n, (text, size, width) in enumerate(lines):
        if width:
            size *= width / fitz.get_text_length(text, fontsize=size)
        page.insert_text((60, 400 + 11 * n), text, fontsize=size)
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return " ".join(vs.load_source_text(str(path), None).split())


class TestFigureCaptionsInPdf:

    def test_a_paragraph_after_a_bracketed_caption_end_is_kept(self, tmp_path):
        for end, size in ((" (best viewed in color)", 10), (" (Best viewed in color.)", 9),
                          (' ("ours")?', 9)):
            path = stacked(tmp_path, [(CAPTION[:-1] + end, 9), (PARAGRAPH, size)])
            text = " ".join(vs.load_source_text(path, None).split())
            assert "noise grows" not in text and PARAGRAPH in text, (end, size)

    def test_a_caption_block_ending_in_a_bracket_that_goes_on_takes_the_next(
            self, tmp_path):
        path = blocks_below(tmp_path, CAPTION[:-1] + " for the encoder (left)",
                            "and for the decoder (right) of the model.")
        assert "decoder (right)" not in vs.load_source_text(path, None)

    def test_a_short_caption_line_ending_in_a_bracket_does_not_end_it(
            self, tmp_path):
        text = lines_at(tmp_path, [
            ("Figure 1: Error of the estimator on the held-out part of each split,", 9, 255),
            ("plotted against the number of samples (left)", 9, None),
            ("Range of the samples for ten seeds and (right) for three splits.", 9, None)])
        assert "held-out part" not in text and "Range of the samples" not in text

    def test_a_body_size_paragraph_after_a_full_last_caption_line_is_kept(
            self, tmp_path):
        text = lines_at(tmp_path, [
            ("Figure 1: Error of the estimator on the held-out part of each split,", 9, 255),
            ("plotted against the number of samples for every size of model.", 9, 255),
            ("Using the model on the larger sets, the accuracy stayed", 10, None),
            ("above the baselines on every split.", 10, None)])
        assert "noise grows" not in text and "Using the model" in text

    def test_a_body_block_under_a_body_size_bracketed_caption_is_kept(self, tmp_path):
        path = blocks_below(tmp_path, CAPTION[:-1] + " (best viewed in color)",
                            PARAGRAPH)
        text = " ".join(vs.load_source_text(path, None).split())
        assert "noise grows" not in text and PARAGRAPH in text

    def test_a_body_block_under_a_caption_ending_a_full_line_is_kept(self, tmp_path):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=1000)
        assert page.insert_textbox(fitz.Rect(60, 40, 300, 380), prose(600),
                                   fontsize=10) >= 0
        # Two lines of one width at body size: the last is a full line.
        for n, line in enumerate(["Figure 1: Error of the estimator as the noise grows,",
                                  "and is the gap to the baseline real for every model?"]):
            page.insert_text((60, 400 + 12 * n), line, fontsize=10)
        assert page.insert_textbox(fitz.Rect(60, 428, 300, 1000), PARAGRAPH,
                                   fontsize=10) >= 0
        path = tmp_path / "paper.pdf"
        doc.save(path)
        text = " ".join(vs.load_source_text(str(path), None).split())
        assert "noise grows" not in text and PARAGRAPH in text

    def test_a_heading_after_a_full_last_caption_line_is_kept(self, tmp_path):
        text = lines_at(tmp_path, [
            ("Figure 1: Error of the estimator on the held-out part of each split,", 9, 255),
            ("plotted against the number of samples for every size of model.", 9, 255),
            ("3 Results", 9, None)])
        assert "held-out part" not in text and "3 Results" in text

    def test_a_sentence_end_a_little_short_of_the_measure_ends_the_caption(
            self, tmp_path):
        text = lines_at(tmp_path, [
            ("Figure 1: Error of the estimator on the held-out part of each split,", 9, 230),
            ("plotted against the number of samples for each model size.", 9, 240),
            ("Each curve is one size of the training set over ten seeds and the", 9, 255),
            ("three splits of the data.", 9, None)])
        assert "held-out part" not in text and "Each curve" in text

    def test_the_other_column_beside_a_caption_is_kept(self, tmp_path):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=800)
        page.insert_textbox(fitz.Rect(60, 40, 290, 300), prose(600), fontsize=10)
        page.insert_textbox(fitz.Rect(60, 400, 290, 600), CAPTION, fontsize=9)
        page.insert_textbox(fitz.Rect(320, 380, 560, 620), PARAGRAPH, fontsize=10)
        path = tmp_path / "paper.pdf"
        doc.save(path)
        text = " ".join(vs.load_source_text(str(path), None).split())
        assert "noise grows" not in text and PARAGRAPH in text

    def test_a_caption_block_is_not_source_text(self, tmp_path):
        body = prose(1500)
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (body, 10),
                        (CAPTION, 9), (prose(800), 10), ("3 Results", 11),
                        (prose(900), 10))
        text = vs.load_source_text(path, None)
        assert "noise grows" not in text
        got = vs.source_section_chars(text, SECTIONS[1:])
        assert close_to(got["2.1"], body + prose(800), slack=80)

    def test_a_caption_after_the_figure_s_own_label_line_is_not_source_text(
            self, tmp_path):
        # The PDF set an axis title and the caption under it as one block.
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11),
                        (prose(900), 10), ("log(N)\n" + CAPTION, 9))
        assert "noise grows" not in vs.load_source_text(path, None)

    def test_a_heading_the_pdf_set_in_the_caption_block_is_kept(self, tmp_path):
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (prose(900), 10),
                        (CAPTION + "\n3 Results", 9), (prose(900), 10))
        text = vs.load_source_text(path, None)
        assert "noise grows" not in text and "3 Results" in text
        assert vs.source_section_chars(text, SECTIONS[1:])["2.1"] is not None

    def test_a_paragraph_the_pdf_set_in_the_caption_block_is_kept(self, tmp_path):
        para = ("Using the model on the larger sets, the accuracy stayed above "
                "the baselines. " + prose(400).replace("\n", " "))
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (prose(900), 10),
                        (CAPTION + "\n" + para, 10), ("3 Results", 11),
                        (prose(900), 10))
        text = vs.load_source_text(path, None)
        assert "noise grows" not in text
        assert "Using the model on the larger sets" in text
        got = vs.source_section_chars(text, SECTIONS[1:])
        assert close_to(got["2.1"], prose(900) + para, slack=80)

    def test_a_sentence_ending_at_a_full_caption_line_does_not_end_it(self, tmp_path):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=800)
        page.insert_textbox(fitz.Rect(60, 40, 560, 300), prose(900), fontsize=10)
        lines = ["Figure 1: Error of the estimator on the held-out part of each split,",
                 "plotted against the number of samples for each model size.",
                 "Each curve is one size of the training set over ten seeds."]
        for n, line in enumerate(lines):
            size = 9 if n == 2 else 9 * 450 / fitz.get_text_length(line, fontsize=9)
            page.insert_text((60, 400 + 11 * n), line, fontsize=size)
        path = tmp_path / "paper.pdf"
        doc.save(path)
        assert "Each curve" not in vs.load_source_text(str(path), None)

    def test_a_sentence_ending_a_little_short_of_a_full_line_does_not_end_it(
            self, tmp_path):
        text = lines_at(tmp_path, [
            ("Figure 1: Error of the estimator on the held-out part of each split,", 9, 255),
            ("plotted against the number of samples for each model size.", 9, 253),
            ("Each curve is one size of the training set over ten seeds.", 9, None)])
        assert "held-out part" not in text and "Each curve" not in text

    def test_caption_fragments_on_one_baseline_are_one_line(self, tmp_path):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=800)
        page.insert_textbox(fitz.Rect(60, 40, 560, 300), prose(900), fontsize=10)
        page.insert_text((60, 400), "Figure 1: Error of the estimator on the split.",
                         fontsize=9)
        page.insert_text((290, 400), "Each curve is one size of the training set,",
                         fontsize=9)
        page.insert_text((60, 411), "against the number of samples as the noise grows.",
                         fontsize=9)
        path = tmp_path / "paper.pdf"
        doc.save(path)
        text = vs.load_source_text(str(path), None)
        assert "Each curve" not in text and "noise grows" not in text

    def test_a_line_that_opens_on_a_label_inside_a_paragraph_is_kept(self, tmp_path):
        body = prose(300) + "\nFigure 1. The error falls quickly with scale."
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (body, 10))
        assert "falls quickly" in vs.load_source_text(path, None)

    def test_a_sentence_that_opens_on_a_label_is_kept(self, tmp_path):
        sentence = "Figure 1 shows that the error falls quickly with scale."
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (sentence, 10))
        assert sentence in " ".join(vs.load_source_text(path, None).split())

    def test_a_heading_block_under_a_caption_with_no_stop_is_kept(self, tmp_path):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=612, height=1000)
        y, body = 40, ("We train the model on the full set and report the mean over five "
                       "seeds. ") * 12
        for text, gap in (("E.1 Setup", 6), (body, 8), (CAPTION[:-1], 6),
                          ("E.2 Generalization across labelers", 4), (body, 8),
                          ("E.3 Metadata results", 4), (body, 4)):
            left = page.insert_textbox(fitz.Rect(60, y, 550, 1000), text, fontsize=10,
                                       align=fitz.TEXT_ALIGN_JUSTIFY)
            y = 1000 - left + gap
        path = tmp_path / "paper.pdf"
        doc.save(path)
        text = vs.load_source_text(str(path), None)
        got = vs.source_section_chars(text, [
            ("E.1", "Setup"), ("E.2", "Generalization across labelers"),
            ("E.3", "Metadata results")])
        assert "noise grows" not in text and "E.2 Generalization" in text
        assert got["E.1"] is not None and got["E.2"] is not None, got

    def test_every_short_caption_the_pdf_set_in_one_block_is_not_source_text(
            self, tmp_path):
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11), (prose(900), 10),
                        ("Figure 1: Overview.\nTable 1: Sizes of the splits.", 9),
                        (prose(900), 10))
        text = vs.load_source_text(path, None)
        assert "Overview" not in text and "Sizes of the splits" not in text


def sentences_table(page, top, ruled=True, over=False):
    """Three rows of cells of sentences, between rules when `ruled`, captioned
    below, or above when `over`; returns the caption's baseline, or when `over`
    the table's last rule."""
    if over:
        page.insert_text((72, top + 10), "Table 3: Examples of answers.", fontsize=10)
        top += 16
    y = top
    for _ in range(3):
        if ruled:
            page.draw_line((72, y), (540, y))
        for j in range(3):
            page.insert_text((78, y + 14 + 12 * j), PROMPT[:70], fontsize=10)
        y += 50
    if ruled:
        page.draw_line((72, y), (540, y))
    if over:
        return y
    page.insert_text((72, y + 14), "Table 3: Examples of answers.", fontsize=10)
    return y + 14


def prompt_box(page, top):
    """A framed box of six lines of sentences, captioned below; returns the
    caption's baseline."""
    import fitz

    page.draw_rect(fitz.Rect(90, top, 520, top + 130), color=(0, 0, 0))
    for j in range(6):
        page.insert_text((100, top + 20 + 16 * j), PROMPT[:60], fontsize=9)
    page.insert_text((72, top + 150), "Figure 2: The prompt given to the model.",
                     fontsize=10)
    return top + 150


def pdf_float(tmp_path, float_at=None, gap=26):
    """A PDF whose section 2.1 is a paragraph, the float `float_at(page, top)`
    draws (none when None), and a paragraph `gap` under the bottom it returns;
    pages of 10 pt prose before it."""
    import fitz

    doc = fitz.open()
    para = lambda page, top: page.insert_textbox(
        fitz.Rect(72, top, 540, top + 130), prose(600), fontsize=10)
    for heading in ("1 Introduction", "2 Method"):
        page = doc.new_page()
        page.insert_text((72, 70), heading, fontsize=12)
        for top in (80, 260, 440, 620):
            para(page, top)
    page = doc.new_page()
    page.insert_text((72, 70), "2.1 Effect of Scale", fontsize=12)
    para(page, 80)
    bottom = float_at(page, 220) if float_at else 220
    para(page, bottom + gap)
    page.insert_text((72, bottom + 180), "3 Results", fontsize=12)
    para(page, bottom + 190)
    path = tmp_path / ("float.pdf" if float_at else "plain.pdf")
    doc.save(path)
    return str(path)


def measure(path):
    """The source text of the PDF at `path` and its section 2.1's span."""
    text = vs.load_source_text(path, None)
    return text, vs.source_section_chars(text, SECTIONS)["2.1"]


class TestFloatBodiesInPdf:
    """A float the crop renderer takes (`extract_pdf_media.float_crops`) is an
    image on the page, so the text in its crop is no section's text."""

    @pytest.mark.parametrize("float_at", [sentences_table, prompt_box])
    def test_the_section_measures_its_prose_only(self, tmp_path, float_at):
        text, got = measure(pdf_float(tmp_path, float_at))
        assert "careful assistant" not in text
        assert close_to(got, "x" * measure(pdf_float(tmp_path))[1], slack=2)

    @pytest.mark.parametrize("float_at,gap", [
        (lambda page, top: prompt_box(page, top - 4), 26),
        (lambda page, top: sentences_table(page, top, over=True), 2),
    ], ids=["prose-over-a-box", "prose-under-a-table"])
    def test_prose_whose_line_the_crop_meets_is_counted(self, tmp_path, float_at, gap):
        import fitz

        import extract_pdf_media as epm

        path = pdf_float(tmp_path, float_at, gap)
        with fitz.open(path) as doc:
            (rect,) = [c[2] for _, cs in epm.float_crops(doc) for c in cs]
            edges = {ln["bbox"][k] for b in doc[2].get_text("dict")["blocks"]
                     if epm._block_text(b).startswith("The method")
                     for ln in b["lines"] for k in (1, 3)}
        assert {rect.y0, rect.y1} & edges
        assert close_to(measure(path)[1], "x" * measure(pdf_float(tmp_path))[1], slack=2)

    def test_a_float_the_renderer_cannot_crop_keeps_its_body(self, tmp_path):
        import fitz

        import extract_pdf_media as epm

        path = pdf_float(tmp_path, lambda page, top: sentences_table(page, top, False))
        with fitz.open(path) as doc:
            crops = [c for _, cs in epm.float_crops(doc) for c in cs]
        assert [(c[0], c[1], c[2]) for c in crops] == [("table", 3, None)]
        text = vs.load_source_text(path, None)
        assert "careful assistant" in text and "Examples of answers" not in text

    def test_a_heading_in_a_crop_is_kept(self, tmp_path):
        # A crop the renderer took past its float, over the next heading.
        import fitz

        import extract_pdf_media as epm

        with fitz.open(pdf_float(tmp_path, prompt_box)) as doc:
            pages = [epm.ReadOnce(page) for page in doc]
            text = vs._pdf_page_text(pages[2], [fitz.Rect(60, 200, 550, 560)],
                                     epm.body_text(pages))
        assert "careful assistant" not in text and "3 Results" in text

    def test_each_page_s_get_text_is_one_dict_without_images(self, tmp_path,
                                                             monkeypatch):
        import fitz

        path, reads, read = pdf_float(tmp_path, prompt_box), [], fitz.Page.get_text

        def get_text(page, option="text", **kwargs):
            reads.append((page.number, option, kwargs.get("flags")))
            return read(page, option, **kwargs)

        monkeypatch.setattr(fitz.Page, "get_text", get_text)
        vs.load_source_text(path, None)
        assert sorted(n for n, _, _ in reads) == [0, 1, 2]
        assert all(o == "dict" and f is not None and not f & fitz.TEXT_PRESERVE_IMAGES
                   for _, o, f in reads)


class TestNumericRows:

    def test_a_long_row_of_numbers_is_a_table_fragment(self):
        rows = "\n".join([
            "Table 1: Performance on the dataset", "Method", "Input", "IoU",
            "Baseline* [5] xrgb", "41.60", "15.17 44.71 22.41 12.55", "26.11",
            "Ours", "xrgb", "42.16 19.88 41.88",
            "22.37 16.98 29.09 42.43 42.00 29.60 10.62 36.36 24.61 28.71",
            "93.50 12.06 12.57 13.72 48.19 36.11 15.13 15.22 27.96 12.94 26.94",
            "36.11", "15.13"])
        body = prose(900)
        assert vs._prose_chars(body + "\n" + rows) == len(body)

    def test_a_long_row_of_negative_numbers_is_a_table_fragment(self):
        row = " ".join(f"-{n}.25 \u2212{n}.75" for n in range(1, 7))
        body = prose(900)
        assert vs._prose_chars(body + "\n" + "\n".join([row] * 4)) == len(body)

    def test_a_sentence_with_numbers_is_prose(self):
        line = "In 2019 and 2020 the error fell from 12.5 to 3.4 on 8 of the 10 tasks."
        assert vs._prose_chars(line) == len(line)

    def test_a_long_row_of_percentages_is_a_table_fragment(self):
        row = " ".join(f"{n}.13% {n}.87%" for n in range(41, 47))
        body = prose(900)
        assert vs._prose_chars(body + "\n" + "\n".join([row] * 4)) == len(body)

    def test_a_line_of_exactly_half_numbers_is_prose(self):
        line = "Results 41.60 baseline 42.16 previous 39.85 ablation 47.02"
        assert vs._prose_chars("Method\nInput\nIoU\nOurs\n" + line) == len(line)


_TABLE_ROWS = "\n".join(["Parameter", "Value", "Learning rate", "3e-4", "Batch", "256"])


def audit_json():
    import contextlib
    import io
    import json
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            vs.main()
        except SystemExit:
            pass
    return json.loads(out.getvalue())["findings"]


def block(kind, text):
    return {"type": kind, "id": text[:8], kind: {"rich_text": [{"plain_text": text}]}}


class TestTranslatedCaptionsOnThePage:

    BODY = "본문은 규모가 커질수록 오차가 빠르게 줄어든다는 것을 보여준다. " * 10

    def chars(self, *extra):
        blocks = [block("heading_2", "2.1 Effect of Scale"),
                  block("paragraph", self.BODY), {"type": "image", "id": "img"},
                  *(block("paragraph", t) for t in extra)]
        return vs.group_sections(blocks)[0]["chars"]

    def test_a_translated_caption_paragraph_is_not_body_text(self):
        for caption in ("Figure 1: 학습 집합에서 표본 수에 따른 추정 오차.",
                        "그림 1: 학습 집합에서 표본 수에 따른 추정 오차.",
                        "표 1. 데이터셋에서의 성능.", "Fig. 8 | 오차의 변화."):
            assert self.chars(caption) == len(self.BODY), caption

    def test_a_section_of_floats_only_is_not_empty(self, monkeypatch):
        blocks = [block("heading_3", "E.1 Hyperparameters"), {"type": "image", "id": "i"},
                  block("paragraph", "Table 7: 학습률과 배치 크기 | 3e-4 | 256 |" * 3),
                  block("heading_3", "E.2 Compute"), block("paragraph", self.BODY)]
        monkeypatch.setattr(vs, "fetch_blocks", lambda page: blocks)
        monkeypatch.setattr(vs, "page_title", lambda page: "a paper")
        monkeypatch.setattr(vs, "load_source_text", lambda *a: (
            "E.1 Hyperparameters\n" + _TABLE_ROWS + "\nE.2 Compute\n" + prose(600)))
        monkeypatch.setattr("sys.argv", ["verify_sections.py", "--page", "p",
                                         "--source", "x.pdf", "--json"])
        findings = audit_json()
        assert not [f for f in findings if f.get("section") == "E.1"], findings

    def test_a_parent_of_caption_only_subsections_is_not_empty(self, monkeypatch):
        blocks = [block("heading_2", "4 Experimental Details"),
                  block("heading_3", "Hyperparameters"), {"type": "image", "id": "i"},
                  block("paragraph", "Table 7: 학습률과 배치 크기 | 3e-4 | 256 |" * 3),
                  block("heading_2", "5 Compute"), block("paragraph", self.BODY)]
        monkeypatch.setattr(vs, "fetch_blocks", lambda page: blocks)
        monkeypatch.setattr(vs, "page_title", lambda page: "a paper")
        monkeypatch.setattr(vs, "load_source_text", lambda *a: (
            "Hyperparameters\n" + _TABLE_ROWS + "\n5 Compute\n" + prose(600)))
        monkeypatch.setattr("sys.argv", ["verify_sections.py", "--page", "p",
                                         "--source", "x.pdf", "--json"])
        findings = audit_json()
        assert not [f for f in findings if f.get("section") == "4"], findings

    def test_a_short_intro_over_translated_tables_is_no_content_loss(self, monkeypatch):
        captions = [f"Table {n}: 모델이 생성한 응답 예시와 평가자가 매긴 점수의 비교." * 4
                    for n in range(4, 11)]
        blocks = [block("heading_3", "D.2 Sample Responses"),
                  block("paragraph", "아래 표들은 각 설정에서 얻은 응답 예시를 보여준다. " * 4)]
        for caption in captions:
            blocks += [{"type": "image", "id": "i"}, block("paragraph", caption)]
        blocks += [block("heading_3", "D.3 Compute"), block("paragraph", self.BODY)]
        monkeypatch.setattr(vs, "fetch_blocks", lambda page: blocks)
        monkeypatch.setattr(vs, "page_title", lambda page: "a paper")
        monkeypatch.setattr(vs, "load_source_text", lambda *a: (
            "D.2 Sample Responses\n" + prose(9000) + "\nD.3 Compute\n" + prose(600)))
        monkeypatch.setattr("sys.argv", ["verify_sections.py", "--page", "p",
                                         "--source", "x.pdf", "--json"])
        found = [f["type"] for f in audit_json() if f.get("section") == "D.2"]
        assert found == ["SUMMARIZED"], found

    def test_two_caption_only_sections_are_compared_by_their_captions(self):
        a = vs.group_sections([block("heading_3", "Results"),
                               block("paragraph", "Figure 2: 첫 번째 실험의 정확도 변화 곡선. " * 6)])[0]
        b = vs.group_sections([block("heading_3", "Results"),
                               block("paragraph", "Figure 5: 두 번째 환경에서 측정한 보상 분포. " * 6)])[0]
        assert not vs.dup_confirmed(a, b)
        assert vs.dup_confirmed(a, dict(a))

    def test_a_paragraph_that_mentions_a_figure_is_body_text(self):
        for text in ("그림 1은 오차가 빠르게 줄어든다는 것을 보여준다.",
                     "Figure 1 shows that the error falls quickly."):
            assert self.chars(text) == len(self.BODY) + len(text), text


class TestTextBesideAFigure:
    """A `column_list` holds `column`s, and each column the blocks set in it."""

    INTRO = "아래 그림은 각 설정에서 얻은 정성적 결과를 보여준다. "
    BODY = TestTranslatedCaptionsOnThePage.BODY
    CAPTION = "Figure 5: 각 설정에서 얻은 정성적 결과."

    def test_a_section_whose_text_sits_in_a_column_list_measures_it(self, monkeypatch):
        children = {
            "page": [block("heading_3", "6.1 Qualitative Results"),
                     block("paragraph", self.INTRO),
                     {"type": "column_list", "id": "columns", "has_children": True},
                     block("heading_3", "6.2 Limitations"), block("paragraph", self.BODY)],
            "columns": [{"type": "column", "id": "left", "has_children": True},
                        {"type": "column", "id": "right", "has_children": True}],
            "left": [{"type": "image", "id": "img"}, block("paragraph", self.CAPTION)],
            "right": [block("paragraph", self.BODY), block("paragraph", self.BODY)],
        }
        monkeypatch.setattr(vs, "fetch_blocks", children.__getitem__)
        monkeypatch.setattr(vs, "page_title", lambda page: "a paper")
        monkeypatch.setattr(vs, "load_source_text", lambda *a: (
            "6.1 Qualitative Results\n" + prose(1500) + "\n6.2 Limitations\n"
            + prose(700)))
        monkeypatch.setattr("sys.argv", ["verify_sections.py", "--page", "page",
                                         "--source", "x.pdf", "--json"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            vs.main()
        report = json.loads(out.getvalue())
        chars = {s["key"]: s["chars"] for s in report["sections"]}
        assert chars["6.1"] == len(self.INTRO) + 2 * len(self.BODY), chars
        assert not [f for f in report["findings"] if f.get("section") == "6.1"], report

    def test_the_columns_are_read_left_to_right_in_place(self, monkeypatch):
        children = {
            "columns": [{"type": "column", "id": "left"}, {"type": "column", "id": "right"}],
            "left": [block("paragraph", "left one"), block("paragraph", "left two")],
            "right": [block("paragraph", "right one")],
        }
        monkeypatch.setattr(vs, "fetch_blocks", children.__getitem__)
        got = vs.reading_order([block("paragraph", "before"),
                                {"type": "column_list", "id": "columns"},
                                block("paragraph", "after")])
        assert [b["id"] for b in got] == ["before", "left one", "left two", "right on",
                                          "after"]

    def test_a_column_list_inside_a_column_is_read_in_place(self, monkeypatch):
        children = {
            "columns": [{"type": "column", "id": "left"}, {"type": "column", "id": "right"}],
            "left": [block("paragraph", "left one"), {"type": "column_list", "id": "inner"},
                     block("paragraph", "left two")],
            "inner": [{"type": "column", "id": "inner a"}, {"type": "column", "id": "inner b"}],
            "inner a": [block("paragraph", "nested a")],
            "inner b": [block("paragraph", "nested b")],
            "right": [block("paragraph", "right one")],
        }
        monkeypatch.setattr(vs, "fetch_blocks", children.__getitem__)
        got = vs.reading_order([block("paragraph", "before"),
                                {"type": "column_list", "id": "columns"},
                                block("paragraph", "after")])
        assert [b["id"] for b in got] == ["before", "left one", "nested a", "nested b",
                                          "left two", "right on", "after"]


class TestCaptionGrammarEdges:

    def test_prose_between_an_algorithm_caption_and_a_figure_caption_is_kept(self):
        algorithm = ('<figure class="ltx_float"><figcaption class="ltx_caption">'
                     '<span class="ltx_tag ltx_tag_float">Algorithm 1</span> Search'
                     '</figcaption></figure>')
        html = latexml().replace("<h3", algorithm + "<h3", 1)
        got = vs.source_section_chars(vs.source_text_from_html(html), SECTIONS)
        assert close_to(got["2.1"], BEFORE + AFTER, slack=80)

    def test_a_sentence_and_a_long_caption_is_still_a_located_pdf_section(
            self, tmp_path):
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11),
                        ("The two figures below show the effect of scale.", 10),
                        (CAPTION + " " + CAPTION[10:] * 3, 9), ("3 Results", 11),
                        (prose(900), 10))
        got = vs.source_section_chars(vs.load_source_text(path, None), SECTIONS[1:])
        assert got["2.1"] is not None and got["2.1"] < 100

    def test_a_body_paragraph_opening_on_a_label_is_kept(self, tmp_path):
        para = ("Figure 3 (left) shows the error against the scale. "
                + prose(500).replace("\n", " "))
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11),
                        (prose(600), 10), (para, 10), (prose(600), 10))
        assert "Figure 3 (left) shows" in " ".join(
            vs.load_source_text(path, None).split())

    def test_a_caption_with_no_separator_in_caption_type_is_blanked(self, tmp_path):
        caption = "Fig. 1 " + CAPTION.split(": ", 1)[1]
        path = pdf_with(tmp_path, ("2.1 Effect of Scale", 11),
                        (prose(900), 10), (caption, 9), (prose(900), 10))
        assert "noise grows" not in vs.load_source_text(path, None)
