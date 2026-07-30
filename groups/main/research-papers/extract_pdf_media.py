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
the next element — i.e. at the first line of body text. Page furniture is never
reached because body text always sits between it and the figure. The crop box is
then the UNION of what was actually collected (plus the caption), so a
single-column figure keeps its full width and a two-column figure keeps its own
column, with no caption-shape guessing.

Tables use the identical machinery, growing DOWNWARD (LaTeX puts a table's
caption above its body) instead of upward.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# Crop geometry, in PDF points (1/72").
_CLAMP = 40.0      # never crop outside this margin of the page
_PAD = 6.0         # breathing room around the collected content
_CAP_GAP = 26.0    # max vertical gap when a caption spills into more blocks
_DPI = 250         # render resolution; ~3.5x, matches the old snippet

# A block counts as a barrier (body prose / heading) rather than figure content.
_BARRIER_MIN_LINES = 2
_BARRIER_FULL_FRAC = 0.85    # a "full-measure" line is this much of the block width
_BARRIER_FULL_SHARE = 0.6    # ...and this share of lines must be full-measure
_HEADING_SIZE_DELTA = 0.6    # a block this much larger than body text is a heading

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


def _caption_re(kind: str, num: int):
    """Matches a `Figure 7:` / `Table 3.` caption opener for one number."""
    word = "Figure" if kind == "figure" else "Table"
    return re.compile(rf"^\s*{word}\s+0*{num}\s*[:.]")


def _spans(block):
    for line in block.get("lines", ()):
        for span in line.get("spans", ()):
            yield span


def _block_text(block) -> str:
    return " ".join(s["text"] for s in _spans(block))


def _body_size(blocks) -> float:
    """The page's dominant font size — used to tell headings from body text."""
    hist = {}
    for b in blocks:
        if b.get("type") != 0:
            continue
        for s in _spans(b):
            key = round(s.get("size", 0), 1)
            hist[key] = hist.get(key, 0) + len(s.get("text", ""))
    return max(hist, key=hist.get) if hist else 10.0


