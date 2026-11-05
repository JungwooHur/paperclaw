"""A short conference paper is a paper, not a slide deck.

The ingestion check for a non-arXiv PDF called anything under 20 pages a slide
deck, so an 8-page NeurIPS paper or a 17-page ICLR paper with its appendix was
refused and the agent went looking for a "real" PDF that does not exist. A deck
is many sparse pages; a paper is dense ones, whatever their number.
"""
import fitz
import pytest

import pdf_check


@pytest.mark.parametrize("pages,avg_chars", [(7, 3294), (17, 2960), (22, 2725), (9, 1600)])
def test_dense_papers_of_any_length_are_papers(pages, avg_chars):
    assert not pdf_check.looks_like_slides(pages, avg_chars, "REFERENCES\n" + "X" * 2000)


@pytest.mark.parametrize("pages,avg_chars", [(30, 420), (12, 900), (60, 1100)])
def test_sparse_pages_are_slides(pages, avg_chars):
    assert pdf_check.looks_like_slides(pages, avg_chars, "SOME BULLET")


def test_a_short_last_page_that_thanks_the_audience_is_a_deck():
    assert pdf_check.looks_like_slides(14, 1700, "THANK YOU!\nQUESTIONS?")


def test_a_paper_that_says_thank_you_or_q_and_a_is_still_a_paper():
    last = ("ACKNOWLEDGMENTS WE THANK YOU ALL ... THE Q&A DATASET ... " + "Y" * 2500)
    assert not pdf_check.looks_like_slides(11, 3000, last)


def _pdf(tmp_path, pages, text):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(36, 36, 576, 806), text, fontsize=6)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return str(path)


def test_the_cli_passes_a_short_dense_paper_and_refuses_a_deck(tmp_path):
    paper = _pdf(tmp_path, 7, ("dense paper sentence " * 200))
    assert pdf_check.main([paper]) == 0
    deck = _pdf(tmp_path, 25, "- a bullet")
    assert pdf_check.main([deck]) == 1
