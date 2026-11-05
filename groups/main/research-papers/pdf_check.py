#!/usr/bin/env python3
"""Is this PDF a paper, or a slide deck or talk?

A non-arXiv paper is ingested from a PDF someone downloaded, and a conference
site often serves the talk's slides under the paper's title. The check that
caught those decks called anything under 20 pages a deck, which refused every
short conference paper: 8 pages at NeurIPS, 9 at ICLR, 17 with an appendix.
What tells them apart is density: a deck is many pages of a few words, a paper
is pages of prose, however few.

  pdf_check.py <pdf>   prints pages, avg_chars and slides_suspect; exit 1 on a deck
"""
import sys

import fitz

# A page of a two-column paper holds 2,500-4,000 characters; a slide, a few
# hundred. 1,500 sits well clear of both.
MIN_PAPER_CHARS = 1500
# A deck often ends on a page that only thanks the audience. A paper's last page
# can say "thank you" too, in its acknowledgments, but not on an almost empty page.
CLOSING_PAGE_CHARS = 600
CLOSING_WORDS = ("THANK YOU", "QUESTIONS?")


def looks_like_slides(pages: int, avg_chars: float, last_text: str) -> bool:
    if avg_chars < MIN_PAPER_CHARS:
        return True
    last = last_text.upper()
    return len(last.strip()) < CLOSING_PAGE_CHARS and any(w in last for w in CLOSING_WORDS)


def measure(path: str) -> tuple:
    doc = fitz.open(path)
    pages = doc.page_count
    avg_chars = sum(len(page.get_text()) for page in doc) / max(pages, 1)
    return pages, avg_chars, doc[-1].get_text() if pages else ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    pages, avg_chars, last_text = measure(argv[0])
    slides = looks_like_slides(pages, avg_chars, last_text)
    print(f"pages={pages} avg_chars={avg_chars:.0f} slides_suspect={slides}")
    return 1 if slides else 0


if __name__ == "__main__":
    sys.exit(main())
