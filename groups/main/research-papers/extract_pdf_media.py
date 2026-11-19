#!/usr/bin/env python3
r"""Render a PDF-only paper's figures AND tables and inject them into Notion.

Why this exists
---------------
`extract_paper_figures` / `extract_paper_tables` both need LaTeXML HTML
(`arxiv.org/html/<id>` or ar5iv). A paper that has NO usable HTML — a company
tech report, a brand-new arxiv submission whose HTML build hasn't landed, a
scanned/`pdflatex`-only submission — falls off both paths:

  * figures: the ONLY fallback was a ~90-line snippet pasted in
    `groups/main/CLAUDE.md` ("Phase 3b") that the agent copy-ran by hand. That is
    the recurring "prose isn't load-bearing" pattern, AND its crop math was
    wrong (see below), so pages shipped with whole-page screenshots.
  * tables: there was no PDF path at all, not even prose — so every table stayed
    as the flattened run of numbers the fulltext translation produced.

The two crop bugs the pasted snippet had (both reproduced pixel-exactly against
a real 47-page report, then fixed here):

  1. VERTICAL over-capture. It took `fig_top = min(y of every vector drawing
     above the caption)`. A running-header rule is a vector drawing at the top of
     the page, so `fig_top` collapsed to the page margin and the crop swallowed
     the title block, the abstract and every body paragraph above the figure —
     "a screenshot of the whole page" instead of the figure. 10 of 16 figures hit
     this.
  2. HORIZONTAL clipping. It inferred the figure's column from the CAPTION's
     x-extent (`is_fullwidth = cx0 < 0.3W and cx1 > 0.7W`). A short centered
     caption ("Figure 1: Main results.") fails that test, so a
     full-width figure on a SINGLE-column paper was treated as a left-column
     figure and cropped to `cx1 + 6` — chopping off its right half.

Geometry used here instead
--------------------------
Both bugs come from inferring the figure's box from the caption. This walks the
page's actual content instead. Text blocks are split into `barriers` (justified
body prose, headings) and `elements` (everything else: vector drawings, raster
images, axis labels, sub-captions). Starting at the caption, the region grows
away from it one element at a time and stops as soon as a barrier is nearer than
the next element — i.e. at the first line of body text. Only a barrier
overlapping the caption's x-span ends the walk; one beside it bounds the
caption's column instead. Page furniture is never reached because body text
always sits between it and the figure. The crop box is
then the UNION of what was actually collected (plus the caption), so a
single-column figure keeps its full width and a two-column figure keeps its own
column, with no caption-shape guessing.

A figure's own print that reads like body text stays its content: print set
on a drawing that runs on past the caption and holds no body prose or other
caption (an EPS backdrop) is the figure's, small print opening on a panel's
mark (`(a) …`) under a panel is a subcaption, and lines between the rows of an
image grid or titling one of its panels are no prose to the crop check; a
paragraph's last lines over a row are.

Tables use the identical machinery, growing DOWNWARD (LaTeX puts a table's
caption above its body) instead of upward. A rule set as a thin raster image is
a rule like a drawn one.

  # eyeball the crops first — renders to disk, touches nothing:
  extract_pdf_media.py --pdf <path-or-url> --out <dir>

  extract_pdf_media.py --page <id> [--arxiv <id> | --pdf <path-or-url>]
                       [--dry-run] [--figures-only|--tables-only]
                       [--force] [--keep-text]

`--force` REPLACES: it archives the page's existing figure/table images before
injecting, so a page built by the old broken path can be repaired in place
without ending up with two of every figure.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import urllib.request
from typing import NamedTuple, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract_paper_figures as ef  # noqa: E402
import extract_paper_tables as et  # noqa: E402
import reference_section  # noqa: E402

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# Crop geometry, in PDF points (1/72").
# Never crop outside this margin of the page. It was 40 pt, which is an arXiv
# margin: a journal's text block runs to about 32 pt from the edge, and a figure
# spanning the column lost its right-most box to the clamp. What the clamp is for
# — the margin stamp — is excluded as furniture before anything reaches it.
_CLAMP = 8.0
_PAD = 6.0         # breathing room around the collected content
_CAP_GAP = 26.0    # max vertical gap when a caption spills into more blocks
_DPI = 250         # render resolution; ~3.5x, matches the old snippet

# A block counts as a barrier (body prose / heading) rather than figure content.
_BARRIER_MIN_LINES = 2
_BARRIER_FULL_FRAC = 0.85    # a "full-measure" line is this much of the block width
_BARRIER_FULL_SHARE = 0.6    # ...and this share of lines must be full-measure
_HEADING_SIZE_DELTA = 0.6    # a block this much larger than body text is a heading
_BODY_SIZE_TOL = 0.3         # text this close to body size is set at body size
_BARRIER_MIN_SIZE_FRAC = 0.75  # prose is set at body size; small print is figure furniture
_WIDE_FRAC = 0.9             # a block this share of the column measure is column-wide
_PROSE_MIN_LINES = 4         # a narrower block of prose holds at least this many lines
_PROSE_MIN_CHARS = 25        # ...of at least this many characters on average
_MEASURE_TOL = 3.0           # block widths this close are one column measure
_BASELINE_TOL = 1.0          # line fragments with baselines this close are one line
_COLUMN_TOL = 2.0            # content may reach this far past a column wall
_GUTTER = 6.0                # a column wall stands at least this far beside the caption
_COVER_TOL = 2.0             # a drawing this close to a text's edge still encloses it
_RULE_MAX_THICK = 2.0        # a rule is a drawing at most this thick...
_RULE_MIN_LENGTH = 20.0      # ...and at least this long
_RULE_ALIGN = 3.0            # the rules of one ruled float start and end this close
_RULE_TOUCH = 1.5            # a drawing this close to a ruled float is part of it
_CELL_GAP = 4.0              # two cells of one row stand at least this far apart
_ROW_TOL = 2.0               # lines whose centres are this close share a row
_SAME_EXTENT = 0.8           # rows overlapping this share of the wider span one extent
_NOTE_GAP = 8.0              # text this close past a table's last rule is its note
_PROSE_LINE_CHARS = 40       # a line of prose length holds at least this many characters
_PROSE_LINES_HELD = 2        # a crop holding this many such lines holds prose
_INK = 160                   # a pixel darker than this, on a 0-255 grey scale, is ink

# Page furniture: running heads/feet, and the rotated arxiv stamp in the margin.
_FURNITURE_SAMPLE = 24       # pages sampled to learn the running head/foot
_FURNITURE_SHARE = 0.3       # ...must repeat on this share of them
_FURNITURE_GAP = 6.0         # min gap separating a running head/foot from the body
_HEAD_MAX_CHARS = 120
_FOOT_MAX_CHARS = 20
_STAMP_ASPECT = 3.0          # rotated margin text is this much taller than wide
_STAMP_MIN_HEIGHT = 80.0
_STAMP_MIN_PAGE_FRAC = 0.25  # ...and runs at least this much of the page height
_STAMP_MARGIN_FRAC = 0.10    # ...entirely within this much of the outer margin
_BACKGROUND_AREA_FRAC = 0.8  # a box this big is a page background, not content
_LABEL_LINE_MAX = 40         # a figure's own label line is shorter than this (chars)
_PANEL_GAP = 12.0            # a panel's title or subcaption stands this close to it


# A float's caption opener: the word, the label as printed, an optional
# separator. The label is the citation grammar the HTML injectors share
# (`extract_paper_figures._LABEL`): `7`, `S2`, `B.1`, `A1`, `3.1`, or a roman
# numeral, and one letter glued onto a label ending in a digit (`10a`).
# Leading zeros are allowed because some PDFs pad the number.
_CAPTION = re.compile(
    r"\s*(?P<ed>Extended\s+Data\s+)?(?P<word>Fig\.?|Figure|Table)\s*0*"
    + ef._LABEL + r"(?P<suffix>(?<=\d)[a-z](?![A-Za-z]))?(?P<sep>\s*[:.|])?", re.I)

# A label ending in one letter glued onto a digit (`10a`), and what precedes it.
_SUFFIXED = re.compile(r"(?P<cited>.*\d)[a-z]", re.I)

ED_PREFIX = "ed_"

# A fixed-pitch typeface, by name, for fonts that do not flag it.
_FIXED_PITCH_FONT = re.compile(
    r"mono|courier|typewriter|cmtt|sftt|consol|menlo|monaco|inconsolata|"
    r"sourcecode|firacode", re.I)

# What a body sentence continues with after the label it opens on.
_SENTENCE_GOES_ON = re.compile(r"\s*(?:[a-z]|[,;])")
# A sentence's last characters: its stop and any closing quotes or brackets.
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")

# The title opening an algorithm box: `Algorithm 2`, `ALGORITHM S1`.
_ALGORITHM = re.compile(r"\s*algorithm\s+[A-Z]?\d+\b", re.I)

# A section number: `3`, `3.1.`, `A.2`.
_SECTION = r"\s*(?:[1-9]\d?|[A-Z](?=\.\d))(?:\.\d{1,2})*\.?"

# A section number and the first letter of the heading it opens: `3 M`,
# `3.1. M`, `A.2 M`.
_SECTION_NUMBER = re.compile(_SECTION + r"\s+[A-Z]")

# A panel's mark opening its subcaption: `(a) `, `(iv) `.
_PANEL_MARK = re.compile(r"\s*\((?:[a-z]|[ivx]{1,4})\)\s")

# A small-capitals typeface, by name.
_SMALL_CAPS_FONT = re.compile(r"cmcsc|smallcap|caps\b", re.I)

# What a continued table's header carries after its label: `– continued from
# previous page`, `(Continued)`, `cont'd`.
_CONTINUED = re.compile(
    r"\s*[–—-]?\s*\(?\s*(?:continued|cont\.|cont['’]d)\s*(?:\)|\.|from\b|on\b|$)",
    re.I)


def base_kind(kind: str) -> str:
    """`figure` or `table`, whether or not the kind is an Extended Data one."""
    return kind[len(ED_PREFIX):] if kind.startswith(ED_PREFIX) else kind


def _read_label(text: str, sentence_ok: bool):
    """(kind, label) of the float label `text` opens with, or None.

    The label is an int for a number (a roman table number included) and the
    printed string otherwise, its letter suffix included (`10a`). With
    `sentence_ok` false, a label with no separator that runs on into
    lower-case words or a comma opens a sentence, and a suffixed one with no
    separator a citation of a panel (`Fig. 1d` in a cell), not a caption. A
    label followed by `continued` heads a table's later part,
    not its caption.
    """
    found = _CAPTION.match(text or "")
    if not found:
        return None
    base = "table" if found.group("word").lower().startswith("tab") else "figure"
    token = found.group("label") + (found.group("suffix") or "")
    if token.isdigit():
        label = int(token)
    elif re.fullmatch(r"[ivx]+", token, re.I):
        label = ef.ROMAN_VALUES.get(token.lower()) if base == "table" else None
        if label is None:
            return None
    else:
        label = token
    if (not sentence_ok and not found.group("sep")
            and (found.group("suffix")
                 or _SENTENCE_GOES_ON.match(text, found.end()))):
        return None
    if _CONTINUED.match(text, found.end()):
        return None
    return (ED_PREFIX + base if found.group("ed") else base), label


def caption_numeral(text: str) -> str:
    """The roman numeral the caption opener `text` prints as its label,
    lower-case, or ""."""
    found = _CAPTION.match(text or "")
    token = found.group("label") if found else ""
    return token.lower() if re.fullmatch(r"[ivx]+", token, re.I) else ""


def caption_kind(text: str):
    """(kind, label) of the caption this PDF text block opens, or None.

    The label is an int for a numbered caption and the printed string ("S2",
    "B.1", "3.1") otherwise.
    """
    return _read_label(text, sentence_ok=False)


def _in_english(caption: str) -> str:
    """`caption` with a Korean opening float word (`그림`, `표`) in English."""
    return re.sub(r"^\s*그림", "Figure", re.sub(r"^\s*표", "Table", caption or ""))


def image_label(caption: str):
    """(kind, label) an image block's caption opens with, in English or Korean."""
    return _read_label(_in_english(caption), sentence_ok=True)


def is_caption_text(text: str) -> bool:
    """Does `text`, in English or Korean, open on a float label set off by a
    separator (`Figure 4:`, `그림 4:`, `표 1.`, `Fig. 8 |`)?"""
    text = _in_english(text)
    return bool(_read_label(text, sentence_ok=True)) and _separated(text)


def opens_caption(text: str, kind: str, label) -> bool:
    """Does `text` open the caption of `kind label`?"""
    return caption_kind(text) == (kind, label)


def _spans(block):
    for line in block.get("lines", ()):
        for span in line.get("spans", ()):
            yield span


def _block_text(block) -> str:
    return " ".join(s["text"] for s in _spans(block))


def _size_histogram(blocks, hist: dict) -> dict:
    """Adds each text span's character count to `hist`, keyed by its font size."""
    for b in blocks:
        if b.get("type") != 0:
            continue
        for s in _spans(b):
            key = round(s.get("size", 0), 1)
            hist[key] = hist.get(key, 0) + len(s.get("text", ""))
    return hist


def _mode(hist: dict, tol: float) -> float:
    """The key of `hist` whose window of keys within `tol` holds the most weight;
    ties go to the heavier key."""
    def window(key):
        return sum(n for k, n in hist.items() if abs(k - key) <= tol + 1e-6)
    return max(hist, key=lambda k: (window(k), hist[k]))


def _body_size(blocks) -> float:
    """The dominant font size of these blocks, by characters (`_mode`)."""
    hist = _size_histogram(blocks, {})
    return _mode(hist, _BODY_SIZE_TOL) if hist else 10.0


class Body(NamedTuple):
    """The document's body text.

    size: its font size, in points (`_body_size` over every page).
    measure: the width of its column, in points, or None for a document with
        no block of prose (`_prose_measure`).
    """
    size: float
    measure: Optional[float]


def document_body_size(doc) -> float:
    """The document's body text size: its dominant font size over every page.

    Barriers are told from figure content by this size (`_is_barrier`).
    """
    hist = {}
    for page in doc:
        _size_histogram(page.get_text("dict")["blocks"], hist)
    return _mode(hist, _BODY_SIZE_TOL) if hist else 10.0