def _is_barrier(block, body_size: float) -> bool:
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
    lines = block.get("lines") or []
    if not lines:
        return False
    sizes = [round(s.get("size", 0), 1) for s in _spans(block)]
    if sizes and max(sizes) >= body_size + _HEADING_SIZE_DELTA:
        return True
    if len(lines) < _BARRIER_MIN_LINES:
        return False
    x0, _, x1, _ = block["bbox"]
    measure = (x1 - x0) * _BARRIER_FULL_FRAC
    full = sum(1 for ln in lines if ln["bbox"][2] - ln["bbox"][0] >= measure)
    return full >= max(_BARRIER_MIN_LINES, _BARRIER_FULL_SHARE * len(lines))


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
    """Find the document's running head and foot, as normalised text.

    A paper's running head (its title, repeated at the top) and its page
    number are drawn from the page's own content stream, so they look exactly
    like figure content to a geometric walk — and they sit at the very top of the
    page, which is how the old snippet ended up cropping whole pages. They are
    identified the only way that is actually reliable: they REPEAT, near-verbatim,
    at the same edge of most pages.

    Returns (head_text, foot_text); either may be None.
    """
    n_pages = doc.page_count
    step = max(1, n_pages // _FURNITURE_SAMPLE)
    sampled = list(range(0, n_pages, step))[:_FURNITURE_SAMPLE]
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

    return _pick(tops, _HEAD_MAX_CHARS), _pick(bots, _FOOT_MAX_CHARS)


def _row_at(lines, index: int) -> tuple:
    """(y0, y1) of the whole typeset ROW containing lines[index].

    A running head is often several boxes side by side (title left, `TECHNICAL
    REPORT` right, a logo between). Treating only the single first line as the
    header leaves its neighbours behind as figure content, so the row is taken as
    a unit: every line that vertically overlaps the seed line.
    """
    y0, y1 = lines[index][0][1], lines[index][0][3]
    for (bbox, _) in lines:
        if bbox[1] < y1 and bbox[3] > y0:
            y0, y1 = min(y0, bbox[1]), max(y1, bbox[3])
    return y0, y1


def _rule_below(page, y: float, span: float = 12.0):
    """A wide hairline just under `y` — the rule under a running head."""
    best = None
    for d in page.get_drawings():
        r = d["rect"]
        if r.y1 - r.y0 > 2.5 or r.x1 - r.x0 < page.rect.width * 0.5:
            continue
        if y - 2 <= r.y0 <= y + span:
            best = max(best, r.y1) if best is not None else r.y1
    return best


def furniture_band(page, head_text, foot_text) -> tuple:
    """(header_bottom, footer_top) for this page — the body's usable y range.

    Only the page's own top/bottom row is considered, and only when it matches
    the document's running head/foot AND is separated from the body by a real
    gap, so a page whose top line is genuine content is never clipped.
    """
    lines = _page_lines(page)
    top, bottom = float("-inf"), float("inf")
    if len(lines) < 3:
        return top, bottom
    if head_text and _norm_furniture(lines[0][1]) == head_text:
        _, row_y1 = _row_at(lines, 0)
        below = [b[1] for (b, _) in lines if b[1] >= row_y1]
        if below and min(below) - row_y1 >= _FURNITURE_GAP:
            top = row_y1 + 2
            rule = _rule_below(page, row_y1)
            if rule is not None and rule < min(below):
                top = rule + 2
    if foot_text and _norm_furniture(lines[-1][1]) == foot_text:
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
    """A background rectangle covering the page — never figure content."""
    area = max(box[2] - box[0], 0) * max(box[3] - box[1], 0)
    return area >= _BACKGROUND_AREA_FRAC * page_rect.width * page_rect.height


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def find_caption(page, kind: str, num: int):
    """Caption box for `kind num` on this page, or None.

    A long caption is split by the PDF into several blocks; they are stitched
    back on while the text has not reached a sentence end and the next block is
    close by, in the same column, at the same font size.
    """
    pat = _caption_re(kind, num)
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
    blocks.sort(key=lambda b: b["bbox"][1])
    start = None
    for i, b in enumerate(blocks):
        if pat.match(_block_text(b)):
            start = i
            break
    if start is None:
        return None
    head = blocks[start]
    x0, y0, x1, y1 = head["bbox"]
    fonts = {round(s.get("size", 0), 1) for s in _spans(head)}
    text = _block_text(head)
    for nxt in blocks[start + 1:]:
        if text.rstrip().endswith("."):
            break
        nx0, ny0, nx1, ny1 = nxt["bbox"]
        if ny0 - y1 > _CAP_GAP or abs(nx0 - x0) > 40:
            break
        if {round(s.get("size", 0), 1) for s in _spans(nxt)} - fonts:
            break
        x0, x1, y1 = min(x0, nx0), max(x1, nx1), ny1
        text = _block_text(nxt)
    return (x0, y0, x1, y1)


def _page_parts(page, caption_box, body_size: float, band):
    """Split the page into (barriers, elements), excluding caption and furniture."""
    head_bottom, foot_top = band
    rect = page.rect

    def _furniture(box):
        return (box[3] <= head_bottom or box[1] >= foot_top
                or _is_stamp(box, rect))

    barriers, elements = [], []
    for b in page.get_text("dict")["blocks"]:
        box = tuple(b["bbox"])
        if _furniture(box):
            continue
        if b.get("type") != 0:
            elements.append(box)          # raster image
            continue
        if _overlaps(box, caption_box):
            continue                      # the caption is the anchor, not content
        (barriers if _is_barrier(b, body_size) else elements).append(box)
    for d in page.get_drawings():
        r = d["rect"]
        if r.y1 - r.y0 <= 0.2 and r.x1 - r.x0 <= 0.2:
            continue                      # degenerate stroke
        box = (r.x0, r.y0, r.x1, r.y1)
        if _furniture(box):
            continue
        elements.append(box)
    return barriers, elements


def _grow(caption_box, barriers, elements, upward: bool) -> float:
    """Find how far the figure/table body extends away from its caption.

    Repeatedly steps to the nearest element on the far side of the caption and
    stops when a barrier is nearer than that element — the first body-text line
    ends the region. Returns the far edge (a y coordinate).
    """
    edge = caption_box[1] if upward else caption_box[3]
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
        new_edge = min(e[1] for e in step) if upward else max(e[3] for e in step)
        if (new_edge >= edge - 0.5) if upward else (new_edge <= edge + 0.5):
            break                          # no progress; avoid spinning
        edge = new_edge
    return edge


def crop_box(page, kind: str, num: int, band=(float("-inf"), float("inf"))):
    """Crop rectangle for `kind num` on `page`, or None if it isn't there."""
    import fitz

    caption_box = find_caption(page, kind, num)
    if caption_box is None:
        return None
    blocks = page.get_text("dict")["blocks"]
    barriers, elements = _page_parts(page, caption_box, _body_size(blocks), band)
    upward = kind == "figure"
    edge = _grow(caption_box, barriers, elements, upward=upward)
    top = edge if upward else caption_box[1]
    bottom = caption_box[3] if upward else edge
    if bottom - top <= (caption_box[3] - caption_box[1]) + 2:
        return None                        # a caption with no body isn't a hit

    # Everything that lies in the band the walk delimited — NOT just the boxes it
    # stepped on. The walk moves by the nearest bottom edge, so a sibling that is
    # vertically enclosed by an already-crossed element (a plot's legend beside
    # its axes, a rotated axis title) is never stepped on, and taking only the
    # stepped-on boxes clipped those off the side of the figure.
    inside = [e for e in elements
              if e[3] > top + 0.5 and e[1] < bottom - 0.5
              and not _too_big(e, page.rect)]
    boxes = inside + [caption_box]
    rect = fitz.Rect(
        max(min(b[0] for b in boxes) - _PAD, _CLAMP),
        max(top - _PAD, _CLAMP),
        min(max(b[2] for b in boxes) + _PAD, page.rect.width - _CLAMP),
        min(bottom + _PAD, page.rect.height - _CLAMP))
    return rect if rect.width > 40 and rect.height > 20 else None


def render_media(pdf_path: str, out_dir: str, kinds=("figure", "table")) -> dict:
    """Render every figure/table found in the PDF.

    Returns {(kind, num): {"path", "page", "caption"}}. A number is rendered from
    the FIRST page whose caption line introduces it, so a body reference
    ("as Figure 3 shows") never wins over the real caption.
    """
    import fitz

    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        head_text, foot_text = learn_furniture(doc)
        found = {}
        for pno in range(doc.page_count):
            page = doc[pno]
            band = furniture_band(page, head_text, foot_text)
            blocks = [b for b in page.get_text("dict")["blocks"]
                      if b.get("type") == 0]
            for b in blocks:
                m = re.match(r"\s*(Figure|Table)\s+0*(\d+)\s*[:.]", _block_text(b))
                if not m:
                    continue
                kind = m.group(1).lower()
                num = int(m.group(2))
                if kind not in kinds or (kind, num) in found:
                    continue
                rect = crop_box(page, kind, num, band)
                if rect is None:
                    continue
                out = os.path.join(out_dir, f"{kind}{num}.png")
                zoom = _DPI / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                      clip=rect, alpha=False)
                pix.save(out)
                cap = re.sub(r"\s+", " ", _block_text(b)).strip()
                found[(kind, num)] = {"path": out, "page": pno + 1,
                                      "caption": cap[:1900],
                                      "box": [round(v, 1) for v in rect]}
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
           kinds=("figure", "table"), keep_text: bool = False) -> dict:
    """Render from the PDF and insert each image after its first mention."""
    import time

    import extract_paper_figures as ef
    import verify_sections as vs
    from notion_upload import upload_image
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)

    def _img_caption(b):
        return "".join(c.get("plain_text", "") for c in
                       ((b.get("image") or {}).get("caption") or [])).strip().lower()

    def _img_kind(b):
        return "table" if _img_caption(b).startswith("table") else "figure"

    # Count/replace images ANYWHERE on the page, not just at top level — see
    # _all_image_blocks for why nested ones exist and what missing them costs.
    page_images = _all_image_blocks(blocks)
    have = {"figure": 0, "table": 0}
    for b in page_images:
        have[_img_kind(b)] += 1
    todo = tuple(k for k in kinds if force or not have[k])
    rep = {"page": page_id, "existing": have, "kinds": list(todo),
           "found": 0, "placed": 0, "replaced": 0, "text_archived": 0}
    if not todo:
        rep["skipped_existing"] = True
        return rep

    pdf = fetch_pdf(source)
    out_dir = tempfile.mkdtemp(prefix="pdfmedia_")
    media = render_media(pdf, out_dir, kinds=todo)
    rep["found"] = len(media)
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

    groups, order = {}, []
    for (kind, num) in sorted(media, key=lambda k: (k[0], k[1])):
        item = media[(kind, num)]
        anchor = _anchor_for(kind, num, blocks) or "__end__"
        if anchor not in groups:
            groups[anchor] = []
            order.append(anchor)
        groups[anchor].append((kind, num, item))

    for key in order:
        children = []
        for kind, num, item in groups[key]:
            if not apply:
                children.append({"_kind": kind, "_num": num, "_after": key})
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
            if key != "__end__":
                body["after"] = key
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


def _anchor_for(kind: str, num: int, blocks: list):
    """Block to insert after: the first body mention of this figure/table."""
    import extract_paper_figures as ef

    words = (r"그림|Figure|Fig\.?") if kind == "figure" else (r"표|Table")
    ref = re.compile(rf"(?:{words})\s*0*{num}\b")
    for b in blocks:
        if b["type"] in ef.TEXT_TYPES and ref.search(ef._block_text(b)):
            return b["id"]
    return None


def heal_pdf_media(page_id: str, apply: bool = False) -> dict:
    """Healer entry: only acts on a paper with no usable LaTeXML HTML.

    A paper that HAS arxiv/ar5iv HTML is left to `heal_figures` / `heal_tables`,
    which render the real HTML and are strictly better. This covers the gap those
    two silently no-op on.
    """
    import extract_paper_figures as ef

    aid = ef.ensure_arxiv_id(page_id, apply=apply)
    if not aid:
        return {"page": page_id, "arxiv": None, "placed": 0}
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
