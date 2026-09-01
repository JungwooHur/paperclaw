"""Recognising a figure caption in a PDF.

Papers punctuate captions differently and the differences are invisible until a
whole paper comes out with no figures. Two forms have already cost this project
a silent zero: the short word (`Fig. 7:` where only `Figure 7:` was accepted),
and now a caption with no separator at all (`Figure 1 Language modeling…`).
"""
import extract_pdf_media as pm


def matches(text, num=1, kind="figure"):
    return bool(pm.caption_opener(kind, num).match(text))


class TestThePunctuatedForms:

    def test_a_colon_after_the_number(self):
        assert matches("Figure 1: Language modeling performance")

    def test_a_period_after_the_number(self):
        assert matches("Figure 1. Language modeling performance")

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


class TestTheScanThatEnumeratesThem:
    """`caption_opener` locates one number's caption; this one sweeps a page for
    whatever it finds. They must agree — when only one of them learned about
    `Fig.`, the other kept the whole PDF path at zero, and the same happened
    again with the missing separator."""

    def test_it_finds_a_caption_with_no_separator(self):
        m = pm.ANY_CAPTION.match("Figure 1 Language modeling performance")
        assert m and m.group(2) == "1"

    def test_it_still_finds_a_punctuated_one(self):
        m = pm.ANY_CAPTION.match("Fig. 4: Left: the loss")
        assert m and m.group(2) == "4"

    def test_it_tells_a_table_from_a_figure(self):
        m = pm.ANY_CAPTION.match("Table 3 Fits to the data")
        assert m and m.group(1).lower().startswith("tab")

    def test_it_reads_the_whole_number(self):
        m = pm.ANY_CAPTION.match("Figure 12 Something")
        assert m and m.group(2) == "12"

    def test_it_agrees_with_the_single_number_matcher(self):
        for text in ("Figure 1 no separator", "Fig. 1: punctuated",
                     "Figure 1. period", "Figure   1   spaced"):
            swept = pm.ANY_CAPTION.match(text)
            assert swept and swept.group(2) == "1", text
            assert pm.caption_opener("figure", 1).match(text), text