def _prose_measure(doc, size: float):
    """The dominant width (`_mode`), by characters, of the body-size blocks of
    at least `_PROSE_MIN_LINES` full-measure lines; None when there are none."""
    hist = {}
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0 or not _at_body_size(b, size):
                continue
            lines = _lines(b)
            if len(lines) < _PROSE_MIN_LINES or not _full_measure(b):
                continue
            width = round(b["bbox"][2] - b["bbox"][0])
            hist[width] = hist.get(width, 0) + sum(n for _, n in lines)
    return _mode(hist, _MEASURE_TOL) if hist else None


def body_text(doc) -> Body:
    """The document's `Body`."""
    size = document_body_size(doc)
    return Body(size, _prose_measure(doc, size))


def _at_body_size(block, body_size: float) -> bool:
    """Is the block's dominant print the document's body size?"""
    return abs(_body_size([block]) - body_size) <= _BODY_SIZE_TOL


def _lines(block) -> list:
    """(box, characters) of each printed line of the block.

    The line objects of words that share a baseline are one line: PyMuPDF
    splits a justified line at its stretched spaces. One without a letter (a
    tick label, a number) stays apart, and so do all of them in a block most of
    whose lines split: that block is a table's rows, its line objects cells.
    """
    raw = [(tuple(ln["bbox"]), ln["spans"][0]["origin"][1],
            sum(len(s["text"]) for s in ln["spans"]),
            any(c.isalpha() for s in ln["spans"] for c in s["text"]))
           for ln in block.get("lines") or () if ln.get("spans")]
    joined = []
    for box, base, chars, words in raw:
        for line in joined:
            if words and line[4] and abs(line[1] - base) <= _BASELINE_TOL:
                line[0] = (min(line[0][0], box[0]), min(line[0][1], box[1]),
                           max(line[0][2], box[2]), max(line[0][3], box[3]))
                line[2] += chars
                line[3] += 1
                break
        else:
            joined.append([box, base, chars, 1, words])
    if 2 * sum(1 for line in joined if line[3] > 1) > len(joined):
        return [(box, chars) for box, _, chars, _ in raw]
    return [(line[0], line[2]) for line in joined]


def _full_measure(block) -> bool:
    """Do most of the block's lines (`_lines`) run the full width of the block?"""
    lines = _lines(block)
    if len(lines) < _BARRIER_MIN_LINES:
        return False
    x0, _, x1, _ = block["bbox"]
    measure = (x1 - x0) * _BARRIER_FULL_FRAC
    full = sum(1 for box, _ in lines if box[2] - box[0] >= measure)
    return full >= max(_BARRIER_MIN_LINES, _BARRIER_FULL_SHARE * len(lines))


def _fixed_pitch(block) -> bool:
    """Is most of the block's print in a fixed-pitch typeface (code)?"""
    fixed = total = 0
    for s in _spans(block):
        n = len(s.get("text", "").strip())
        total += n
        if s.get("flags", 0) & 8 or _FIXED_PITCH_FONT.search(s.get("font", "")):
            fixed += n
    return 2 * fixed > total


def _prose_shaped(block, measure) -> bool:
    """Lines of the full measure, not in a fixed-pitch typeface, in a block as
    wide as the column (`measure`, `Body.measure`), or in at least
    `_PROSE_MIN_LINES` lines of prose length."""
    if not _full_measure(block) or _fixed_pitch(block):
        return False
    width = block["bbox"][2] - block["bbox"][0]
    if measure is not None and width >= _WIDE_FRAC * measure:
        return True
    lines = _lines(block)
    return (len(lines) >= _PROSE_MIN_LINES
            and sum(n for _, n in lines) >= _PROSE_MIN_CHARS * len(lines))


def _title_line(block) -> bool:
    """Is the block one printed line of one line object, or of two whose first
    is a bare section number (`_SECTION`), on one baseline (`_BASELINE_TOL`)?"""
    lines = [ln for ln in block.get("lines") or () if ln.get("spans")]
    if not 1 <= len(lines) <= 2:
        return False
    bases = [ln["spans"][0]["origin"][1] for ln in lines]
    first = "".join(s["text"] for s in lines[0]["spans"])
    return (max(bases) - min(bases) <= _BASELINE_TOL
            and (len(lines) == 1 or re.fullmatch(_SECTION + r"\s*", first) is not None))


def _small_caps(block) -> bool:
    """Is most of the block's print in a small-capitals face (`_SMALL_CAPS_FONT`),
    or are all its letters capitals in two sizes or more?"""
    spans = [s for s in _spans(block) if s["text"].strip()]
    chars = sum(len(s["text"].strip()) for s in spans)
    named = sum(len(s["text"].strip()) for s in spans
                if _SMALL_CAPS_FONT.search(s.get("font", "")))
    lettered = [s for s in spans if any(c.isalpha() for c in s["text"])]
    capitals = bool(lettered) and all(
        c.isupper() for s in lettered for c in s["text"] if c.isalpha())
    return (2 * named > chars
            or capitals and len({round(s.get("size", 0), 1) for s in lettered}) > 1)


def _is_heading(block, body: Body) -> bool:
    """Is some of the block's print `_HEADING_SIZE_DELTA` or more over body
    size, or is the block a title line (`_title_line`) with print at body size
    or over that opens on a section number (`_SECTION_NUMBER`) or is set in
    small capitals (`_small_caps`)?"""
    sizes = [round(s.get("size", 0), 1) for s in _spans(block)]
    if not sizes:
        return False
    if max(sizes) >= body.size + _HEADING_SIZE_DELTA:
        return True
    return (max(sizes) >= body.size - _BODY_SIZE_TOL and _title_line(block)
            and bool(_SECTION_NUMBER.match(_block_text(block)) or _small_caps(block)))


def _is_barrier(block, body: Body) -> bool:
    """True for body prose or a heading — content a figure never spans.

    Body prose is recognised by MEASURE: most of its lines run the full width of
    the block. Figure furniture (axis ticks, legends, sub-captions such as
    `(a) Full training trajectory`) is short and ragged, so it never matches.
    A share rather than "every line" is required because a block routinely holds
    several paragraphs, and each paragraph's last line is short by definition —
    demanding flush edges on all lines mis-read a whole abstract as figure
    content. Headings are caught by font size instead, being too short to
    qualify on measure.
    """
    if not block.get("lines"):
        return False
    if _is_heading(block, body):
        return True
    sizes = [round(s.get("size", 0), 1) for s in _spans(block)]
    # The measure test below is relative to the block's OWN width, so it is blind
    # to scale: a prompt bubble drawn inside a figure is a narrow wrapped
    # paragraph whose lines are flush to its own little box, and it reads as body
    # prose just as strongly as a real column does. Size is the signal that is not
    # scale-free — body prose is set at the document's body size by definition,
    # while figure furniture is set much smaller. Without this, two bubbles inside
    # a teaser figure ended the upward walk two thirds of the way up and the
    # figure shipped with its top third cut off.
    if sizes and max(sizes) < body.size * _BARRIER_MIN_SIZE_FRAC:
        return False
    return _prose_shaped(block, body.measure)


def _is_prose(block, body: Body) -> bool:
    """Body prose: prose-shaped lines (`_prose_shaped`) at body size, no heading."""
    return (not _is_heading(block, body) and _at_body_size(block, body.size)
            and _prose_shaped(block, body.measure))


def _norm_furniture(text: str) -> str:
    """Normalise a head/foot line so page numbers don't defeat the match."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text)).strip().lower()


def _page_lines(page):
    """Text lines on the page, ordered top to bottom."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines") or ():
            text = "".join(s["text"] for s in ln.get("spans", ()))
            if text.strip():
                out.append((tuple(ln["bbox"]), text))
    out.sort(key=lambda r: r[0][1])
    return out


