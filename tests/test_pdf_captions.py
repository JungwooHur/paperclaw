"""Recognising a figure caption in a PDF.

Papers punctuate captions differently and the differences are invisible until a
whole paper comes out with no figures. Two forms have already cost this project
a silent zero: the short word (`Fig. 7:` where only `Figure 7:` was accepted),
and a caption with no separator at all (`Figure 1 Language modeling…`). A label
that is not a plain number (`S2`, `B.1`, `3.1`, `TABLE IV`) is read in each of
those styles too, and a body sentence that opens with a label is not a caption.
"""
import pytest

import extract_pdf_media as pm


def matches(text, num=1, kind="figure"):
    return pm.opens_caption(text, kind, num)


class TestThePunctuatedForms:

    def test_a_colon_after_the_number(self):
        assert matches("Figure 1: Language modeling performance")

    def test_a_period_after_the_number(self):
        assert matches("Figure 1. Language modeling performance")

    def test_a_pipe_after_the_number(self):
        assert matches("Fig. 1 | Language modeling performance")

    def test_the_short_word(self):
        assert matches("Fig. 1: Language modeling performance")

    def test_a_table_caption(self):
        assert matches("Table 3: Fits to the data", num=3, kind="table")


class TestNoSeparatorAtAll:

    def test_a_caption_that_runs_straight_into_its_text(self):
        assert matches("Figure 1 Language modeling performance improves")

    def test_the_number_alone_on_its_line(self):
        assert matches("Figure 7", num=7)

    def test_extra_spacing_from_pdf_extraction(self):
        # PyMuPDF hands back "Figure   1 Language   modeling" on justified text.
        assert matches("Figure   1 Language   modeling   performance")


class TestNotMatchingTheWrongFigure:

    def test_a_longer_number_is_not_this_one(self):
        assert not matches("Figure 12 Something else")

    def test_a_longer_number_is_not_this_one_with_a_colon(self):
        assert not matches("Figure 12: Something else")

    def test_a_leading_zero_is_still_this_one(self):
        assert matches("Figure 01: Something")

    def test_another_figure_is_not_this_one(self):
        assert not matches("Figure 2 Something else")

    def test_a_table_is_not_a_figure(self):
        assert not matches("Table 1: Something")

    def test_a_dotted_label_is_not_its_chapter_number(self):
        assert not matches("Figure 3.1: Something", num=3)


class TestTheLabelAsPrinted:

    @pytest.mark.parametrize("text, expected", [
        ("Figure S2: A supplementary figure.", ("figure", "S2")),
        ("Figure B.1. An appendix figure.", ("figure", "B.1")),
        ("Table A.1: An appendix table.", ("table", "A.1")),
        ("Figure A1 | Grid over cycle length.", ("figure", "A1")),
        ("Figure   A4   |   Comparison to prior fits.", ("figure", "A4")),
        ("Figure S3 Caption that runs straight on.", ("figure", "S3")),
        ("FIGURE S4. An upper-case caption.", ("figure", "S4")),
        ("Figure 3.1: A chapter-numbered figure.", ("figure", "3.1")),
        ("Table G.10: A two-digit appendix table.", ("table", "G.10")),
        ("TABLE IV", ("table", 4)),
        ("TABLE   II   Results on the real robot", ("table", 2)),
        ("Extended Data Fig. 3 | A journal figure.", ("ed_figure", 3)),
        ("Extended Data Table 2: A journal table.", ("ed_table", 2)),
    ])
    def test_it_is_read(self, text, expected):
        assert pm.caption_kind(text) == expected

    def test_a_numbered_label_is_an_int(self):
        assert pm.caption_kind("Figure 12 Something") == ("figure", 12)

    def test_a_figure_is_never_roman(self):
        assert pm.caption_kind("Figure I: Something") is None


class TestASentenceIsAMention:

    @pytest.mark.parametrize("text", [
        "Figure 6 shows these results for both data sets.",
        "Table II examines the performance of the algorithm.",
        "Figure S2 compares the two runs.",
        "Table 2, the largest of them, lists every setting.",
    ])
    def test_it_opens_no_caption(self, text):
        assert pm.caption_kind(text) is None

    def test_an_image_caption_is_read_however_it_continues(self):
        assert pm.image_label("Figure 2 results") == ("figure", 2)
        assert pm.image_label("그림 S2: 보충 그림") == ("figure", "S2")
        assert pm.image_label("table 3 fits") == ("table", 3)