def learn_furniture(doc) -> tuple:
    """Find the document's running heads and feet, as normalised text.

    A paper's running head (its title, repeated at the top) and its page
    number are drawn from the page's own content stream, so they look exactly
    like figure content to a geometric walk — and they sit at the very top of the
    page, which is how the old snippet ended up cropping whole pages. They are
    identified the only way that is actually reliable: they REPEAT, near-verbatim,
    at the same edge of most pages. Odd and even pages are learned apart, since
    a two-sided layout alternates its head (title on one side, authors on the
    other).

    Returns (heads, feet): frozensets of normalised lines, each possibly empty.
    """
    heads, feet = set(), set()
    per_side = _FURNITURE_SAMPLE // 2
    for side in (0, 1):
        pages = range(side, doc.page_count, 2)
        step = max(1, len(pages) // per_side)
        sampled = list(pages[::step])[:per_side]
        tops, bots = {}, {}
        for pno in sampled:
            lines = _page_lines(doc[pno])
            if len(lines) < 3:
                continue
            tops.setdefault(_norm_furniture(lines[0][1]), []).append(pno)
            bots.setdefault(_norm_furniture(lines[-1][1]), []).append(pno)

        def _pick(hist, max_chars):
            if not hist:
                return None
            text = max(hist, key=lambda k: len(hist[k]))
            enough = len(hist[text]) >= max(2, _FURNITURE_SHARE * len(sampled))
            return text if enough and 0 < len(text) <= max_chars else None

        heads.add(_pick(tops, _HEAD_MAX_CHARS))
        feet.add(_pick(bots, _FOOT_MAX_CHARS))
    return frozenset(heads - {None}), frozenset(feet - {None})


def _row_at(lines, index: int) -> tuple:
    """(y0, y1) of the whole typeset ROW containing lines[index].

    A running head is often several boxes side by side (title left, `TECHNICAL
    REPORT` right, a logo between). Treating only the single first line as the
    header leaves its neighbours behind as figure content, so the row is taken as
    a unit: every line that vertically overlaps the seed line.
    """
    seed = lines[index][0]
    row = [bbox for bbox, _ in lines if bbox[1] < seed[3] and bbox[3] > seed[1]]
    return min(b[1] for b in row), max(b[3] for b in row)


def _rule_below(page, y: float, span: float = 12.0):
    """Bottom of the visible part (`_visible`) of the wide hairline nearest
    under `y`, within `span` — the rule under a running head — or None."""
    found = [v for v in _visible(page).values() if v is not None
             and v[3] - v[1] <= 2.5 and v[2] - v[0] >= page.rect.width * 0.5
             and y - 2 <= v[1] <= y + span]
    return min(found, key=lambda r: r[1])[3] if found else None


def furniture_band(page, heads, feet) -> tuple:
    """(header_bottom, footer_top) for this page — the body's usable y range.

    `heads` and `feet` are the document's (`learn_furniture`). Only the page's
    own top/bottom row is considered, and only when it matches one of them AND
    is separated from the body by a real gap, so a page whose top line is
    genuine content is never clipped.
    """
    lines = _page_lines(page)
    top, bottom = float("-inf"), float("inf")
    if len(lines) < 3:
        return top, bottom
    if _norm_furniture(lines[0][1]) in heads:
        _, row_y1 = _row_at(lines, 0)
        below = [b[1] for (b, _) in lines if b[1] >= row_y1]
        if below and min(below) - row_y1 >= _FURNITURE_GAP:
            top = row_y1 + 2
            rule = _rule_below(page, row_y1)
            if rule is not None and rule < min(below):
                top = rule + 2
    if _norm_furniture(lines[-1][1]) in feet:
        row_y0, _ = _row_at(lines, len(lines) - 1)
        above = [b[3] for (b, _) in lines if b[3] <= row_y0]
        if above and row_y0 - max(above) >= _FURNITURE_GAP:
            bottom = row_y0 - 2
    return top, bottom


def _is_stamp(box, page_rect) -> bool:
    """A rotated stamp in the page margin (`arXiv:NNNN.NNNNNvN [cs.XX] …`).

    It runs the height of the page in the outer margin, so it would otherwise act
    as a barrier straight through every figure on page 1. Being in the MARGIN is
    part of the test on purpose: a figure's own rotated y-axis label has the same
    tall-and-narrow shape but sits inside the text block, and excluding it would
    crop the axis title off the figure.
    """
    width, height = box[2] - box[0], box[3] - box[1]
    if height < _STAMP_MIN_HEIGHT or height < _STAMP_ASPECT * max(width, 1.0):
        return False
    if height < _STAMP_MIN_PAGE_FRAC * page_rect.height:
        return False
    margin = _STAMP_MARGIN_FRAC * page_rect.width
    return box[2] <= page_rect.x0 + margin or box[0] >= page_rect.x1 - margin


def _too_big(box, page_rect) -> bool:
    """A background rectangle covering the page — never figure content.

    Also anything reaching well past the page edge: a clip region or a bleed
    drawn behind a figure. The 40 pt margin clamp used to hide these; with the
    clamp narrowed for journal margins, one stretched a crop to the full page.
    """
    if (box[0] < -2 or box[1] < -2 or box[2] > page_rect.width + 2
            or box[3] > page_rect.height + 2):
        return True
    area = max(box[2] - box[0], 0) * max(box[3] - box[1], 0)
    return area >= _BACKGROUND_AREA_FRAC * page_rect.width * page_rect.height


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _separated(text: str) -> bool:
    """Does the caption opener `text` set its label off with a separator?"""
    found = _CAPTION.match(text or "")
    return bool(found and found.group("sep"))


def caption_boxes(page, kind: str, num) -> list:
    """(box, opening block) of each caption of `kind num` on this page.

    Openers that set the label off with a separator (`Figure 18:`) come first,
    then the others (`Figure 18 (a) presents ...` may be a sentence), each top to
    bottom. A long caption is split by the PDF into several blocks; they are
    stitched back on while the text has not reached a sentence end and the next
    block is close by, in the same column, at the same font size, with no rule
    between.
    """
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
    blocks.sort(key=lambda b: b["bbox"][1])
    starts = [i for i, b in enumerate(blocks)
              if opens_caption(_block_text(b), kind, num)]
    starts.sort(key=lambda i: not _separated(_block_text(blocks[i])))
    if not starts:
        return []
    rules = _Rules(page)
    return [(_stitched(blocks, i, rules), blocks[i]) for i in starts]


def caption_regions(page, blocks) -> list:
    """Box of every float caption among this page's text `blocks` (its
    `get_text("dict")` blocks), figure or table (`_stitched`).

    A caption opens a block, or follows short lines the PDF set in the same
    block (the figure's own labels: axis titles, `log(N)`; a short caption),
    and sets its label off with a separator or is set smaller than the page's
    body text. It ends at a sentence end that a section heading follows or that
    closes a short line (a paragraph's last), and a caption set smaller than
    body text also ends at the first row set at body size, even where the PDF
    set that heading or the next paragraph in the caption's block.
    """
    blocks = sorted((b for b in blocks if b.get("type") == 0), key=lambda b: b["bbox"][1])
    rules = _Rules(page)
    body = _body_size(blocks)
    out = []
    for i, b in enumerate(blocks):
        for n, ln in enumerate(b["lines"]):
            text = _block_text({"lines": [ln]})
            small = _body_size([{"type": 0, "lines": [ln]}]) < body - _BODY_SIZE_TOL
            if caption_kind(text) and (_separated(text) or small):
                head = dict(b, lines=b["lines"][n:],
                            bbox=_union(x["bbox"] for x in b["lines"][n:]))
                box = _stitched(blocks[:i] + [head] + blocks[i + 1:], i, rules)
                rows = _rows([head] + blocks[i + 1:], box)
                right = max(x["bbox"][2] for row in rows for x in row)
                kept = rows[:1]
                for row in rows[1:]:
                    now = _block_text({"lines": row})
                    if (small and _at_body_size({"type": 0, "lines": row}, body)
                            or _SENTENCE_END.search(_block_text({"lines": kept[-1]}).rstrip())
                            and (_SECTION_NUMBER.match(now)
                                 or _ends_short(kept[-1], right))):
                        break
                    kept.append(row)
                out.append(_union(x["bbox"] for row in kept for x in row))
            if len(text.strip()) >= _LABEL_LINE_MAX:
                break
    return out


def _ends_caption(text: str, after: str) -> bool:
    """Can caption text ending in `text` end before `after`: at a sentence end,
    or at a closing bracket (`(best viewed in color)`) that `after` does not go
    on from?"""
    text = text.rstrip()
    return bool(_SENTENCE_END.search(text)) or (
        text.endswith(")") and not _SENTENCE_GOES_ON.match(after))


def _ends_short(row, right: float) -> bool:
    """Does `row` (a list of lines) stop more than half its font size short of
    `right`, the way a paragraph's last line does?"""
    size = max(s["size"] for ln in row for s in ln["spans"])
    return right - max(ln["bbox"][2] for ln in row) > size / 2


def _rows(blocks, box) -> list:
    """The lines of `blocks` centred inside `box`, grouped by baseline, in order."""
    rows = []
    for b in blocks:
        for ln in b["lines"]:
            x0, y0, x1, y1 = ln["bbox"]
            if not (box[0] <= (x0 + x1) / 2 <= box[2] and box[1] <= (y0 + y1) / 2 <= box[3]):
                continue
            base = ln["spans"][0]["origin"][1] if ln.get("spans") else y1
            if rows and abs(rows[-1][0]["spans"][0]["origin"][1] - base) <= _BASELINE_TOL:
                rows[-1].append(ln)
            else:
                rows.append([ln])
    return rows


def _page_rules(page) -> list:
    """Visible parts (`_visible`) of the page's horizontal rules."""
    return [v for v in _visible(page).values() if v is not None
            and v[3] - v[1] <= _RULE_MAX_THICK and v[2] - v[0] >= _RULE_MIN_LENGTH]


class _Rules:
    """The page's horizontal rules (`_page_rules`), read on first use."""

    def __init__(self, page):
        self._page, self._rules, self._paths = page, None, None

    def __iter__(self):
        if self._rules is None:
            self._rules = _page_rules(self._page)
        return iter(self._rules)

    def between(self, top: float, bottom: float, x0: float, x1: float) -> bool:
        """Does a rule lie from `top` to `bottom` (each 0.5 looser) and across
        part of x0..x1? None does when no path's box (`get_bboxlog`; it holds
        the path's visible part) meets that band, and then the rules are not
        read."""
        if self._rules is None:
            if self._paths is None:
                self._paths = [r for t, r in self._page.get_bboxlog() if t.endswith("path")]
            if not any(b[1] <= bottom + 0.5 and b[3] >= top - 0.5
                       and b[0] < x1 + 0.5 and b[2] > x0 - 0.5 for b in self._paths):
                return False
        return any(r[1] >= top - 0.5 and r[3] <= bottom + 0.5 and r[0] < x1 and r[2] > x0
                   for r in self)


def _caption_lines(block, rules) -> int:
    """How many of the block's lines the caption opening it takes: all of them
    up to the first line, on a baseline of its own, in none of the fonts of
    the lines before it or past one of `rules` from them."""
    fonts, base, bottom = set(), None, None
    for i, ln in enumerate(block.get("lines") or ()):
        names = {s["font"] for s in ln.get("spans", ()) if s["text"].strip()}
        origin = ln["spans"][0]["origin"][1] if ln.get("spans") else base
        new_line = base is None or abs(origin - base) > _BASELINE_TOL
        x0, y0, x1, _ = ln["bbox"]
        if i and new_line and ((names and not names & fonts)
                               or rules.between(bottom, y0, x0, x1)):
            return i
        fonts |= names
        base = origin
        bottom = ln["bbox"][3] if bottom is None or new_line else max(bottom, ln["bbox"][3])
    return len(block.get("lines") or ())


def _union(boxes) -> tuple:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _stitched(blocks, start: int, rules) -> tuple:
    """Box of the caption opening at blocks[start] (sorted top to bottom): its
    lines of that block (`_caption_lines`) and, when it takes them all, the
    blocks that continue it; no block past one of `rules` or opening on a
    section number (`_SECTION_NUMBER`) continues it."""
    head = blocks[start]
    taken = _caption_lines(head, rules)
    if taken < len(head["lines"]):
        return _union(ln["bbox"] for ln in head["lines"][:taken])
    x0, y0, x1, y1 = head["bbox"]
    fonts = {round(s.get("size", 0), 1) for s in _spans(head)}
    text = _block_text(head)
    for nxt in blocks[start + 1:]:
        after = _block_text(nxt)
        if _ends_caption(text, after) or _SECTION_NUMBER.match(after):
            break
        nx0, ny0, nx1, ny1 = nxt["bbox"]
        if ny0 - y1 > _CAP_GAP or abs(nx0 - x0) > 40:
            break
        if {round(s.get("size", 0), 1) for s in _spans(nxt)} - fonts:
            break
        if rules.between(y1, ny0, nx0, nx1):
            break
        x0, x1, y1 = min(x0, nx0), max(x1, nx1), ny1
        text = _block_text(nxt)
    return (x0, y0, x1, y1)


class _Parts:
    """A page split around one caption.

    barriers: boxes of body prose and headings.
    walls: the barriers that bound a column: body prose (`_is_prose`) and
        other floats' captions.
    captions: the walls that are other floats' captions.
    figure_captions: the captions that are figures'.
    loose: the barriers from text that is neither body prose nor a caption.
    small: the loose barriers that are no heading (`_is_heading`): small print
        set as prose.
    elements: boxes of everything else — text, raster images, drawings.
    drawings: the elements that are vector drawings or raster rules
        (`_horizontals`).
    texts: (box, block) of the elements that are text.
    """

    def __init__(self):
        self.barriers, self.walls, self.captions, self.loose = [], [], [], []
        self.figure_captions, self.small = [], []
        self.elements, self.drawings, self.texts = [], [], []

    def make_barrier(self, box) -> None:
        """Turns `box` into a barrier, dropping the elements it encloses."""
        self.barriers.append(box)
        self.elements = [e for e in self.elements if not _contains(box, e)]

    def drop(self, float_box) -> None:
        """Drops the elements of the ruled float `float_box` (`_member`)."""
        self.elements = [e for e in self.elements if not _member(float_box, e)]

    def make_element(self, box) -> None:
        """Turns the barrier `box` into an element."""
        self.barriers.remove(box)
        self.walls = [w for w in self.walls if w != box]
        self.loose = [b for b in self.loose if b != box]
        self.elements.append(box)


def _page_parts(page, caption_box, body: Body, band) -> _Parts:
    """Split the page around `caption_box`, excluding the caption and furniture."""
    head_bottom, foot_top = band
    rect = page.rect

    def _furniture(box):
        return (box[3] <= head_bottom or box[1] >= foot_top
                or _is_stamp(box, rect))

    parts = _Parts()
    for b in page.get_text("dict")["blocks"]:
        box = tuple(b["bbox"])
        if _furniture(box):
            continue
        if b.get("type") != 0:
            parts.elements.append(box)    # raster image
            parts.drawings += _horizontals([box])
            continue
        if _overlaps(box, caption_box):   # the caption is the anchor, not content
            rest = [ln for ln in b.get("lines") or ()
                    if ln["bbox"][1] >= caption_box[3] - 0.5
                    or ln["bbox"][3] <= caption_box[1] + 0.5]
            if not rest:
                continue
            b = {"type": 0, "lines": rest, "bbox": _union(ln["bbox"] for ln in rest)}
            box = tuple(b["bbox"])
        if _is_barrier(b, body):
            parts.barriers.append(box)
            if _is_prose(b, body):
                parts.walls.append(box)
            else:
                parts.loose.append(box)
                if not _is_heading(b, body):
                    parts.small.append(box)
        else:
            parts.elements.append(box)
            parts.texts.append((box, b))
    for d in page.get_drawings():
        r = d["rect"]
        if r.y1 - r.y0 <= 0.2 and r.x1 - r.x0 <= 0.2:
            continue                      # degenerate stroke
        box = (r.x0, r.y0, r.x1, r.y1)
        if _furniture(box):
            continue
        parts.elements.append(box)
        parts.drawings.append(box)
    return parts


def _contains(outer, inner) -> bool:
    return (inner[0] >= outer[0] - 0.5 and inner[1] >= outer[1] - 0.5
            and inner[2] <= outer[2] + 0.5 and inner[3] <= outer[3] + 0.5)


def _member(float_box, box) -> bool:
    """Does `box` lie in the ruled float `float_box`, as `_ruled_floats` grows
    one: within `_RULE_ALIGN` of its width and `_RULE_TOUCH` of its height?"""
    return (box[0] >= float_box[0] - _RULE_ALIGN and box[2] <= float_box[2] + _RULE_ALIGN
            and box[1] >= float_box[1] - _RULE_TOUCH
            and box[3] <= float_box[3] + _RULE_TOUCH)


def _horizontals(drawings) -> list:
    """The rules among `drawings`: at most `_RULE_MAX_THICK` thick and at
    least `_RULE_MIN_LENGTH` long."""
    return [d for d in drawings if d[3] - d[1] <= _RULE_MAX_THICK
            and d[2] - d[0] >= _RULE_MIN_LENGTH]


def _verticals(drawings) -> list:
    """The vertical rules among `drawings`: at most `_RULE_MAX_THICK` wide and
    taller than that."""
    return [d for d in drawings if d[2] - d[0] <= _RULE_MAX_THICK
            and d[3] - d[1] > _RULE_MAX_THICK]


def _grid_columns(box, floats, verticals) -> list:
    """(rule, float) for each column rule of one of `floats` beside or across
    `box`: a vertical rule covering the box's height, at an x inside the
    float where another piece of `verticals` (other ends) stands in the
    float, with the box within the float's width."""
    out = []
    for v in verticals:
        if v[1] > box[1] + _COVER_TOL or v[3] < box[3] - _COVER_TOL:
            continue
        x = (v[0] + v[2]) / 2
        for f in floats:
            if (f[0] + _CELL_GAP < x < f[2] - _CELL_GAP
                    and box[0] >= f[0] - _RULE_ALIGN and box[2] <= f[2] + _RULE_ALIGN
                    and any(abs((u[0] + u[2]) / 2 - x) <= _RULE_ALIGN
                            and (abs(u[1] - v[1]) > _RULE_TOUCH
                                 or abs(u[3] - v[3]) > _RULE_TOUCH)
                            and _contains(f, u) for u in verticals)):
                out.append((v, f))
    return out


def _in_cell(box, floats, verticals) -> bool:
    """Is `box` set in a cell of a ruled float: beside one of its column rules
    (`_grid_columns`) that reaches to within `_RULE_ALIGN` of its rules?"""
    return any(v[3] >= f[1] - _RULE_ALIGN and v[1] <= f[3] + _RULE_ALIGN
               for v, f in _grid_columns(box, floats, verticals))


def _with_notes(box, texts) -> tuple:
    """`box` grown over each of `texts` printed within `_NOTE_GAP` under it
    with `_SAME_EXTENT` of its width within the box's, in turn."""
    grown = True
    while grown:
        grown = False
        for t in texts:
            if (t[3] > box[3] and box[3] - 0.5 <= t[1] <= box[3] + _NOTE_GAP
                    and min(t[2], box[2]) - max(t[0], box[0])
                    >= _SAME_EXTENT * (t[2] - t[0])):
                box, grown = _union([box, t]), True
    return box


def _across(caption_box, box) -> bool:
    """Does `box` overlap the caption's x-span?"""
    return box[0] < caption_box[2] and box[2] > caption_box[0]


def _column(caption_box, walls, y0: float, y1: float) -> tuple:
    """(x_lo, x_hi): the caption's column over y0..y1.

    Bounded by the walls overlapping y0..y1 vertically that stand at least a
    gutter to the left or right of the caption's x-span; open where there are
    none.
    """
    lo, hi = float("-inf"), float("inf")
    for w in walls:
        if w[1] >= y1 or w[3] <= y0:
            continue
        if w[2] <= caption_box[0] - _GUTTER:
            lo = max(lo, w[2])
        elif w[0] >= caption_box[2] + _GUTTER:
            hi = min(hi, w[0])
    return lo, hi


def _in_column(box, column) -> bool:
    return (box[0] >= column[0] - _COLUMN_TOL
            and box[2] <= column[1] + _COLUMN_TOL)


def _grow(caption_box, barriers, elements, upward: bool, walls=(),
          gapped=(), column=None) -> float:
    """Find how far the figure/table body extends away from its caption.

    Repeatedly steps to the nearest element on the far side of the caption and
    stops when a barrier is nearer than that element — the first body-text line
    ends the region. Only a barrier overlapping the caption's x-span bounds the
    walk; the walls confine it to the caption's column (`_column`), so an
    element is reachable only if it stays in the column of the region it would
    end (or in `column`, when given), and no element carries it past one across
    the caption's x-span. Past its first
    step, elements of `gapped` end it instead when they lie more than
    `_NOTE_GAP` away. Returns the far edge (a y coordinate).
    """
    elements = [e for e in elements if _in_column(e, column or _column(
        caption_box, walls, *((e[1], caption_box[3]) if upward
                              else (caption_box[1], e[3]))))]
    barriers = [b for b in barriers if _across(caption_box, b)]
    hard = [w for w in walls if _across(caption_box, w)]
    edge = start = caption_box[1] if upward else caption_box[3]
    while True:
        if upward:
            cand = [e for e in elements if e[3] <= edge + 1 and e[1] < edge - 0.5]
            near_elem = max((e[3] for e in cand), default=None)
            near_bar = max((b[3] for b in barriers if b[3] <= edge + 1), default=None)
        else:
            cand = [e for e in elements if e[1] >= edge - 1 and e[3] > edge + 0.5]
            near_elem = min((e[1] for e in cand), default=None)
            near_bar = min((b[1] for b in barriers if b[1] >= edge - 1), default=None)
        if near_elem is None:
            break
        if near_bar is not None:
            # A barrier strictly between the caption edge and the next element
            # ends the region (`>=` would stop on a barrier that merely shares an
            # edge with the element, e.g. a label typeset flush against a plot).
            if upward and near_bar > near_elem:
                break
            if not upward and near_bar < near_elem:
                break
        step = [e for e in cand if (e[3] == near_elem if upward else e[1] == near_elem)]
        if (edge != start and all(e in gapped for e in step)
                and abs(edge - near_elem) > _NOTE_GAP):
            break
        new_edge = min(e[1] for e in step) if upward else max(e[3] for e in step)
        if (new_edge >= edge - 0.5) if upward else (new_edge <= edge + 0.5):
            break                          # no progress; avoid spinning
        if upward:
            wall = max((w[3] for w in hard if w[3] <= edge + 1),
                       default=float("-inf"))
            if new_edge < wall:
                break
            new_edge = _passed(elements, edge, new_edge, True, wall)
        else:
            wall = min((w[1] for w in hard if w[1] >= edge - 1),
                       default=float("inf"))
            if new_edge > wall:
                break
            new_edge = _passed(elements, edge, new_edge, False, wall)
        edge = new_edge
    return edge


def _passed(elements, edge: float, new_edge: float, upward: bool,
            wall: float) -> float:
    """`new_edge` moved on over the elements whose near end the step from
    `edge` to it crossed, and that end short of `wall`: they lie in the region
    already."""
    while True:
        if upward:
            crossed = [e[1] for e in elements if wall <= e[1] < new_edge - 0.5
                       and new_edge - 0.5 <= e[3] <= edge + 1]
            if not crossed:
                return new_edge
            new_edge = min(crossed)
        else:
            crossed = [e[3] for e in elements if wall >= e[3] > new_edge + 0.5
                       and edge - 1 <= e[1] <= new_edge + 0.5]
            if not crossed:
                return new_edge
            new_edge = max(crossed)


def _ruled_floats(drawings, cuts=()) -> list:
    """Boxes of floats framed by rules.

    Two or more horizontal rules that start and end together with no box of
    `cuts` between them, grown by every drawing within their width that
    touches the box.
    """
    rules = _horizontals(drawings)
    groups = []
    for r in rules:
        for g in groups:
            if (abs(g[0][0] - r[0]) <= _RULE_ALIGN
                    and abs(g[0][2] - r[2]) <= _RULE_ALIGN):
                g.append(r)
                break
        else:
            groups.append([r])
    runs = []
    for g in groups:
        g.sort(key=lambda r: r[1])
        run = [g[0]]
        for prev, r in zip(g, g[1:]):
            if any(c[1] >= prev[3] - 0.5 and c[3] <= r[1] + 0.5
                   and c[0] < r[2] and c[2] > r[0] for c in cuts):
                runs.append(run)
                run = []
            run.append(r)
        runs.append(run)
    out = []
    for run in runs:
        if len(run) < 2:
            continue
        x0, x1 = min(r[0] for r in run), max(r[2] for r in run)
        y0, y1 = min(r[1] for r in run), max(r[3] for r in run)
        within = [d for d in drawings
                  if d[0] >= x0 - _RULE_ALIGN and d[2] <= x1 + _RULE_ALIGN]
        grown = True
        while grown:
            grown = False
            for d in within:
                outside = d[1] < y0 or d[3] > y1
                if (outside and d[3] >= y0 - _RULE_TOUCH
                        and d[1] <= y1 + _RULE_TOUCH):
                    y0, y1, grown = min(y0, d[1]), max(y1, d[3]), True
        out.append((x0, y0, x1, y1))
    return out


def _borders(a, b, boxes) -> bool:
    """Do boxes `a` and `b`, one above the other, face each other with none
    of `boxes` between them over their common width?"""
    upper, lower = (a, b) if a[1] <= b[1] else (b, a)
    strip = (max(a[0], b[0]), upper[3], min(a[2], b[2]), lower[1])
    if strip[3] <= strip[1]:
        return True
    return not any(_overlaps(e, strip) and not _contains(a, e)
                   and not _contains(b, e) for e in boxes)


def _owners(floats, captions, figure_captions, boxes) -> dict:
    """{index into `floats`: the caption owning that ruled float}.

    A caption owns the one float it borders (`_borders` with `boxes`, across
    its x-span, above or below it),
    or of two the one no other caption owns, unless a caption still unsettled
    borders that float nearer; a caption bordering two floats that nothing
    else settles owns neither. A caption of `figure_captions` borders only
    what lies above it, its figure's side.
    """
    def gap(c, f):
        return f[1] - c[3] if f[1] >= c[3] - 1 else c[1] - f[3]

    pending = {c: [i for i, f in enumerate(floats)
                   if _across(c, f) and _borders(c, f, boxes)
                   and (_beyond(c, f, True) or _beyond(c, f, False))
                   and (c not in figure_captions or _beyond(c, f, True))]
               for c in captions}
    owner, progress = {}, True
    while progress:
        progress = False
        for c in list(pending):
            free = [i for i in pending[c] if i not in owner]
            if len(free) == 1:
                i = free[0]
                if any(o != c and i in pending[o]
                       and gap(o, floats[i]) < gap(c, floats[i]) for o in pending):
                    continue
                owner[i] = c
            if len(free) <= 1:
                del pending[c]
                progress = True
    return owner


def _foreign_floats(caption_box, parts: _Parts) -> list:
    """Ruled floats across the caption's x-span, overlapping it by no more
    than `_COLUMN_TOL`, that are another float's: they reach out of the
    caption's column, or another caption owns them (`_owners`); each grown by
    its notes (`_with_notes`)."""
    floats = _ruled_floats(parts.drawings, parts.barriers + [caption_box])
    owner = _owners(floats, [caption_box] + parts.captions,
                    parts.figure_captions, parts.elements + parts.barriers)
    out = []
    for i, box in enumerate(floats):
        core = (box[0] + _COLUMN_TOL, box[1] + _COLUMN_TOL,
                box[2] - _COLUMN_TOL, box[3] - _COLUMN_TOL)
        if not _across(caption_box, box) or _overlaps(core, caption_box):
            continue
        column = _column(caption_box, parts.walls, min(box[1], caption_box[1]),
                         max(box[3], caption_box[3]))
        if not _in_column(box, column) or owner.get(i, caption_box) != caption_box:
            out.append(_with_notes(box, [t for t, _ in parts.texts]))
    return out


def _beside_floats(caption_box, parts: _Parts) -> list:
    """Elements of the floats whose captions stand beside this caption.

    Such an element lies across that caption's x-span with its middle outside
    this one's, on that caption's float side (only above a figure's), with no
    barrier across that caption between them.
    """
    out = []
    for cap in parts.captions:
        if _across(caption_box, cap):
            continue
        bars = [b for b in parts.barriers if b != cap and _across(cap, b)]
        for e in parts.elements:
            middle = (e[0] + e[2]) / 2
            if (not _across(cap, e)
                    or caption_box[0] <= middle <= caption_box[2]):
                continue
            if _beyond(cap, e, True):
                lo, hi = e[3], cap[1]
            elif _beyond(cap, e, False) and cap not in parts.figure_captions:
                lo, hi = cap[3], e[1]
            else:
                continue
            if not any(b[1] >= lo - 0.5 and b[3] <= hi + 0.5 for b in bars):
                out.append(e)
    return out


def _cell_rows(texts, column) -> list:
    """(row box, block boxes) for each row of separate cells among `texts`.

    A row is the text lines in the column whose centres share a height; it
    holds cells when two of its lines stand at least `_CELL_GAP` apart.
    """
    lines = []
    for box, block in texts:
        if not _in_column(box, column):
            continue
        for ln in block.get("lines") or ():
            lb = ln["bbox"]
            lines.append(((lb[1] + lb[3]) / 2, tuple(lb), box))
    lines.sort()
    rows, i = [], 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1][0] - lines[i][0] <= _ROW_TOL:
            j += 1
        group = sorted(lines[i:j + 1], key=lambda ln: ln[1][0])
        if any(b[1][0] - a[1][2] >= _CELL_GAP for a, b in zip(group, group[1:])):
            rows.append(((min(g[1][0] for g in group), min(g[1][1] for g in group),
                          max(g[1][2] for g in group), max(g[1][3] for g in group)),
                         {g[2] for g in group}))
        i = j + 1
    return rows


def _between_rows(box, cells) -> bool:
    """Is `box` a row of one table: a line or less from a row of cells above it
    and one below it that span the same x-extent, and within that extent?"""
    height = box[3] - box[1]
    above = [r for r, _ in cells
             if r[3] <= box[1] + _ROW_TOL and box[1] - r[3] <= height]
    below = [r for r, _ in cells
             if r[1] >= box[3] - _ROW_TOL and r[1] - box[3] <= height]
    if not above or not below:
        return False
    a = max(above, key=lambda r: r[3])
    b = min(below, key=lambda r: r[1])
    shared = min(a[2], b[2]) - max(a[0], b[0])
    if shared < _SAME_EXTENT * max(a[2] - a[0], b[2] - b[0]):
        return False
    return (box[0] >= min(a[0], b[0]) - _ROW_TOL
            and box[2] <= max(a[2], b[2]) + _ROW_TOL)


def _beyond(caption_box, box, upward: bool) -> bool:
    """Does `box` lie on the `upward` side of the caption?"""
    return box[3] <= caption_box[1] + 1 if upward else box[1] >= caption_box[3] - 1


def _table_stops(caption_box, parts: _Parts, body: Body, upward: bool,
                 cells: list) -> list:
    """Boxes of body-size text on the `upward` side that end a table there.

    Body-size text is part of the table only inside its drawings — a drawing
    that covers its width starts above it and one ends below it, short of the
    next barrier — in a row of cells, or as a row between rows of cells
    (`_between_rows`).
    """
    in_cells = set().union(*(blocks for _, blocks in cells)) if cells else set()
    across = [b for b in parts.barriers if _across(caption_box, b)]
    stops = []
    for box, block in parts.texts:
        if (not _beyond(caption_box, box, upward) or box in in_cells
                or not _at_body_size(block, body.size)
                or _between_rows(box, cells)):
            continue
        cover = [d for d in parts.drawings if _beyond(caption_box, d, upward)
                 and d[0] <= box[0] + _COVER_TOL and d[2] >= box[2] - _COVER_TOL]
        if upward:
            limit = max((b[3] for b in across if b[3] <= box[1] + 1),
                        default=float("-inf"))
            cover = [d for d in cover if d[3] > limit]
        else:
            limit = min((b[1] for b in across if b[1] >= box[3] - 1),
                        default=float("inf"))
            cover = [d for d in cover if d[1] < limit]
        starts_above = any(d[1] <= box[1] + _COVER_TOL for d in cover)
        ends_below = any(d[3] >= box[3] - _COVER_TOL for d in cover)
        if not (starts_above and ends_below):
            stops.append(box)
    return stops


def _ends_at_caption(caption_box, parts: _Parts, barriers, edge: float,
                     upward: bool, cells: list) -> bool:
    """Is the first of `barriers` across the caption's x-span past `edge`, on
    the `upward` side, another float's caption claiming what lies before it?

    A figure's caption claims only from below, always; a table's does not when
    structure (a drawing or a block of cells) borders it on its other side,
    its own float.
    """
    across = [b for b in barriers if _across(caption_box, b)]
    if upward:
        nearest = max((b for b in across if b[3] <= edge + 1),
                      key=lambda b: b[3], default=None)
    else:
        nearest = min((b for b in across if b[1] >= edge - 1),
                      key=lambda b: b[1], default=None)
    if (nearest is None or nearest not in parts.captions
            or (upward and nearest in parts.figure_captions)):
        return False
    if nearest in parts.figure_captions:
        return True
    structure = parts.drawings + [b for _, blocks in cells for b in blocks]
    return not any(_across(nearest, s) and _beyond(nearest, s, upward)
                   and _borders(nearest, s, parts.elements + parts.barriers)
                   for s in structure)


def _table_body(caption_box, parts: _Parts, body: Body, cells: list,
                column=None):
    """(upward, edge, stops) for the side of a table caption holding its body.

    The side whose walk reaches table structure — a drawing, or one of two or
    more rows of cells — unless the walk ends at another float's caption, whose
    structure that is; of those, the side whose rows of cells come nearest the
    caption (one with fewer than two after), then the one whose structure
    does, each to 0.1 pt; below on a tie. None when neither side has any.
    `column`, when given, is the column every walk keeps to.
    """
    def gap(boxes, upward):
        return round(min(caption_box[1] - b[3] if upward else b[1] - caption_box[3]
                         for b in boxes), 1)

    best = None
    for upward in (False, True):
        stops = _table_stops(caption_box, parts, body, upward, cells)
        elements = [e for e in parts.elements if e not in stops]
        barriers = parts.barriers + stops
        edge = _grow(caption_box, barriers, elements, upward, parts.walls,
                     column=column)
        lo, hi = (edge, caption_box[1]) if upward else (caption_box[3], edge)
        if hi - lo <= 2:
            continue
        side = column or _column(caption_box, parts.walls, lo, hi)
        structure = [d for d in parts.drawings if d[3] > lo + 0.5
                     and d[1] < hi - 0.5 and _in_column(d, side)]
        rows = [row for row, _ in cells if row[3] > lo + 0.5
                and row[1] < hi - 0.5 and _in_column(row, side)]
        structure += rows if len(rows) >= 2 else []
        if not structure:
            continue
        rank = (_ends_at_caption(caption_box, parts, barriers, edge, upward, cells),
                gap(rows, upward) if len(rows) >= 2 else float("inf"),
                gap(structure, upward))
        if best is None or rank < best[0]:
            best = (rank, upward, edge, stops)
    return best[1:] if best else None


def _boxed_prose(caption_box, parts: _Parts, edge: float, upward: bool) -> bool:
    """Does the walk to `edge` stop at body prose boxed into the table: a wall
    across the caption's x-span within `_NOTE_GAP` past `edge`, with aligned
    rules within `_NOTE_GAP` above and below it, or the nearest such wall past
    `edge` beside a column rule of a ruled float (`_grid_columns`)?"""
    rules = _horizontals(parts.drawings)
    walls = [w for w in parts.walls
             if w not in parts.captions and _across(caption_box, w)]
    beyond = [w for w in walls if (w[3] <= edge + 1 if upward else w[1] >= edge - 1)]
    if beyond:
        nearest = (max(beyond, key=lambda w: w[3]) if upward
                   else min(beyond, key=lambda w: w[1]))
        floats = _ruled_floats(parts.drawings, parts.captions + [caption_box])
        if _grid_columns(nearest, floats, _verticals(parts.drawings)):
            return True
    for w in walls:
        gap = edge - w[3] if upward else w[1] - edge
        if not -1 <= gap <= _NOTE_GAP:
            continue
        above = [r for r in rules if -0.5 <= w[1] - r[3] <= _NOTE_GAP and _across(w, r)]
        below = [r for r in rules if -0.5 <= r[1] - w[3] <= _NOTE_GAP and _across(w, r)]
        if any(abs(a[0] - b[0]) <= _RULE_ALIGN and abs(a[2] - b[2]) <= _RULE_ALIGN
               for a in above for b in below):
            return True
    return False


def _table_end(caption_box, parts: _Parts, upward: bool, edge: float,
               elements, cells, column=None, stops=()):
    """Where a table walked to `edge` ends, or `RUNS_ON` when it cannot tell.

    Its first part is its ruled float nearest the caption, and each next one
    the ruled float that continues it (`_table_part`) or, unless the walk
    ends at another float's caption claiming what lies before it
    (`_ends_at_caption`, `stops` among the barriers), that it runs on to
    (`_run_on`). It ends at the far edge of its last part, and of what is
    printed within `_NOTE_GAP` past it, when what follows before `edge`
    starts more than `_NOTE_GAP` away or, under a claiming caption, is a
    graphic or text a graphic starts beside. It ends at `edge` when nothing
    follows, or a row of cells within twice `_NOTE_GAP` and within the
    table's width, which a claiming caption leaves to the table as printed
    past it.
    """
    lo, hi = (edge, caption_box[1]) if upward else (caption_box[3], edge)
    column = column or _column(caption_box, parts.walls, lo, hi)
    ruled = [r for r in _ruled_floats(parts.drawings, parts.barriers + [caption_box])
             if _across(caption_box, r) and _in_column(r, column)
             and r[1] >= lo - 1 and r[3] <= hi + 1]
    if not ruled:
        return edge
    claimed = _ends_at_caption(caption_box, parts, parts.barriers + list(stops), edge,
                               upward, cells)
    texts = {t for t, _ in parts.texts}
    table = max(ruled, key=lambda r: r[3]) if upward else min(ruled, key=lambda r: r[1])
    in_cells = set().union(*(blocks for _, blocks in cells)) if cells else set()
    past = [e for e in elements if _in_column(e, column) and not _contains(table, e)]
    far = table[1] if upward else table[3]
    while True:
        if upward:
            nearest = max((e for e in past if e[3] <= far + 0.5 and e[1] >= edge - 0.5),
                          key=lambda e: e[3], default=None)
            gap = None if nearest is None else far - nearest[3]
        else:
            nearest = min((e for e in past if e[1] >= far - 0.5 and e[3] <= edge + 0.5),
                          key=lambda e: e[1], default=None)
            gap = None if nearest is None else nearest[1] - far
        if nearest is None:
            return edge
        if claimed and nearest in texts and any(
                e not in texts and (nearest[1] < e[3] <= far + 0.5 if upward
                                    else far - 0.5 <= e[1] < nearest[3])
                for e in past):
            return far
        row = (nearest in in_cells and gap <= 2 * _NOTE_GAP
               and nearest[0] >= table[0] - _RULE_ALIGN
               and nearest[2] <= table[2] + _RULE_ALIGN)
        if row and not claimed:
            return edge
        if not row and (gap > _NOTE_GAP or (claimed and nearest not in texts)):
            part = _table_part(caption_box, parts, table, ruled, far, upward, cells)
            if part is None and not claimed:
                part = _run_on(parts, table, ruled, far, upward, cells, past, column)
            if part is None or part == RUNS_ON:
                return far if part is None else part
            table, far = part, part[1] if upward else part[3]
            past = [e for e in past if not _contains(part, e)]
            continue
        far = min(far, nearest[1]) if upward else max(far, nearest[3])
        past.remove(nearest)


def _held_rows(parts: _Parts, table, part, cells) -> list:
    """The rows of `cells` within `part` with a block at the size of a block
    in the rows within `table`, each as the sizes of its blocks."""
    sizes = {box: _body_size([block]) for box, block in parts.texts}

    def row_sizes(box):
        return [[sizes[b] for b in blocks if b in sizes]
                for row, blocks in cells if _contains(box, row)]

    own = [size for row in row_sizes(table) for size in row]
    return [row for row in row_sizes(part)
            if any(abs(a - b) <= _BODY_SIZE_TOL for a in row for b in own)]


def _table_part(caption_box, parts: _Parts, table, ruled, far: float,
                upward: bool, cells):
    """The ruled float of `ruled` nearest past `far` on the `upward` side when
    it continues `table`, else None.

    It continues the table when it is as wide (`_SAME_EXTENT`), holds two or
    more rows of cells at the size of the table's own (`_held_rows`) or
    carries on every one of its vertical rules, lies in no frame (a drawing
    enclosing it), and does not run into another float's caption (the
    nearest barrier past it across the caption's x-span, with no drawing
    between).
    """
    beyond = [r for r in ruled if r is not table
              and (r[3] <= far + 0.5 if upward else r[1] >= far - 0.5)
              and min(r[2], table[2]) - max(r[0], table[0])
              >= _SAME_EXTENT * max(r[2] - r[0], table[2] - table[0])]
    if not beyond:
        return None
    part = max(beyond, key=lambda r: r[3]) if upward else min(beyond, key=lambda r: r[1])
    held = _held_rows(parts, table, part, cells)
    verticals = _verticals(parts.drawings)

    def rule_xs(box):
        return [(v[0] + v[2]) / 2 for v in verticals if _contains(box, v)]

    carried = rule_xs(table) and all(
        any(abs(x - y) <= _RULE_ALIGN for y in rule_xs(part)) for x in rule_xs(table))
    if len(held) < 2 and not carried:
        return None
    if any(d[2] - d[0] > _RULE_MAX_THICK and d[3] - d[1] > _RULE_MAX_THICK
           and _contains(d, part) for d in parts.drawings):
        return None
    end = part[1] if upward else part[3]
    across = [b for b in parts.barriers if _across(caption_box, b)
              and (b[3] <= end + 1 if upward else b[1] >= end - 1)]
    if across:
        nxt = max(across, key=lambda b: b[3]) if upward else min(across, key=lambda b: b[1])
        lo, hi = (nxt[3], end) if upward else (end, nxt[1])
        if nxt in parts.captions and not any(
                d[3] > lo + 0.5 and d[1] < hi - 0.5 and not _member(part, d)
                for d in parts.drawings):
            return None
    return part


def _run_on(parts: _Parts, table, ruled, far: float, upward: bool, cells,
            past, column):
    """The ruled float of `ruled` nearest past `far` on the `upward` side
    when `table` runs on to it; None when there is none, a barrier in
    `column` stands between them or it is an algorithm box (text over it or
    between opens on `_ALGORITHM`); else `RUNS_ON`.

    The table runs on to it when nothing but text of `past` lies between
    them and it holds a row of cells at the size of the table's own
    (`_held_rows`).
    """
    beyond = [r for r in ruled if r is not table
              and (r[3] <= far + 0.5 if upward else r[1] >= far - 0.5)]
    if not beyond:
        return None
    part = max(beyond, key=lambda r: r[3]) if upward else min(beyond, key=lambda r: r[1])
    lo, hi = (part[3], far) if upward else (far, part[1])
    span = (min(table[0], part[0]), max(table[2], part[2]))

    def between(box):
        return (box[1] >= lo - 0.5 and box[3] <= hi + 0.5
                and box[0] < span[1] and box[2] > span[0])

    if any(between(b) and _in_column(b, column) for b in parts.barriers):
        return None
    if any((between(box) or _overlaps(part, box)) and _ALGORITHM.match(_block_text(block))
           for box, block in parts.texts):
        return None
    texts = {t for t, _ in parts.texts}
    if (any(between(e) and e not in texts and not _member(part, e) for e in past)
            or not _held_rows(parts, table, part, cells)):
        return RUNS_ON
    return part


NO_TABLE_BESIDE = "no table structure beside its caption"
NOTHING_BESIDE = "nothing to crop beside its caption"
NO_GRAPHICS = "the crop holds no drawing or image"
HOLDS_PROSE = "the crop holds body prose"
HOLDS_CAPTION = "the crop holds another float's caption"
HOLDS_HEADING = "the crop holds a heading"
HOLDS_FLOAT = "the crop holds another caption's ruled float"
RUNS_ON = "a ruled block no caption claims follows the table"


def float_crop(page, kind: str, num, band=(float("-inf"), float("inf")),
               body: Body = None) -> tuple:
    """(rect, problem, caption) for `kind num` on `page`.

    Each of its captions (`caption_boxes`) is tried in turn: rect is the crop of
    the first that yields one, and caption the text of that caption's opening
    block. Without a crop, rect and caption are None and problem is the first
    caption's reason — None too when the caption is not on the page. `body` is
    the document's (`body_text`), computed here when not given.
    """
    found = caption_boxes(page, kind, num)
    if not found:
        return None, None, None
    if body is None:
        body = body_text(page.parent)
    first = None
    for caption_box, head in found:
        rect, problem = _crop_at(page, caption_box, kind, num, band, body)
        if rect is not None:
            return rect, None, _block_text(head)
        first = first or problem
    return None, first, None


def _crop_at(page, caption_box, kind: str, num, band, body: Body) -> tuple:
    """(rect, None) for the float captioned at `caption_box`, else (None,
    problem)."""
    import fitz

    parts = _page_parts(page, caption_box, body, band)
    blocks = page.get_text("dict")["blocks"]
    # ANOTHER float's caption is a hard boundary. The walk stops at body prose, but
    # a figure stacked directly above another figure or a table has no prose between
    # them, so the region kept growing: one crop swallowed the table AND the figure
    # above the one being rendered. Whatever sits beyond another caption belongs to
    # that float, never to this one.
    rules = _Rules(page)
    for b in blocks:
        if b.get("type") != 0:
            continue
        found = _other_caption(b, parts, kind, num)
        if found is None:
            continue
        # _page_parts classifies a caption line as an ELEMENT (it looks like a
        # sub-caption), and the walk steps over elements — so it is dropped from
        # the elements as it becomes a barrier. The block's lines before and
        # past the caption (`_caption_lines`) stay content.
        other, start = found
        lines = b["lines"][start:]
        taken = _caption_lines({"lines": lines}, rules)
        box = _union(ln["bbox"] for ln in lines[:taken])
        whole = tuple(b["bbox"])
        parts.barriers = [e for e in parts.barriers if e != whole]
        parts.walls = [e for e in parts.walls if e != whole]
        parts.loose = [e for e in parts.loose if e != whole]
        parts.texts = [t for t in parts.texts if not _overlaps(t[0], box)]
        for rest in (b["lines"][:start], lines[taken:]):
            if rest:
                block = {"type": 0, "lines": rest,
                         "bbox": _union(ln["bbox"] for ln in rest)}
                parts.texts.append((tuple(block["bbox"]), block))
                parts.elements.append(tuple(block["bbox"]))
        parts.barriers.append(box)
        parts.walls.append(box)
        parts.captions.append(box)
        if base_kind(other[0]) == "figure":
            parts.figure_captions.append(box)
        parts.elements = [e for e in parts.elements if not _overlaps(e, box)]
    if base_kind(kind) == "figure":
        for box in (_on_backdrop(parts, caption_box, page.rect)
                    + _subcaptions(blocks, parts, caption_box)):
            parts.make_element(box)
    for box in _ruled_floats(parts.drawings, parts.barriers + [caption_box]):
        column = _column(caption_box, parts.walls, min(box[1], caption_box[1]),
                         max(box[3], caption_box[3]))
        if not _across(caption_box, box) and not _in_column(box, column):
            parts.drop(box)
    if base_kind(kind) == "table":
        grid = _ruled_floats(parts.drawings, parts.captions + [caption_box])
        verticals = _verticals(parts.drawings)
        for box in [b for b in parts.barriers if b not in parts.captions
                    and _in_cell(b, grid, verticals)]:
            parts.make_element(box)
    foreign = _foreign_floats(caption_box, parts)
    foreign += [a for a in _algorithms(blocks, _horizontals(parts.drawings), body,
                                       caption_box) if a not in foreign]
    for box in foreign:
        parts.make_barrier(box)
    # Print set inside a ruled float of this caption's is that float's content.
    ruled = [r for r in _ruled_floats(parts.drawings, parts.walls + [caption_box])
             if not any(_overlaps(r, f) for f in foreign)]
    enclosed = [b for b in parts.loose if any(_contains(r, b) for r in ruled)]
    parts.barriers = [b for b in parts.barriers if b not in enclosed]
    parts.loose = [b for b in parts.loose if b not in enclosed]
    parts.elements += enclosed
    beside = set(_beside_floats(caption_box, parts))
    parts.elements = [e for e in parts.elements if e not in beside]
    parts.texts = [t for t in parts.texts if t[0] not in beside]
    cells = _cell_rows(parts.texts, _column(caption_box, parts.walls,
                                            float("-inf"), float("inf")))
    found = _region(caption_box, kind, parts, body, cells)
    if isinstance(found, str):
        return None, found
    upward, top, bottom, elements = found
    # Everything that lies in the band the walk delimited — NOT just the boxes it
    # stepped on. The walk moves by the nearest bottom edge, so a sibling that is
    # vertically enclosed by an already-crossed element (a plot's legend beside
    # its axes, a rotated axis title) is never stepped on, and taking only the
    # stepped-on boxes clipped those off the side of the figure.
    column = _column(caption_box, parts.walls, min(top, caption_box[1]),
                     max(bottom, caption_box[3]))
    wide = _clean_column(caption_box, parts.walls, column, elements, top, bottom)
    if wide != column:
        found = _region(caption_box, kind, parts, body, cells, wide)
        if isinstance(found, str):
            return None, found
        upward, top, bottom, elements = found
        column = wide
    texts = {t for t, _ in parts.texts}
    visible = _visible(page)
    off = [e for e in elements if _too_big(e, page.rect) and (e in texts or (
        visible.get(e) and (_horizontals([visible[e]]) or _verticals([visible[e]]))))]
    taken = [e for e in elements
             if e[3] > top + 0.5 and e[1] < bottom - 0.5
             and (e in off or not _too_big(e, page.rect)) and _in_column(e, column)
             and (e[3] <= caption_box[3] if upward else e[1] >= caption_box[1])]
    boxes = [_on_page(visible.get(e, e), page.rect) if e in off else e for e in taken]
    boxes += [b for b in parts.loose
              if b[1] >= top - 0.5 and b[3] <= bottom + 0.5 and _in_column(b, column)
              and (b not in parts.small or _across(caption_box, b))]
    if not boxes:
        return None, NOTHING_BESIDE
    if base_kind(kind) == "table":
        boxes, top, bottom = _held_to_structure(page, parts, boxes, column, top,
                                                bottom, upward, body)
    # Padding never reaches back into the caption the crop just left out (nor
    # its glyphs), nor past the content into the running head or foot, nor
    # into a wall, a foreign float or an element, no rule, the crop leaves out
    # past its far edge; content keeps its width.
    seen = [v for v in (visible.get(b, b) for b in boxes) if v is not None]
    x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
    if upward:
        drawn = [v for v in (visible.get(b, b) for b in boxes if b in parts.drawings)
                 if v is not None]
        head = _head_print(page, band, drawn, x0, x1)
        boxes, seen = boxes + head, seen + head
        top = min([top] + [h[1] for h in head])
    left_out = [e for e in elements
                if e not in taken and e[0] < x1 and e[2] > x0
                and not _horizontals([e]) and not _verticals([e])
                and (e[1] < top - 0.5 and e[3] <= top + 0.5 if upward
                     else e[3] > bottom + 0.5 and e[1] >= bottom - 0.5)]
    across = [w for w in parts.walls + foreign if _across(caption_box, w)]
    top_limit = max([min(band[0], min((v[1] for v in seen), default=top)),
                     _CLAMP if upward else max(_CLAMP, caption_box[3])]
                    + [w[3] for w in across if w[3] <= top + 0.5]
                    + [e[3] for e in left_out if upward])
    if not upward:
        under = [e[1] for e in parts.elements + parts.barriers
                 if e[1] >= caption_box[3] and e[0] < x1 and e[2] > x0]
        top_limit = max(top_limit, _ink_end(page, caption_box,
                                            min(under, default=bottom)))
    bottom_limit = min([max(band[1], max((v[3] for v in seen), default=bottom)),
                        min(page.rect.height - _CLAMP, caption_box[1])
                        if upward else page.rect.height - _CLAMP]
                       + [w[1] for w in across if w[1] >= bottom - 0.5]
                       + [e[1] for e in left_out if not upward])
    beside = [w for w in parts.walls + foreign if w[1] < bottom and w[3] > top]
    left_limit = max([_CLAMP] + [w[2] for w in beside if w[2] <= x0 + _COLUMN_TOL])
    right_limit = min([page.rect.width - _CLAMP]
                      + [w[0] for w in beside if w[0] >= x1 - _COLUMN_TOL])
    rect = fitz.Rect(
        max(x0 - _PAD, min(x0, left_limit)),
        max(top - _PAD, top_limit),
        min(x1 + _PAD, max(x1, right_limit)),
        min(bottom + _PAD, bottom_limit))
    if rect.width <= 40 or rect.height <= 20:
        return None, NOTHING_BESIDE
    problem = _crop_problem(page, rect, kind, num, caption_box, body, band)
    return (None, problem) if problem else (rect, None)


def _on_backdrop(parts: _Parts, caption_box, page_rect) -> list:
    """The loose barriers (`_Parts.loose`) ending over the caption that lie in
    a backdrop, under an image or another drawing of `parts` that shares some
    of the barrier's x-span and ends between the backdrop's top and the
    barrier's: print on a figure's backdrop. A backdrop is a drawing of `parts`
    across the caption's x-span that runs from over it to under it, is not
    `_too_big` and holds no wall (`_Parts.walls`)."""
    backdrops = [d for d in parts.drawings if _across(caption_box, d)
                 and d[1] < caption_box[1] and d[3] > caption_box[3]
                 and not _too_big(d, page_rect)
                 and not any(_contains(d, w) for w in parts.walls)]
    texts = {t for t, _ in parts.texts}
    graphics = [e for e in parts.elements if e not in texts and e not in backdrops]
    return [b for b in parts.loose if b[3] <= caption_box[1] + 0.5
            and any(_contains(d, b) and any(
                d[1] <= g[3] <= b[1] + 0.5 and g[0] < b[2] and g[2] > b[0]
                for g in graphics)
                for d in backdrops)]


def _subcaptions(blocks, parts: _Parts, caption_box) -> list:
    """Boxes of the small print (`_Parts.small`) among the text `blocks` that
    opens on a panel's mark (`_PANEL_MARK`), ends over the caption and opens
    within `_PANEL_GAP` under an image or a drawing of `parts` that is no rule
    (`_horizontals`) and shares some of its x-span: a figure's subcaptions."""
    texts = {t for t, _ in parts.texts}
    graphics = [e for e in parts.elements
                if e not in texts and not _horizontals([e])]
    out = []
    for b in blocks:
        box = tuple(b["bbox"])
        if (b.get("type") == 0 and box in parts.small
                and _PANEL_MARK.match(_block_text(b))
                and box[3] <= caption_box[1] + 0.5
                and any(g[0] < box[2] and g[2] > box[0]
                        and box[1] - _PANEL_GAP <= g[3] <= box[1] + 0.5
                        for g in graphics)):
            out.append(box)
    return out


def _other_caption(block, parts: _Parts, kind: str, num):
    """((kind, label), index) of the caption, not `kind num`'s, that opens on
    the block's line `index`: line 0 (`caption_kind` of the block's text) or,
    in a block that is no barrier of `parts`, its first later line opening
    one with a separator (`_separated`: a cell can cite `Table A9`); else
    None."""
    found = caption_kind(_block_text(block)), 0
    if found[0] is None and tuple(block["bbox"]) not in parts.barriers:
        texts = ["".join(s["text"] for s in ln["spans"]) for ln in block["lines"]]
        found = next(((caption_kind(text), i) for i, text in enumerate(texts)
                      if i and _separated(text) and caption_kind(text)), found)
    return None if found[0] in (None, (kind, num)) else found


def _on_page(box, page_rect) -> tuple:
    """`box` cut to the page."""
    return (max(box[0], 0.0), max(box[1], 0.0), min(box[2], page_rect.width),
            min(box[3], page_rect.height))


def _head_print(page, band, drawn, x0: float, x1: float) -> list:
    """Boxes of the page's text lines over `band` (its running head row)
    within x0..x1 that come within `_RULE_TOUCH` of one of the `drawn`
    boxes: a float's print, not furniture."""
    out = []
    for box, _ in _page_lines(page):
        if box[3] > band[0] or box[0] < x0 - 0.5 or box[2] > x1 + 0.5:
            continue
        near = (box[0] - _RULE_TOUCH, box[1] - _RULE_TOUCH,
                box[2] + _RULE_TOUCH, box[3] + _RULE_TOUCH)
        if any(_overlaps(near, d) for d in drawn):
            out.append(box)
    return out


def _visible(page) -> dict:
    """{box: the part of it left visible, or None} for each of the page's
    vector drawings: its box cut to the clip paths in force where it is
    drawn."""
    out, clips = {}, []
    for d in page.get_cdrawings(extended=True):
        clips = [c for c in clips if c[0] < d["level"]]
        if d["type"] == "clip":
            clips.append((d["level"], tuple(d["scissor"])))
        elif d["type"] != "group":
            box = seen = tuple(d["rect"])
            for _, c in clips:
                seen = (max(seen[0], c[0]), max(seen[1], c[1]),
                        min(seen[2], c[2]), min(seen[3], c[3]))
            out[box] = seen if seen[0] <= seen[2] and seen[1] <= seen[3] else None
    return out


def _ink_end(page, box, limit: float) -> float:
    """The top of the first row of pixels with no ink (`_INK`, at `_DPI`)
    under `box` and over `limit`: the foot of the glyphs set in `box` when
    they reach past it. The bottom of `box` when there is no such row."""
    import fitz

    if limit <= box[3]:
        return box[3]
    zoom = _DPI / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY,
                          clip=fitz.Rect(box[0], box[3], box[2], limit))
    for y in range(pix.height):
        if min(pix.samples[y * pix.stride:y * pix.stride + pix.width]) >= _INK:
            return max(box[3], (pix.y + y) / zoom)
    return box[3]


def _algorithms(blocks, rules, body: Body, caption_box) -> list:
    """Boxes of the ruled algorithm boxes on a page of text `blocks` and
    horizontal `rules`, beside the float captioned at `caption_box`.

    One opens with a block, no body prose, whose first line opens
    `Algorithm N`. The nearest rule under that line and across its width,
    within the block or `_NOTE_GAP` under it, and the next rule of that
    extent under that one close the box; it reaches up to the rule of that
    extent within `_NOTE_GAP` over the line, or else to the line. A box that
    would run over the caption ends at the caption's top.
    """
    out = []
    for b in blocks:
        if (b.get("type") != 0 or not b.get("lines") or _is_prose(b, body)
                or not _ALGORITHM.match(_block_text(b))):
            continue
        line = b["lines"][0]["bbox"]
        under = sorted((r for r in rules if r[1] >= line[3] - 1
                        and r[0] <= line[0] + _RULE_ALIGN
                        and r[2] >= line[2] - _RULE_ALIGN), key=lambda r: r[1])
        if not under or under[0][1] > b["bbox"][3] + _NOTE_GAP:
            continue
        first = under[0]

        def extent(r):
            return (abs(r[0] - first[0]) <= _RULE_ALIGN
                    and abs(r[2] - first[2]) <= _RULE_ALIGN)

        last = [r for r in under[1:] if extent(r)]
        if not last:
            continue
        over = [r for r in rules if extent(r) and -1 <= line[1] - r[3] <= _NOTE_GAP]
        top = max(over, key=lambda r: r[3])[1] if over else line[1]
        box = (first[0], top, first[2], last[0][3])
        if _overlaps(box, caption_box):
            box = box[:3] + (caption_box[1],)
        if box[3] > box[1]:
            out.append(box)
    return out


def _line_size(block, box) -> float:
    """The dominant font size (`_body_size`) of the block's lines within `box`."""
    return _body_size([{"type": 0, "lines": [
        ln for ln in block.get("lines") or () if _contains(box, tuple(ln["bbox"]))]}])


def _prose_lines(blocks, body: Body, caption_box=None) -> list:
    """Boxes of the printed lines (`_lines`) of the text `blocks` with at least
    `_PROSE_LINE_CHARS` characters set at body size and, when `caption_box` is
    given, those of the barriers outside its x-span that are no heading
    (`_is_barrier`, `_is_heading`): small print set as prose."""
    out = []
    for b in blocks:
        if b.get("type") != 0:
            continue
        small = (caption_box is not None and not _across(caption_box, b["bbox"])
                 and _is_barrier(b, body) and not _is_heading(b, body))
        out += [box for box, chars in _lines(b) if chars >= _PROSE_LINE_CHARS
                and (small or abs(_line_size(b, box) - body.size) <= _BODY_SIZE_TOL)]
    return out


def _held_to_structure(page, parts: _Parts, boxes, column, top: float,
                       bottom: float, upward: bool, body: Body) -> tuple:
    """(boxes, top, bottom) of a table crop kept to the x-extent of the
    drawings among `boxes` when a line of prose (`_prose_lines`) in `column`
    stands beside that extent within top..bottom: the boxes within
    `_CELL_GAP` of it, and the far edge moved to theirs; else unchanged."""
    drawn = [b for b in boxes if b in parts.drawings]
    if not drawn:
        return boxes, top, bottom
    x0, x1 = min(d[0] for d in drawn), max(d[2] for d in drawn)
    if not any(box[3] > top and box[1] < bottom and _in_column(box, column)
               and (box[2] <= x0 or box[0] >= x1)
               for box in _prose_lines(page.get_text("dict")["blocks"], body)):
        return boxes, top, bottom
    kept = [b for b in boxes if b[0] >= x0 - _CELL_GAP and b[2] <= x1 + _CELL_GAP]
    if upward:
        return kept, max(top, min(b[1] for b in kept)), bottom
    return kept, top, min(bottom, max(b[3] for b in kept))


def _region(caption_box, kind: str, parts: _Parts, body: Body, cells: list,
            column=None):
    """(upward, top, bottom, elements): the side of the caption the float
    lies on, its band and its candidate elements; or the problem when it has
    none. `column`, when given, is the column the walk keeps to."""
    if base_kind(kind) == "table":
        found = _table_body(caption_box, parts, body, cells, column)
        if found is None:
            return NO_TABLE_BESIDE
        upward, edge, stops = found
        if _boxed_prose(caption_box, parts, edge, upward):
            return HOLDS_PROSE
        elements = [e for e in parts.elements if e not in stops]
        edge = _table_end(caption_box, parts, upward, edge, elements, cells,
                          column, stops)
        if edge == RUNS_ON:
            return edge
    else:
        # Body-size text a figure reaches across a gap is the prose around it.
        upward, elements = True, parts.elements
        loose_text = _table_stops(caption_box, parts, body, True, cells)
        edge = _grow(caption_box, parts.barriers, elements, True, parts.walls,
                     loose_text, column)
    # The caption is NOT part of the image: the image block carries it as text,
    # so including it showed it twice, and a caption set in two columns under a
    # full-width figure was cut down the middle by the crop.
    top = edge if upward else caption_box[3]
    bottom = caption_box[1] if upward else edge
    if bottom - top <= 2:
        return NOTHING_BESIDE
    return upward, top, bottom, elements


def _clean_column(caption_box, walls, column, elements, top, bottom) -> tuple:
    """`column` narrowed to the caption's column over the whole page
    (`_column`) on each side where no element in top..bottom straddles that
    page-wide bound: a float that stays in one column is held to it even where
    no wall stands beside it."""
    page = _column(caption_box, walls, float("-inf"), float("inf"))
    band = [e for e in elements if e[3] > top + 0.5 and e[1] < bottom - 0.5]

    def clean(bound):
        return not any(e[0] < bound - _GUTTER and e[2] > bound + _GUTTER
                       for e in band)

    lo = page[0] if page[0] > column[0] and clean(page[0]) else column[0]
    hi = page[1] if page[1] < column[1] and clean(page[1]) else column[1]
    return lo, hi


def _holds(rect, block) -> bool:
    """Does `rect` hold a line of the block: its middle and half its width?"""
    for ln in block.get("lines") or ():
        x0, y0, x1, y1 = ln["bbox"]
        overlap = min(x1, rect.x1) - max(x0, rect.x0)
        if rect.y0 < (y0 + y1) / 2 < rect.y1 and overlap >= (x1 - x0) / 2:
            return True
    return False


def _crop_problem(page, rect, kind: str, num, caption_box, body: Body,
                  band=(float("-inf"), float("inf"))):
    """Why `rect` is no crop of `kind num` captioned at `caption_box`, or None.

    A figure crop holds a drawing or an image more than half its padding
    inside its edges. No crop holds a line (`_holds`) of another float's
    caption, a block of body prose (`_is_prose`) or `_PROSE_LINES_HELD` lines
    of prose (`_prose_lines`) that are not a float's content (`_content_line`)
    nor print of a grid of its images (`_in_image_grid`), or a ruled float
    another caption owns (`_owners`) or an algorithm box (`_algorithms`) more
    than half its padding inside its edges; a table crop holds no line at
    heading size. Our caption's own lines are left out, and so are drawings
    outside `band` (head..foot); an image as thin as a rule is a rule.
    """
    blocks = page.get_text("dict")["blocks"]
    inset = _PAD / 2
    inner = (rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
    images = [tuple(b["bbox"]) for b in blocks if b.get("type") != 0]
    drawings = [tuple(d["rect"]) for d in page.get_drawings()]
    if base_kind(kind) == "figure":
        if not any(_overlaps(g, inner) and not _too_big(g, page.rect)
                   for g in images + drawings):
            return NO_GRAPHICS
    rules = _Rules(page)
    texts, captions, figure_captions = [], [], []
    for b in blocks:
        if b.get("type") != 0:
            continue
        other = caption_kind(_block_text(b))
        if (other is None or other == (kind, num)
                or _overlaps(tuple(b["bbox"]), caption_box)):
            texts.append(b)
            continue
        lines = b["lines"][:_caption_lines(b, rules)]
        if _holds(rect, {"lines": lines}):
            return HOLDS_CAPTION
        captions.append(_union(ln["bbox"] for ln in lines))
        if base_kind(other[0]) == "figure":
            figure_captions.append(captions[-1])
    drawn = [d for d in drawings + _horizontals(images)
             if d[3] > band[0] and d[1] < band[1]]
    prose = [b for b in texts if _is_prose(b, body)]
    floats = _ruled_floats(drawn, [tuple(b["bbox"]) for b in prose]
                           + captions + [caption_box])
    frames = images + [d for d in drawn if d[2] - d[0] > _RULE_MAX_THICK
                       and d[3] - d[1] > _RULE_MAX_THICK and not _too_big(d, page.rect)]

    def content(box):
        return _content_line(box, _horizontals(drawn), floats, _verticals(drawn),
                             frames)

    def held(box):
        return (_holds(rect, {"lines": [{"bbox": box}]})
                and not _overlaps(box, caption_box))

    if any(not _overlaps(tuple(b["bbox"]), caption_box) and _holds(rect, b)
           and not content(tuple(b["bbox"])) for b in prose):
        return HOLDS_PROSE
    if base_kind(kind) == "table" and any(
            held(box) and _line_size(b, box) >= body.size + _HEADING_SIZE_DELTA
            for b in texts for box, _ in _lines(b)):
        return HOLDS_HEADING
    grid = [i for i in images if _overlaps(i, inner) and not _too_big(i, page.rect)]
    lines = [box for box in _prose_lines(texts, body, caption_box)
             if held(box) and not content(box)]
    if len([box for box in lines
            if not _in_image_grid(box, grid, lines)]) >= _PROSE_LINES_HELD:
        return HOLDS_PROSE
    figures = figure_captions + ([caption_box] if base_kind(kind) == "figure" else [])
    owner = _owners(floats, [caption_box] + captions, figures,
                    [tuple(b["bbox"]) for b in texts] + images + drawings)
    if any(owner.get(i, caption_box) != caption_box and _overlaps(f, inner)
           for i, f in enumerate(floats)):
        return HOLDS_FLOAT
    if any(_overlaps(a, inner)
           for a in _algorithms(blocks, _horizontals(drawn), body, caption_box)):
        return HOLDS_FLOAT
    return None


def _in_image_grid(box, images, lines) -> bool:
    """Is the line `box` print of a grid of `images`: set between two of its
    rows, under one image and over another that each share some of its
    x-span, within the x-extent of those that do; or, with no other line of
    `lines` sharing some of its x-span -3..4 pt over or under it (a
    paragraph's), within `_PANEL_GAP` over or under one image of a row of two
    or more, within its x-span?"""
    beside = [i for i in images if i[0] < box[2] and i[2] > box[0]]
    if (any(i[3] <= box[1] + 1 for i in beside)
            and any(i[1] >= box[3] - 1 for i in beside)
            and box[0] >= min(i[0] for i in beside) - _COLUMN_TOL
            and box[2] <= max(i[2] for i in beside) + _COLUMN_TOL):
        return True
    if any(o != box and o[0] < box[2] and o[2] > box[0]
           and (-3 <= box[1] - o[3] <= 4 or -3 <= o[1] - box[3] <= 4) for o in lines):
        return False
    return any(i[0] - _COLUMN_TOL <= box[0] and box[2] <= i[2] + _COLUMN_TOL
               and (-1 <= i[1] - box[3] <= _PANEL_GAP
                    or -1 <= box[1] - i[3] <= _PANEL_GAP)
               and any(j[1] < i[3] and j[3] > i[1]
                       and (j[2] <= i[0] or j[0] >= i[2]) for j in images)
               for i in images)


def _content_line(box, rules, floats, verticals, frames) -> bool:
    """Is the line `box` a float's content: between `rules` above and below it
    that each span `_SAME_EXTENT` of its width, in a cell of one of `floats`
    (`_in_cell`), or inside one of `frames`?"""
    def spans(r):
        return min(r[2], box[2]) - max(r[0], box[0]) >= _SAME_EXTENT * (box[2] - box[0])

    return ((any(r[3] <= box[1] + 1 and spans(r) for r in rules)
             and any(r[1] >= box[3] - 1 and spans(r) for r in rules))
            or _in_cell(box, floats, verticals)
            or any(_contains(f, box) for f in frames))


def render_media(pdf_path: str, out_dir: str, kinds=("figure", "table"),
                 only: set = None, unrendered: list = None) -> dict:
    """Render every figure/table found in the PDF.

    Returns {(kind, label): {"path", "page", "caption", "box", "roman"}}, the
    label as `caption_kind` reads it and "roman" its numeral as printed
    (`caption_numeral`). A label is rendered from the FIRST page whose
    caption line introduces it, so a body reference ("as Figure 3 shows") never
    wins over the real caption.

    `only` restricts rendering to those figure labels — used to fill the specific
    figures the HTML could not supply, without re-rendering the ones it already
    did.

    Every caption found with no crop (`float_crop`'s problem) is printed to
    stderr and appended to `unrendered` as {"kind", "label", "page",
    "problem"}, unless the label is rendered from another page.
    """
    import fitz

    os.makedirs(out_dir, exist_ok=True)
    wanted = None if only is None else {ef._label_key(n) for n in only}
    doc = fitz.open(pdf_path)
    try:
        heads, feet = learn_furniture(doc)
        body = body_text(doc)
        found, missed = {}, {}
        for pno in range(doc.page_count):
            page = doc[pno]
            band = furniture_band(page, heads, feet)
            labels = []
            for b in page.get_text("dict")["blocks"]:
                seen = caption_kind(_block_text(b)) if b.get("type") == 0 else None
                if seen is not None and seen not in labels:
                    labels.append(seen)
            for kind, num in labels:
                if wanted is not None and (kind != "figure"
                                           or ef._label_key(num) not in wanted):
                    continue
                if base_kind(kind) not in kinds or (kind, num) in found:
                    continue
                rect, problem, caption = float_crop(page, kind, num, band, body)
                if rect is None:
                    if problem is None:
                        raise RuntimeError(f"{kind} {num} on page {pno + 1}: "
                                           "caption read but not found")
                    missed.setdefault((kind, num), {
                        "kind": kind, "label": num, "page": pno + 1,
                        "problem": problem})
                    continue
                out = os.path.join(out_dir, f"{kind}{num}.png")
                zoom = _DPI / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                      clip=rect, alpha=False)
                pix.save(out)
                cap = re.sub(r"\s+", " ", caption).strip()
                found[(kind, num)] = {"path": out, "page": pno + 1,
                                      "caption": cap[:1900],
                                      "box": [round(v, 1) for v in rect],
                                      "roman": caption_numeral(caption)}
        for key, item in missed.items():
            if key in found:
                continue
            print(f"WARN {item['kind']} {item['label']} on page {item['page']}: "
                  f"{item['problem']}; not rendered", file=sys.stderr)
            if unrendered is not None:
                unrendered.append(item)
        return found
    finally:
        doc.close()


def fetch_pdf(source: str) -> str:
    """Return a local path for `source` (a path, a URL, or an arxiv id)."""
    if os.path.exists(source):
        return source
    url = source
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", source):
        url = f"https://arxiv.org/pdf/{source}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 4096 or not data[:5].startswith(b"%PDF"):
        raise ValueError(f"not a PDF: {url}")
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def inject(page_id: str, source: str, apply: bool = False, force: bool = False,
           kinds=("figure", "table"), keep_text: bool = False,
           only: set = None) -> dict:
    """Render from the PDF and insert each image after its first mention."""
    import time

    import verify_sections as vs
    from notion_upload import upload_image
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)

    def _img_caption(b):
        return _image_caption(b).strip().lower()

    def _img_kind(b):
        # Extended Data is counted apart: a page holding the main figures says
        # nothing about whether its Extended Data figures are there.
        seen = image_label(_img_caption(b))
        return seen[0] if seen else "figure"

    # Count/replace images ANYWHERE on the page, not just at top level — see
    # _all_image_blocks for why nested ones exist and what missing them costs.
    page_images = _all_image_blocks(blocks)
    full_kinds = [k for base in kinds for k in (base, ED_PREFIX + base)]
    have = {k: 0 for k in full_kinds}
    for b in page_images:
        have[_img_kind(b)] = have.get(_img_kind(b), 0) + 1
    # `only` means "fill these specific numbers". Whether the page has OTHER
    # figures says nothing about whether figure 4 is present, so the blanket
    # "already has figures, skip" guard must not apply — it made the HTML-gap
    # fallback a no-op on exactly the pages that needed it. Drop numbers that are
    # already there instead.
    if only is not None:
        present = set()
        for b in page_images:
            label = _image_figure_label(_image_caption(b))
            if label is not None:
                present.add(ef._label_key(label))
        only = {n for n in only if ef._label_key(n) not in present}
        if not only:
            rep = {"page": page_id, "existing": have, "kinds": [], "found": 0,
                   "placed": 0, "replaced": 0, "text_archived": 0,
                   "skipped_existing": True}
            return rep
        todo = tuple(k for k in kinds if k == "figure")
    else:
        todo = tuple(k for k in full_kinds if force or not have[k])
    rep = {"page": page_id, "existing": have, "kinds": list(todo),
           "found": 0, "placed": 0, "replaced": 0, "text_archived": 0}
    # On a page with no text yet every image anchors nowhere and lands at the top,
    # where nothing moves it once the text arrives below. The HTML figure and table
    # injectors already wait for a body; this one placed every PDF figure above
    # the first heading of a page being processed.
    if not ef._has_body_to_anchor(blocks):
        rep["deferred"] = "no body text to anchor against"
        return rep
    if not todo:
        rep["skipped_existing"] = True
        return rep

    pdf = fetch_pdf(source)
    out_dir = tempfile.mkdtemp(prefix="pdfmedia_")
    unrendered = []
    media = render_media(pdf, out_dir,
                         kinds=tuple({base_kind(k) for k in todo}), only=only,
                         unrendered=unrendered)
    media = {key: item for key, item in media.items() if key[0] in todo}
    rep["found"] = len(media)
    if unrendered:
        rep["unrendered"] = unrendered
    if not media:
        return rep

    # --force means REPLACE, not "add a second copy". A page built by the old
    # whole-page-screenshot path already has an image per figure, so injecting
    # without clearing first doubles every figure and makes the page worse than
    # it was. Old images go only after the new ones rendered successfully.
    stale = [b for b in page_images if _img_kind(b) in todo]
    if force and stale:
        rep["replaced"] = len(stale)
        if apply:
            for b in stale:
                notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                time.sleep(0.2)
            blocks = vs.fetch_blocks(page_id)

    resolved = placements(list(media), blocks,
                          {key: item["roman"] for key, item in media.items()})
    groups, order = {}, []
    for (kind, num) in sorted(media, key=_float_order):
        item = media[(kind, num)]
        anchor = resolved.get((kind, num)) or "__end__"
        if anchor not in groups:
            groups[anchor] = []
            order.append(anchor)
        groups[anchor].append((kind, num, item))

    if not apply:
        rep["planned"] = []
    for key in order:
        children = []
        for kind, num, item in groups[key]:
            if not apply:
                children.append({"_kind": kind, "_num": num, "_after": key})
                rep["planned"].append({"kind": kind, "label": num, "after": key})
                continue
            fid = upload_image(item["path"])
            if not fid:
                continue
            children.append(ef._image_block(fid, item["caption"]))
            time.sleep(0.2)
        if not children:
            continue
        rep["placed"] += len(children)
        if apply:
            body = {"children": children}
            end_of_body = reference_section.body_end_anchor(blocks)
            if key != "__end__":
                body["after"] = key
            elif end_of_body:
                body["after"] = end_of_body
            notion("PATCH", f"/blocks/{page_id}/children", body)
            time.sleep(0.34)

    if not keep_text:
        if "table" in todo:
            rep["text_archived"] = _archive_flattened_tables(page_id, blocks, apply)
        # Charts leave flattened label runs too, and they sit right above the image
        # that replaces them. Verified against the figure's own PDF box, so only
        # text the figure actually contains is removed.
        rep["chart_text_archived"] = _archive_flattened_figure_text(
            page_id, blocks, _media_text(pdf, media), apply)
    return rep


def _all_image_blocks(top_blocks: list, max_depth: int = 2) -> list:
    r"""Every image block on the page, INCLUDING ones nested under another block.

    Why this can't just scan top level: the old hand-rolled injector PATCHed
    `/blocks/{paragraph-id}/children`, so its figures are CHILDREN of the caption
    paragraph rather than page-level blocks — the same wrong-parent mistake this
    repo already has scar tissue for with Q&A callouts. A top-level-only scan then
    fails twice over:

      * the page looks like it has ZERO figures, so a normal run happily injects a
        second copy of every figure right next to the stale one, and
      * `--force` "replaces" only what it can see.

    Observed on a real page: 15 whole-page screenshots (title + abstract + figure,
    right edge clipped) survived a `--force` run that reported `replaced: 1`, so the
    page still rendered the broken images the fix was supposed to remove.

    Depth is bounded (a figure may sit under a paragraph, or a column inside a
    column_list) and child fetches are best-effort: a page we can't fully walk must
    degrade to "found fewer images", never crash the healer.
    """
    from translate_fulltext import notion

    out = []

    def children_of(bid):
        got, cur = [], None
        while True:
            path = f"/blocks/{bid}/children?page_size=100"
            if cur:
                path += f"&start_cursor={cur}"
            d = notion("GET", path)
            got += d.get("results", [])
            if not d.get("has_more"):
                return got
            cur = d["next_cursor"]

    def walk(blocks, depth):
        for b in blocks:
            if b.get("type") == "image":
                out.append(b)
            if b.get("has_children") and depth < max_depth:
                try:
                    walk(children_of(b["id"]), depth + 1)
                except Exception:
                    continue
    walk(top_blocks, 0)
    return out


def _media_text(pdf_path: str, media: dict) -> str:
    r"""All text lying INSIDE the rendered figure/table boxes, normalized.

    Used to spot body paragraphs that are nothing but a chart's flattened labels.
    Matching against the source region (rather than guessing from shape) is what
    makes the removal safe: translated prose is Korean and cannot match this
    English text, so only the untranslated label runs can ever be archived.
    """
    import fitz

    doc = fitz.open(pdf_path)
    try:
        parts = []
        for item in media.values():
            pno = item.get("page", 0) - 1
            box = item.get("box")
            if not box or not (0 <= pno < doc.page_count):
                continue
            parts.append(doc[pno].get_text(clip=fitz.Rect(*box)))
    finally:
        doc.close()
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _archive_flattened_figure_text(page_id: str, blocks: list, media_text: str,
                                   apply: bool) -> int:
    r"""Drop body paragraphs that are just a figure's flattened chart labels.

    A chart translated out of a PDF lands as a run of bare label/number text
    ("DeepSWE GPT-5.6 Sol 73.0 Fable 5 70.0 ..."), which then sits directly above
    the figure image that replaces it — the page shows the same data twice, once
    unreadable. `_is_pure_table` does not catch these: it demands >=12 decimals and
    a chart label run has ~half that, so figure-derived text was nobody's job.

    Rather than loosen that threshold globally (which risks eating real prose on
    every page), each candidate must be VERIFIABLY part of a rendered figure: its
    tokens have to appear in the text inside that figure's own box in the PDF. A
    paragraph that isn't reproduced there is left alone, whatever it looks like.
    """
    from translate_fulltext import notion

    def _text(b):
        return "".join(x.get("plain_text", "")
                       for x in (b.get(b["type"]) or {}).get("rich_text", []))

    doomed = []
    for b in blocks:
        if b["type"] != "paragraph":
            continue
        t = _text(b).strip()
        toks = t.split()
        if len(toks) < 8:
            continue
        if sum(1 for c in t if "가" <= c <= "힣") / max(1, len(t)) >= 0.15:
            continue                                  # translated prose — keep
        if len(re.findall(r"\d", t)) < 5:
            continue                                  # not a data run
        # Bibliography citations mark BODY text — a chart label never cites. Without
        # this an in-text benchmark enumeration ("Agentic: BrowseComp [1], ...")
        # scores ~0.95 against the figures, because those same product names are
        # printed inside the charts, and real content gets archived.
        if re.search(r"\[\d+(?:\s*,\s*\d+)*\]", t):
            continue
        if "`" in t:
            continue    # literal template/code text (a figure panel spelled out) — readable, keep
        norm = [w for w in re.sub(r"[^\w.%+-]+", " ", t.lower()).split() if w]
        if not norm:
            continue
        hit = sum(1 for w in norm if w in media_text)
        if hit / len(norm) >= 0.9:                    # reproduced inside a figure
            doomed.append(b["id"])
    if apply:
        import time
        for bid in doomed:
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            time.sleep(0.2)
    return len(doomed)


def _archive_flattened_tables(page_id: str, blocks: list, apply: bool) -> int:
    """Drop the table text a real table image now replaces.

    Only unambiguous whole-block cases: a `code` block that IS a markdown table
    (what build_answer_blocks emits for one), and a paragraph `_is_pure_table`
    already vouches for. A block mixing table data with the next real sentence is
    left alone — same policy as extract_paper_tables, so no prose is ever lost.
    """
    import verify_sections as vs
    from extract_paper_tables import _is_pure_table
    from translate_fulltext import notion

    def _text(b):
        return "".join(x.get("plain_text", "")
                       for x in (b.get(b["type"]) or {}).get("rich_text", []))

    doomed = []
    for b in blocks:
        if b["type"] == "code" and vs._MD_TABLE_RE.search(_text(b)):
            doomed.append(b["id"])
        elif b["type"] == "paragraph" and _is_pure_table(_text(b)):
            doomed.append(b["id"])
    if apply:
        import time
        for bid in doomed:
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            time.sleep(0.2)
    return len(doomed)


def _image_caption(block: dict) -> str:
    """The plain text of an image block's caption."""
    return "".join(s.get("plain_text", "") for s in
                   ((block.get("image") or {}).get("caption") or []))


def _image_figure_label(caption: str):
    """The figure label an image caption opens with (`image_label`), or None."""
    seen = image_label(caption)
    return seen[1] if seen and seen[0] == "figure" else None


def _placed_figures(blocks: list) -> dict:
    """{figure label: index of its image block} for figures already on the page."""
    found = {}
    for i, b in enumerate(blocks):
        if b.get("type") != "image":
            continue
        label = _image_figure_label(_image_caption(b))
        if label is not None:
            found[label] = i
    return found


def _neighbour_spot(label, placed: dict):
    """`ef.neighbour_spot` among the placed figures in `label`'s own series.

    The series is the label's letter (`ef._series_key`); numbered labels are one
    series of their own.
    """
    series, index = ef._series_key(label)
    same = {ef._series_key(n)[1]: at for n, at in placed.items()
            if isinstance(n, int) == isinstance(label, int)
            and ef._series_key(n)[0] == series}
    return ef.neighbour_spot(index, same)


def _float_order(key):
    """Sort key for (kind, label): by kind, then series and number."""
    return key[0], ef._series_key(key[1]), str(key[1])


def placements(keys: list, blocks: list, roman: dict) -> dict:
    """{(kind, label): id of the block to insert after, or None for the page end}.

    `roman` is {(kind, label): the numeral its caption prints, or ""}. The first
    mention (`_anchor_for`); else the anchor of a neighbour in the same
    batch and series (`ef.fill_anchor_gaps`); else, for a figure, beside the
    figures of its series already on the page (`_neighbour_spot`).
    """
    resolved = {key: _anchor_for(key[0], key[1], blocks, roman[key]) for key in keys}
    for kind_ in {k for k, _n in keys}:
        nums = [n for k, n in keys if k == kind_]
        filled = ef.fill_anchor_gaps(nums, {n: resolved[(kind_, n)] for n in nums})
        for n in nums:
            resolved[(kind_, n)] = filled.get(n)
    placed = _placed_figures(blocks)
    for (kind_, n), anchor in list(resolved.items()):
        if anchor or kind_ != "figure":
            continue
        spot = _neighbour_spot(n, placed)
        if spot is None:
            continue
        at, side = spot
        at = at if side == "after" else max(at - 1, 0)
        resolved[(kind_, n)] = blocks[at]["id"]
    return resolved


def _cited_label(num):
    """The label a body mention of the float `num` cites: what precedes the
    letter of a suffixed label (`10a` -> "10", `S2c` -> "S2"), else `num`."""
    found = _SUFFIXED.fullmatch(num) if isinstance(num, str) else None
    return num if found is None else found.group("cited")


def _anchor_for(kind: str, num, blocks: list, roman: str = ""):
    """Block to insert after: the first body mention of this figure/table (or of
    `roman`, the numeral its caption prints), a mention of a suffixed label's
    number included (`_cited_label`), else, for a lettered label, the heading
    of the appendix its letter names."""
    if kind.startswith(ED_PREFIX):
        return _extended_data_anchor(base_kind(kind), num, blocks)
    # The same rule the HTML injectors read: a particle after the number, a
    # lowercase "fig. 5", an Extended Data mention that names another figure, and
    # another float's caption cross-referencing this one are all handled there.
    found = ef.first_mention(kind, _cited_label(num), blocks, roman)
    if found or isinstance(num, int):
        return found
    letter = re.match(r"([A-Za-z])\.?\d", num)
    return ef.appendix_anchor(letter.group(1), blocks) if letter else None


# "Extended Data Fig. 3", "Extended Data Figs. 7–9", "Extended Data Table 1".
_ED_MENTION = re.compile(
    r"Extended\s+Data\s+(Figs?\.?|Figures?|그림|Tables?|표)\s*"
    r"(\d+)(?:\s*[–—-]\s*(\d+))?", re.I)


def _extended_data_anchor(kind: str, num: int, blocks: list):
    """The first body block that mentions this Extended Data figure or table."""
    import extract_paper_figures as ef

    for b in blocks:
        if b["type"] not in ef.TEXT_TYPES:
            continue
        for found in _ED_MENTION.finditer(ef._block_text(b)):
            word = found.group(1).lower()
            is_table = word.startswith(("tab", "표"))
            if is_table != (kind == "table"):
                continue
            low = int(found.group(2))
            high = int(found.group(3) or low)
            if low <= num <= high:
                return b["id"]
    return None


# Where the WhatsApp channel saves a document the reader sends.
ATTACHMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           os.pardir, "attachments")

# Shorter than this, a title is a phrase that any paper might print.
_MIN_TITLE_KEY = 24


def _title_key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def attachment_for(title: str, directory: str = ATTACHMENTS):
    """The reader's own PDF of this paper, found by the title on its first pages.

    Nothing ties an attachment to the page it was processed into — it is saved
    under the id of the message that carried it — so a paper sent WITH its PDF
    still had no figure source, and came out with none.
    """
    key = _title_key(title)
    if len(key) < _MIN_TITLE_KEY or not os.path.isdir(directory):
        return None
    import fitz

    names = sorted(n for n in os.listdir(directory) if n.lower().endswith(".pdf"))
    for name in names:
        path = os.path.join(directory, name)
        try:
            with fitz.open(path) as doc:
                text = "".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
        except Exception:
            continue                        # not a readable PDF
        if key in _title_key(text):
            return path
    return None


def journal_pdf_url(paper_url: str):
    """The publisher's PDF for a paper outside arXiv, where we know the form."""
    import nature_source

    article = nature_source.article_url(paper_url)
    return f"{article}.pdf" if article else None


def pdf_source_for(title: str, paper_url: str, directory: str = ATTACHMENTS):
    """The PDF to render a non-arXiv paper from: the reader's own, else the
    publisher's. The reader's comes first because it is the version they read."""
    return attachment_for(title, directory) or journal_pdf_url(paper_url)


def heal_pdf_media(page_id: str, apply: bool = False) -> dict:
    """Healer entry: only acts on a paper with no usable LaTeXML HTML.

    A paper outside arXiv is rendered from the reader's own PDF or, failing
    that, the publisher's (see `pdf_source_for`). A paper that HAS arxiv/ar5iv HTML is left to `heal_figures` / `heal_tables`,
    which render the real HTML and are strictly better. This covers the gap those
    two silently no-op on.
    """
    import extract_paper_figures as ef

    aid = ef.ensure_arxiv_id(page_id, apply=apply)
    if not aid:
        from translate_fulltext import notion

        page = notion("GET", f"/pages/{page_id}")
        paper_url = next((p.get("url") for p in (page.get("properties") or {}).values()
                          if p.get("type") == "url" and p.get("url")), "")
        source = pdf_source_for(ef._page_title(page), paper_url)
        if not source:
            return {"page": page_id, "arxiv": None, "placed": 0}
        rep = inject(page_id, source, apply=apply)
        rep["source"] = "attachment" if os.path.exists(source) else source
        return rep
    html_text, _ = ef.fetch_html(aid)
    if html_text:
        return {"page": page_id, "arxiv": aid, "html": True, "placed": 0}
    return inject(page_id, aid, apply=apply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="Notion page id (omit with --out for a dry render)")
    ap.add_argument("--arxiv", help="arxiv id — fetches https://arxiv.org/pdf/<id>")
    ap.add_argument("--pdf", help="local path or URL of the paper PDF")
    ap.add_argument("--out", help="render to this directory and exit (no Notion)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="REPLACE existing figure/table images (archive, then inject)")
    ap.add_argument("--keep-text", action="store_true",
                    help="keep flattened table text that a table image replaces")
    ap.add_argument("--figures-only", action="store_true")
    ap.add_argument("--tables-only", action="store_true")
    a = ap.parse_args()

    kinds = ("figure", "table")
    if a.figures_only:
        kinds = ("figure",)
    elif a.tables_only:
        kinds = ("table",)

    source = a.pdf or a.arxiv
    if a.out:
        if not source:
            ap.error("--out needs --pdf or --arxiv")
        media = render_media(fetch_pdf(source), a.out, kinds=kinds)
        print(json.dumps({f"{k[0]}{k[1]}": v for k, v in media.items()},
                         ensure_ascii=False, indent=1))
        return 0
    if not a.page:
        ap.error("--page is required (or use --out for a dry render)")
    if not source:
        import extract_paper_figures as ef
        source = ef.arxiv_id_from_page(a.page)
        if not source:
            print("ERROR no --pdf/--arxiv and the page has no arxiv Paper URL",
                  file=sys.stderr)
            return 2
    rep = inject(a.page, source, apply=not a.dry_run, force=a.force, kinds=kinds,
                 keep_text=a.keep_text)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
