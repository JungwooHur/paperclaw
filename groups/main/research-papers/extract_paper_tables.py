#!/usr/bin/env python3
r"""Render an arxiv paper's tables to images and inject them into its Notion page.

Why this exists
---------------
Translating a paper from its arxiv HTML *fulltext* flattens every `<table>` into a
run of prose ("VLAs $\pi_{0.5}$3.3B 96.9 84.6 ($\downarrow$ 12.3) …") — unreadable.
Unlike figures, arxiv tables are HTML `<figure class="ltx_table" id="SnTm">`
elements, not images, so they must be *rendered*: this loads the real arxiv page
in headless Chromium (playwright) and screenshots each table element, preserving
the paper's exact layout, column rules and color highlights + caption.

Placement mirrors figures, through the same mention rule
(`extract_paper_figures.mentions`): the image goes right after the paragraph that
first cites the table's PRINTED label (`표 N` / `Table N` / `Table A.1`, a particle
glued on or not, alone or in a list), fallback the heading of the section the
table sits in (the `S3` of `S3.T5`, never the heading numbered like the table),
fallback end of body.
Uploaded PRIVATELY via notion_upload.

Safe removal (default)
----------------------
The flattened table text is entangled with prose (one block can hold a table's
data tail AND the next real paragraph). So only PURE-table blocks are archived —
high numeric density, low Korean-prose ratio, no leaked heading, no prose sentence
in the tail. Mixed blocks are left untouched (no prose is ever lost); a little
numeric residue may remain. Idempotent (skips if the page already has table
images, detected by an image caption starting "Table").

  extract_paper_tables.py --page <id> --arxiv <id> [--dry-run] [--force] [--keep-text]

Tables already placed are never re-placed, so an anchor fix does not reach them.
`--relocate` moves them by hand (a plan unless `--apply`): see `plan_relocation`.

  extract_paper_tables.py --page <id> --relocate [--arxiv <id>] [--apply]
"""
import argparse
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reference_section  # noqa: E402
import extract_paper_figures as ef   # fetch_html, _clean, _block_text, _image_block

_TNUM = re.compile(r"T(\d+)")
_TABLE_REF = None  # built per-number


_TID = re.compile(r'\bid="((?:S|A)\d+\.T(\d+))"')


_ROMAN = ef.ROMAN_VALUES


_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _cap_key(cap: str) -> str:
    """Normalised caption text — the label-agnostic identity of a placed float.

    Zero-width characters are dropped: the current render puts U+200B into a
    caption's maths, and no caption stored on a page carries one.
    """
    text = _ZERO_WIDTH.sub("", cap or "")
    return re.sub(r"\s+", " ", text).strip().lower()[:80]


def caption_number(cap: str):
    """The table number a caption states, arabic or ROMAN.

    IEEE papers label tables `TABLE I:`. Reading only `Table 1` meant a placed table
    was never recognised as placed, so every healer cycle injected another copy —
    one page ended up with TABLE I/II/III four times over — and the body reference
    ("TABLE II") never matched either, so each copy landed at the page end instead
    of beside the text that cites it.
    """
    m = re.match(r"\s*table\s*([0-9]+|[ivxIVX]+)\s*[:.]?", cap or "", re.I)
    if not m:
        return None
    tok = m.group(1)
    return int(tok) if tok.isdigit() else _ROMAN.get(tok.lower())


def is_table_key(key: str) -> bool:
    """A caption key this injector could have written: every one opens "table"."""
    return key.startswith("table")


def table_repeats(blocks: list) -> int:
    """How many times the most repeated TABLE caption appears among the images.

    Tables only. A composite figure's panels each carry the figure's caption, so a
    repeated figure caption is normal: measured over the DB, 13 pages repeated one,
    and every repeat was a different picture. Counting those made the runaway guard
    refuse such a page for good, tables still unplaced included.
    """
    hist = {}
    for b in blocks:
        if b.get("type") != "image" or b.get("archived") or b.get("in_trash"):
            continue
        key = _cap_key(_image_caption(b))
        if key and is_table_key(key):
            hist[key] = hist.get(key, 0) + 1
    return max(hist.values(), default=0)


def placed_caption(num, caption: str) -> str:
    """The caption the injector writes on a table image: the source's, labelled.

    ALWAYS "Table N…" — the per-number idempotency keys off it, and some papers'
    <figcaption> parses empty (which would otherwise make the image un-tracked and
    re-injected as a duplicate next run).
    """
    if caption.strip().lower().startswith("table"):
        return caption
    return f"Table {num}" + (f": {caption}" if caption.strip() else "")


def _image_caption(block: dict) -> str:
    return "".join(c.get("plain_text", "") for c in
                   ((block.get("image") or {}).get("caption") or []))


def replaced_by(blocks: list, caption_keys: set) -> list:
    """Ids of the images a forced run's new copies stand in for.

    Matched on the CAPTION only, never on the table number: the number comes from
    the LaTeXML id counter, which restarts in an appendix (`S2.T1` and `A1.T1` are
    both 1) and under per-section numbering, so a number match would archive a
    different table the run never re-placed. `blocks` must be the snapshot taken
    BEFORE the new images went in, or they would match themselves. An uncaptioned
    image is a person's and is never matched.
    """
    out = []
    for b in blocks:
        if b.get("type") != "image" or b.get("archived") or b.get("in_trash"):
            continue
        key = _cap_key(_image_caption(b))
        if key and key in caption_keys:
            out.append(b["id"])
    return out


def _source_key(table: dict) -> str:
    return _cap_key(placed_caption(table["num"], table["caption"]))


def _legacy_keys(tables: list) -> dict:
    """{caption key: table} for the captions the pre-fix parser wrote.

    Only a key that names ONE table and is no table's caption today: the old
    parser gave a table without a caption of its own the NEXT table's, and that
    key is the next table's now — matching it would take that table's image.
    The tables `parse_tables` splits out of one id count as one: the old parser
    read one caption for the id, and its image is the id's.
    """
    current = {_source_key(t) for t in tables}
    found = {}
    for t in tables:
        if "legacy_caption" in t:
            key = _cap_key(placed_caption(t["num"], t["legacy_caption"]))
            found.setdefault(key, []).append(t)
    return {key: ts[0] for key, ts in found.items()
            if len({t.get("container") or t["id"] for t in ts}) == 1
            and key not in current}


def forced_keys(placed: list, tables: list) -> set:
    """Caption keys of the images a forced run's new copies of `placed` stand in for.

    The caption each copy carries, and the one the pre-fix parser wrote for the
    same table (`_legacy_keys`). A page placed before the caption fix shows a
    long table as a bare `Table 5`; matching only the new caption kept that
    image beside its replacement, and no tool could clear the pair, since
    neither caption repeats.

    Args:
        placed: The source tables this run placed.
        tables: Every table of the source (`parse_tables`).
    """
    ids = {t["id"] for t in placed}
    return ({_source_key(t) for t in placed}
            | {key for key, t in _legacy_keys(tables).items() if t["id"] in ids})


_element_span = ef.element_span


def _element_body(html_text: str, at: int) -> str:
    """The markup inside the element whose start tag holds position `at`."""
    span = _element_span(html_text, at)
    return html_text[span[0]:span[1]] if span else ""


_NESTED_FIGURE = re.compile(r"<figure\b", re.I)
_OPENS_AS_TABLE = re.compile(r"\s*(?:table|표)\s*\S", re.I)


def _without_nested_figures(body: str) -> str:
    """`body` with every figure nested in it cut out, panels included."""
    out, i = [], 0
    while True:
        m = _NESTED_FIGURE.search(body, i)
        span = _element_span(body, m.start()) if m else None
        if not span:
            return "".join(out) + body[i:]
        out.append(body[i:m.start()])
        i = span[2]


def _table_caption(body: str, num: int) -> str:
    """The caption of the table whose element markup is `body`.

    Its own `<figcaption>` is the one not inside a nested figure: a composite
    table nests a figure per panel, each captioned "(a) …", and the table's own
    caption — the one carrying its number — sits beside them. Without one, the
    only table caption nested in it is its own: a rotated table puts its id on
    a wrapper around the figure that holds it (`Table SI-2`, which no counter
    matches).
    """
    own = [ef._clean(c) for c in ef._CAP.findall(_without_nested_figures(body))]
    for cap in own:
        if _OPENS_AS_TABLE.match(cap):
            return cap
    if own:
        return own[0]
    nested = [ef._clean(c) for c in ef._CAP.findall(body)]
    labelled = [cap for cap in nested if _OPENS_AS_TABLE.match(cap)]
    if len(labelled) == 1:
        return labelled[0]
    for cap in nested:
        if caption_number(cap) == num:
            return cap
    return ""


# The window the injector used to read a caption in, before it read the table's
# own element. Kept only to recognise the captions it wrote that way.
_LEGACY_WINDOW = 8000


def _holds_several_tables(body: str) -> bool:
    """Does a table element hold several captioned tables (`Table 7`, `Table 8`)?

    LaTeXML can give two tables of a flex row one id. The old renderer pictured
    the largest, so an image made from such an id may show either table.
    """
    labels = {printed_label(ef._clean(c)) for c in ef._CAP.findall(body)
              if _OPENS_AS_TABLE.match(ef._clean(c))}
    return len(labels - {None}) > 1


_CAPTION_OR_TABLE = re.compile(
    r"<figcaption\b[^>]*>(?P<cap>.*?)</figcaption>|<(?P<close>/?)table\b(?P<attrs>[^>]*)>",
    re.DOTALL | re.I)


def _id_of(attrs: str):
    found = re.search(r'\bid="([^"]+)"', attrs or "")
    return found.group(1) if found else None


def _tables_inside(body: str) -> list:
    """[(element id, caption)] for each captioned table a table element holds,
    or [] when the markup does not say which caption is whose.

    LaTeXML gives two tables of a flex row one id. Each is pictured by its own
    element: a panel figure holding one table caption and a table, else — the
    same number of captions and top-level tables, each table with an id — the
    k-th table under the k-th caption, which is how the panels, the caption
    rows over table rows, and the caption-under-table layouts all read.

    Args:
        body: The markup inside the table element (`_element_body`).
    """
    captions = [ef._clean(c) for c in ef._CAP.findall(body)
                if _OPENS_AS_TABLE.match(ef._clean(c))]
    panels = []
    for opening in _NESTED_FIGURE.finditer(body):
        span = _element_span(body, opening.start())
        inner = body[span[0]:span[1]] if span else ""
        own = [ef._clean(c) for c in ef._CAP.findall(_without_nested_figures(inner))
               if _OPENS_AS_TABLE.match(ef._clean(c))]
        if len(own) == 1 and re.search(r"<table\b", inner, re.I):
            tag = body[opening.start():body.find(">", opening.start()) + 1]
            panels.append((_id_of(tag), own[0]))
    if len(panels) == len(captions) > 1 and all(pid for pid, _ in panels):
        return panels
    order, depth = [], 0
    for found in _CAPTION_OR_TABLE.finditer(body):
        if found.group("cap") is not None:
            cap = ef._clean(found.group("cap"))
            if _OPENS_AS_TABLE.match(cap):
                order.append(("caption", cap))
        elif found.group("close"):
            depth -= 1
        else:
            if depth == 0:
                order.append(("table", _id_of(found.group("attrs"))))
            depth += 1
    caps = [x for kind, x in order if kind == "caption"]
    ids = [x for kind, x in order if kind == "table"]
    if len(caps) == len(ids) > 1 and all(ids):
        return list(zip(ids, caps))
    return []


def _legacy_caption(html_text: str, at: int, fid: str) -> str:
    """What the pre-fix parser read as the caption of the table whose id is at `at`.

    The first `<figcaption>` within `_LEGACY_WINDOW` characters: empty for a long
    table, a panel's "(a) …" for a composite one, the NEXT table's for one with
    no caption of its own. Every page placed before the fix carries this text.
    """
    found = re.search(re.escape(fid) + r'".*?<figcaption[^>]*>(.*?)</figcaption>',
                      html_text[at:at + _LEGACY_WINDOW], re.DOTALL | re.I)
    return ef._clean(found.group(1))[:1900] if found else ""


def parse_tables(html_text: str) -> list:
    """Ordered list of {id, num, caption, legacy_caption[, section]} for each
    table id `SnTm` / `AnTm`; `section` is the top-level section the source
    prints it in (`ef.enclosing_section`), when there is one.
    Scans ids directly (not <figure> boundaries) so nested table-figures — which
    truncate a non-greedy `<figure>…</figure>` match — are not dropped. The id is
    what render_tables screenshots, so this is the robust key.

    The caption is read from the table's OWN element. It used to be the first
    `<figcaption>` within 8,000 characters of the id, so a long table (a page of
    prompt examples) had none — it was written as a bare `Table 5` — and a table
    with no caption of its own borrowed the next table's. `legacy_caption` is
    that old reading (`_legacy_caption`), the only thing that names the table an
    image placed before the fix shows.

    An id holding several captioned tables (`several_tables`) is one entry per
    table when `_tables_inside` can tell them apart: `id` is the element that
    pictures it, `container` the id it shares. Otherwise it stays one entry,
    which `render_tables` pictures whole, every caption in it."""
    out, seen = [], set()
    sections = ef.source_sections(html_text)
    for m in _TID.finditer(html_text):
        fid = m.group(1)
        if fid in seen:
            continue
        seen.add(fid)
        num = int(m.group(2))
        body = _element_body(html_text, m.start())
        table = {"id": fid, "num": num, "caption": _table_caption(body, num)[:1900],
                 "legacy_caption": _legacy_caption(html_text, m.start(), fid)}
        section = ef.enclosing_section(sections, m.start())
        if section:
            table["section"] = section
        if _holds_several_tables(body):
            table["several_tables"] = True
            parts = _tables_inside(body)
            if parts:
                out.extend(dict(table, id=pid, caption=cap[:1900], container=fid)
                           for pid, cap in parts)
                seen.update(pid for pid, _ in parts)
                continue
        out.append(table)
    return out


_PRINTED_LABEL = re.compile(
    r"\s*table\s*([A-Z]\.?[0-9]+(?:\.[0-9]+)*|[0-9]+(?:\.[0-9]+)*"
    r"|[ivx]+(?![A-Za-z0-9])|[A-Z](?![A-Za-z0-9]))", re.I)


def printed_label(caption: str):
    """The label a caption prints: 5, 2 for `TABLE II`, "A.1", "2.1", "A"; or None.

    Any other token is returned as written, so the mention rule compares it as
    a whole label and a letter-only one (`Table A`) matches nothing.
    """
    found = _PRINTED_LABEL.match(caption or "")
    if not found:
        return None
    token = found.group(1)
    if token.isdigit():
        return int(token)
    return _ROMAN.get(token.lower(), token)


def _printed_key(table: dict) -> str:
    """The label the injector's caption for `table` prints, as `_label_key`."""
    return ef._label_key(printed_label(placed_caption(table["num"], table["caption"])))


def _mention_label(table: dict, tables: list):
    """The label the body cites table `table` by, or None when it has none of its own.

    The PRINTED label, never the id's counter: an appendix may restart it, so
    `S2.T1` and `A1.T1` are both 1 while the second prints `Table A.1`, and
    searching for "Table 1" put the appendix table beside the body's Table 1. The
    counter stands in only for a caption that prints no label, and only when no
    other table shares it. A label two tables print names neither.
    """
    label = printed_label(table["caption"])
    if label is None:
        shared = sum(1 for t in tables if t["num"] == table["num"]) > 1
        return None if shared else table["num"]
    printed = [printed_label(t["caption"]) for t in tables]
    shared = sum(1 for other in printed if other is not None
                 and ef._label_key(other) == ef._label_key(label)) > 1
    return None if shared else label


def _table_mention(label, blocks: list):
    """The first block citing table `label` (roman too for a number), or None."""
    if label is None:
        return None
    roman = ""
    if isinstance(label, int):
        roman = {v: k for k, v in _ROMAN.items()}.get(label, "")
    return ef.first_mention("table", label, blocks, roman)


def _table_anchor(label, blocks: list, fid: str = ""):
    """Where the table printed as `label` goes: the first block citing it, else
    the heading of the section its id `fid` puts it in."""
    return _table_mention(label, blocks) or ef.section_anchor(fid, blocks)


def table_anchors(tables: list, blocks: list, sections=None) -> dict:
    """{table id: (anchor block id or None, whether a citation chose it)}.

    A citation is evidence of where the table belongs; the section heading is a
    guess, and the relocation must not trade a placement for it (see
    `plan_relocation`). `sections` is `ef.source_sections` of the source, which
    lets the section fallback check the page's numbering against it.
    """
    out = {}
    for t in tables:
        hit = _table_mention(_mention_label(t, tables), blocks)
        out[t["id"]] = (hit, True) if hit else (
            ef.section_anchor(t["id"], blocks, t.get("section"), sections), False)
    return out


def _is_pure_table(text: str) -> bool:
    """A block safe to archive: dense numeric table data with no real prose to lose.
    Rejects mixed blocks (data tail + a following paragraph) and leaked headings."""
    if len(re.findall(r"\d+\.\d+", text)) < 12:
        return False
    kr = sum(1 for c in text if "가" <= c <= "힣")
    if kr / max(1, len(text)) >= 0.18:            # too much Korean -> prose entangled
        return False
    if text.lstrip().startswith("#"):             # leaked heading -> keep it
        return False
    if re.search(r"(?:다|된다|한다|이다|없다|있다|시킨다)\.\s*$", text[-60:]):
        return False                              # ends on a Korean prose sentence
    return True


# Resolve the element that actually holds a table's content. A table id can sit
# on a caption-only <figure> whose <table>/panel is a SIBLING (LaTeXML flex
# layout), so climb to the nearest ancestor that holds table content but no
# heading. Then screenshot the inner <table> / .ltx_figure_panel element DIRECTLY
# — it sizes to its content, so it is never clipped by a narrow minipage wrapper
# and never over-expanded by a width reset (the two clipping bugs).
_CONTENT_JS = """e => {
  const big = x => { for (const n of x.querySelectorAll('table,.ltx_figure_panel,.ltx_tabular')) { const c=n.getBoundingClientRect(); if (c.height>=40 && c.width>=40) return true; } return false; };
  let n = e, s = 0;
  while (n && s < 5) { if (big(n) && !n.querySelector('h1,h2,h3,h4,.ltx_title')) return n; n = n.parentElement; s++; }
  return e.parentElement || e;
}"""
# Stable per-container group id so tables sharing a flex container are captured
# once (not once per table id).
_GROUP_JS = """e => { if (!e.hasAttribute('data-wmid')) e.setAttribute('data-wmid','g'+(window.__wmc=(window.__wmc||0)+1)); return e.getAttribute('data-wmid'); }"""

# Un-clip ONLY this container's subtree (a global overflow reset disrupts the whole
# page layout and makes neighbouring figures overlap the table). Enough to reveal a
# table overflowing a fixed-width minipage.
_RESET_JS = """e => { for (const n of [e, ...e.querySelectorAll('*')]) { if (n.style) n.style.overflow='visible'; } }"""

# Clip rect in PAGE coordinates. HORIZONTAL extent comes from the real table/panel
# boxes only — a centered figcaption can be wider than the table and would pull
# adjacent-column text into the clip. The caption contributes VERTICAL extent only
# (so it shows above the table) at the table's own width. Headings are excluded.
_UNION_JS = """e => {
  const tb = e.querySelectorAll('table,.ltx_figure_panel,.ltx_tabular');
  let l=1e9,t=1e9,r=-1e9,b=-1e9;
  for (const n of tb){ const c=n.getBoundingClientRect();
    if (c.width<10||c.height<10) continue;
    l=Math.min(l,c.left); t=Math.min(t,c.top); r=Math.max(r,c.right); b=Math.max(b,c.bottom); }
  if (r<0) return null;
  for (const n of e.querySelectorAll('figcaption')){ const c=n.getBoundingClientRect();
    if (c.height<5) continue; t=Math.min(t,c.top); b=Math.max(b,c.bottom); }
  return {x:l+window.scrollX, y:t+window.scrollY, w:r-l, h:b-t};
}"""


def render_tables(arxiv_id: str, tables: list, outdir: str,
                  source_url: str = "") -> list:
    """Return [{id, num, caption, path}], one crisp image per table id.

    Per id, not per number: the number is the id's counter, which an appendix
    restarts, and keying on it gave `S3.T5` and `A1.T5` one image between them
    — the first one's picture under the last one's caption.

    Pass 1 (preferred): element.screenshot() the substantial <table>/panel element
    — it paints ONLY that element, so no adjacent-column text or over-wide caption
    bleeds in, and tiny helper <table>s (fragmentation) are filtered by size.
    Pass 2 (fallback): for any table left without an image — e.g. a transform-
    scaled panel whose box collapses — clip-screenshot the union of the nearest
    ancestor that actually holds table content.

    An id holding several captioned tables used to be pictured by its largest
    one, under the id's caption and beside its citation, whichever table that
    was. Each table `parse_tables` split out of one is pictured by its own
    element; one it could not split goes straight to pass 2, which pictures the
    whole element, every caption in it, so the caption it is given is in it."""
    from playwright.sync_api import sync_playwright
    out = {}
    idx = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 2400, "height": 3200},
                              device_scale_factor=2)
        # Render the SAME document the tables were parsed from. This used to be
        # hardcoded to arxiv-native HTML while the parser falls back to ar5iv, so a
        # paper whose native HTML 404s (common for older submissions) loaded a 404
        # page here: every id lookup missed and the run reported found: N, rendered:
        # 0 with no error at all, and the page kept its flattened table text.
        url = source_url or f"https://arxiv.org/html/{arxiv_id}"
        try:
            pg.goto(url, wait_until="networkidle", timeout=60000)
        except Exception:
            pg.goto(url, timeout=60000)
        pg.wait_for_timeout(150)
        # One table of a container (`parse_tables`): its own element, never an
        # ancestor, which holds the container's other table too.
        for t in tables:
            el = pg.query_selector(f'[id="{t["id"]}"]') if "container" in t else None
            if not el:
                continue
            is_table = el.evaluate("e => e.tagName === 'TABLE'")
            (el.evaluate_handle("e => e.parentElement || e").as_element()
             if is_table else el).evaluate(_RESET_JS)
            pg.wait_for_timeout(50)
            boxes = [(tg.bounding_box(), tg)
                     for tg in ([el] if is_table else el.query_selector_all("table"))]
            boxes = [(b["width"] * b["height"], tg) for b, tg in boxes
                     if b and b["width"] >= 10 and b["height"] >= 10]
            if not boxes:
                continue
            path = os.path.join(outdir, f"table_{idx}.png")
            idx += 1
            try:
                max(boxes, key=lambda x: x[0])[1].screenshot(path=path)
            except Exception:
                continue
            if os.path.exists(path) and os.path.getsize(path) >= 300:
                out[t["id"]] = path
        group_el, group_ids = {}, {}
        for t in sorted(tables, key=lambda x: x["num"]):
            if t.get("several_tables"):
                continue    # a container's part above; one not split, whole below
            idel = pg.query_selector(f'[id="{t["id"]}"]')
            if not idel:
                continue
            c = idel.evaluate_handle(_CONTENT_JS).as_element()
            wmid = c.evaluate(_GROUP_JS)
            group_el.setdefault(wmid, c)
            group_ids.setdefault(wmid, []).append(t["id"])
        # pass 1: crisp element screenshots
        for wmid, c in group_el.items():
            ids = group_ids[wmid]       # in number order, as the loop above added them
            c.evaluate(_RESET_JS)
            pg.wait_for_timeout(50)
            raw = (c.query_selector_all("table")
                   or c.query_selector_all(".ltx_figure_panel") or [])
            subs = []
            for tg in raw:
                box = tg.bounding_box()
                if box and box["width"] >= 120 and box["height"] >= 50:
                    subs.append((box["width"] * box["height"], tg))
            subs.sort(key=lambda x: -x[0])
            for i, (_, tg) in enumerate(subs):
                path = os.path.join(outdir, f"table_{idx}.png")
                idx += 1
                try:
                    tg.screenshot(path=path)
                except Exception:
                    continue
                if not os.path.exists(path) or os.path.getsize(path) < 300:
                    continue
                out.setdefault(ids[i] if i < len(ids) else ids[-1], path)
        # pass 2: page-clip fallback for any table still missing
        for t in tables:
            if t["id"] in out:
                continue
            node = pg.query_selector(f'[id="{t["id"]}"]')
            u = None
            for _ in range(5):
                if not node:
                    break
                node.evaluate(_RESET_JS)
                pg.wait_for_timeout(40)
                u = node.evaluate(_UNION_JS)
                if u and u["w"] >= 40 and u["h"] >= 40:
                    break
                node = node.evaluate_handle("e => e.parentElement").as_element()
            if not u or u["w"] < 40 or u["h"] < 40:
                continue
            pad = 12
            clip = {"x": max(0, u["x"] - pad), "y": max(0, u["y"] - pad),
                    "width": u["w"] + 2 * pad, "height": u["h"] + 2 * pad}
            path = os.path.join(outdir, f"table_{idx}.png")
            idx += 1
            try:
                pg.screenshot(path=path, clip=clip, full_page=True)
            except Exception:
                continue
            if os.path.exists(path) and os.path.getsize(path) >= 300:
                out[t["id"]] = path
        browser.close()
    return [{"id": t["id"], "num": t["num"], "caption": t["caption"], "path": out[t["id"]]}
            for t in tables if t["id"] in out]



# A page with no body cannot be placed into. Anchoring means "after the paragraph
# that first mentions this number", so on a page whose text has not been uploaded
# yet EVERY anchor misses and every float falls to the page-end fallback — which,
# on a page that is still empty, is the TOP. One paper shipped with all eleven of
# its figures stacked above the first heading, in a pile, because the agent
# injected them in the same minute it created the page and appended the
# translation underneath afterwards. Nothing could repair it later either: the
# next healer cycle sees images present and skips.
#
# Refusing is safe and self-healing — the healer retries every five minutes, and
# the moment the text lands the anchors work.
MIN_BODY_CHARS_TO_ANCHOR = 1000


def _has_body_to_anchor(blocks) -> bool:
    # The injected reference list is NOT body: it is English apparatus appended at
    # the tail, it anchors nothing, and counting it made a never-translated page
    # carrying 77 reference entries read as 43k characters of body.
    chars = 0
    for b in reference_section.body_blocks(blocks):
        if b.get("type") == "image":
            continue
        payload = b.get(b.get("type")) or {}
        if isinstance(payload, dict):
            chars += sum(len(s.get("plain_text", "")) for s in payload.get("rich_text", []))
    return chars >= MIN_BODY_CHARS_TO_ANCHOR


def inject_tables(page_id: str, arxiv_id: str, apply: bool = False,
                  force: bool = False, keep_text: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion
    from notion_upload import upload_image

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "found": 0, "placed": 0, "archived": 0,
           "skipped_existing": False}
    if not _has_body_to_anchor(blocks):
        rep["deferred"] = "no body text to anchor against"
        return rep

    # Per-NUMBER idempotency: an agent (or an earlier run) may have imaged SOME
    # tables and left the rest as text. Skip only the numbers already imaged (by
    # their "Table N" caption), inject the missing ones — not all-or-nothing.
    # Identity is the CAPTION TEXT first, the parsed number second. We copy the
    # source caption onto the image verbatim, so the text is an exact key that
    # survives any labelling scheme — while the number does not: this paper's
    # appendix tables are labelled `Table A:`/`Table D:`, `caption_number` returns
    # None for a letter, and the injector therefore could not see its OWN output.
    # It re-injected those 7 tables on every 5-minute cycle for days: 750 copies of
    # each, 5,278 images on one page. A number that fails to parse must never read
    # as "not placed yet".
    #
    # The number compared is the PRINTED label, never the id's counter: an
    # appendix restarts the counter, so `A1.T5` (printed `Table A.5`) read as
    # placed the moment the body's `Table 5` was.
    existing, existing_caps = set(), set()
    for b in blocks:
        if b["type"] == "image":
            cap = _image_caption(b)
            key = _cap_key(cap)
            if key:
                existing_caps.add(key)
            label = printed_label(cap)
            if label is not None:
                existing.add(ef._label_key(label))
    # Backstop for the next variant of the same fault: one image per table caption
    # is the only correct state, so a repeated one means the idempotency key has
    # failed again. Refuse rather than add to the pile.
    dup = table_repeats(blocks)
    if dup > 1:
        rep["error"] = f"runaway: a table caption already appears {dup}x on this page"
        return rep

    html_text, html_src = ef.fetch_html(arxiv_id)
    if not html_text:
        rep["error"] = "no HTML source"
        return rep
    all_tables = parse_tables(html_text)
    tables = [t for t in all_tables
              if force or (_printed_key(t) not in existing
                           and _cap_key(t["caption"]) not in existing_caps)]
    rep["found"] = len(all_tables)
    if not tables:
        rep["skipped_existing"] = True
        return rep
    if not apply:
        rep["would_place"] = [t["num"] for t in tables]
        if force:
            rep["would_replace"] = len(replaced_by(blocks, forced_keys(tables, all_tables)))
        rep["would_archive"] = sum(1 for b in blocks
                                   if b["type"] in ef.TEXT_TYPES
                                   and _is_pure_table(ef._block_text(b)))
        return rep

    outdir = tempfile.mkdtemp(prefix="paper_tables_")
    images = render_tables(arxiv_id, tables, outdir, html_src)  # [{id, num, caption, path}]
    rep["rendered"] = len(images)

    # place images grouped by anchor (first citation of the printed label), in
    # document order, each with its own table's caption.
    by_id = {t["id"]: t for t in tables}
    anchors = table_anchors(all_tables, blocks, ef.source_sections(html_text))
    groups, order = {}, []
    for im in images:
        own = by_id.get(im["id"])
        anchor = (anchors[own["id"]][0] if own else None) or "__end__"
        groups.setdefault(anchor, []).append(im)
        if anchor not in order:
            order.append(anchor)
    placed = []
    for key in order:
        children, these = [], []
        for im in groups[key]:
            fid = upload_image(im["path"])
            if fid:
                cap = placed_caption(im["num"], im["caption"])
                children.append(ef._image_block(fid, cap))
                if im["id"] in by_id:
                    these.append(by_id[im["id"]])
                time.sleep(0.2)
        if not children:
            continue
        body = {"children": children}
        end_of_body = reference_section.body_end_anchor(blocks)
        if key != "__end__":
            body["after"] = key
        elif end_of_body:
            body["after"] = end_of_body
        notion("PATCH", f"/blocks/{page_id}/children", body)
        rep["placed"] += len(children)
        placed.extend(these)
        time.sleep(0.34)

    # --force REPLACES: archive the copies the new images stand in for, once the
    # new ones are in. It used to add beside them, so a forced run left two of
    # every table.
    if force:
        for bid in replaced_by(blocks, forced_keys(placed, all_tables)):
            try:
                notion("PATCH", f"/blocks/{bid}", {"archived": True})
                rep["replaced"] = rep.get("replaced", 0) + 1
            except Exception as e:
                rep.setdefault("archive_failed", []).append(f"{bid[-8:]}: {e}"[:120])
            time.sleep(0.34)

    # safe removal: archive only pure-table blocks (never mixed/prose)
    if not keep_text:
        # `blocks` is the snapshot taken before the images were inserted, so a block
        # another pass already archived is still listed here. Re-archiving it is a
        # hard 400 ("Can't edit block that is archived") that aborted the whole run
        # AFTER the tables had been placed — leaving the page half-done and the
        # command looking like a total failure. Skip what is already gone, and never
        # let a single stubborn block undo the work that succeeded.
        seen = set()
        for b in blocks:
            if b["id"] in seen or b.get("archived"):
                continue
            seen.add(b["id"])
            if b["type"] in ef.TEXT_TYPES and _is_pure_table(ef._block_text(b)):
                try:
                    notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                    rep["archived"] += 1
                except Exception as e:
                    rep.setdefault("archive_failed", []).append(f"{b['id'][-8:]}: {e}"[:120])
                time.sleep(0.34)
    return rep


def heal_tables(page_id: str, apply: bool = False) -> dict:
    aid = ef.ensure_arxiv_id(page_id, apply=apply)
    if not aid:
        return {"page": page_id, "arxiv": None, "placed": 0}
    return inject_tables(page_id, aid, apply=apply)


# --- relocating the tables already placed -------------------------------------
# The injector places a table once and then skips its number for good, so fixing
# the anchor rule never reaches a page placed before the fix. `--relocate` moves
# those images, by hand: the healer only sees recent pages, and this is a one-off
# sweep (see groups/main/CLAUDE.md, "Repairs that are not on the healer").


def _which_table(caption: str, tables: list, legacy: dict):
    """The source table an image's caption names, or None when that is unclear.

    By the caption only, which the injector copies verbatim: the source's
    caption today, else the one the pre-fix parser wrote (`legacy`, from
    `_legacy_keys`), which is how a bare `Table 5` is recognised. Never by the
    number alone. A source revised since the page was placed, or one that
    numbers per chapter (`Table 2.1` reads as 2), gives the same number to a
    different table, and the relocation then moved or recaptioned the wrong one.
    """
    key = _cap_key(caption)
    by_key = [t for t in tables if _source_key(t) == key]
    if by_key:
        return by_key[0] if len(by_key) == 1 else None
    return legacy.get(key)


def _cut_short(caption: str, want: str) -> bool:
    """Is `caption` the beginning of `want` and no more?"""
    def flat(text):
        return re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", text or "")).strip()
    has, full = flat(caption).rstrip(" .…"), flat(want).rstrip(" .…")
    return len(has) < len(full) and full.startswith(has)


def _image_url(block: dict) -> str:
    image = block.get("image") or {}
    return ((image.get("file") or {}).get("url")
            or (image.get("external") or {}).get("url") or "")


def _where_a_fallback_put_it(body: list, i: int) -> bool:
    """Does image `i` sit where a fallback, not a citation, would have put it?

    That is the run of images right under a heading (the old rule's heading
    numbered like the table) or the run that ends the body (its last resort).
    """
    start = end = i
    while start > 0 and body[start - 1].get("type") == "image":
        start -= 1
    while end + 1 < len(body) and body[end + 1].get("type") == "image":
        end += 1
    return (start == 0 or body[start - 1].get("type", "").startswith("heading")
            or end == len(body) - 1)


def plan_relocation(blocks: list, tables: list, me, sections=None) -> dict:
    """Which table images to move where, and which captions to correct. Pure.

    A table image is in place when it sits in the run of images right after its
    anchor (`_table_anchor`), after every table of that anchor that comes before
    it in the source. Images of figures may sit in that run too: the figure
    injector anchors on the same paragraphs, and whichever ran second inserted
    its images first. Everything else that this integration wrote and no person
    has edited since (`collect_papers._ours`, the rule `--clear-unprocessed`
    uses) is moved there. A caption that is not the table's own — a bare
    `Table 5`, what the old parser read (`legacy_caption`), one cut short — is
    replaced by the SOURCE caption, on a move or where the image already is; the
    plan keeps the caption it replaces, so that can be undone. One the caption
    key already identifies is kept, since the source's text can be noisier than
    the page's (`75%75\\%`, a formula and its TeX).

    An image is matched to its table by caption alone (`_which_table`). A table
    no text cites has only its section heading as an anchor, and that is a
    guess: such an image is moved only off a spot a fallback put it in
    (`_where_a_fallback_put_it`). One that follows a paragraph may follow the
    sentence citing it in words the mention rule does not read.

    Left alone: anything after the injected reference list
    (`reference_section.body_blocks`), anchors there included; an image a person
    wrote or edited; one the source does not have; one whose table the rule
    gives no anchor, since moving it to the end of the body would trade one
    guess for another; and one whose picture the caption cannot vouch for. The
    old renderer made one image per id COUNTER — the first table's picture under
    the last one's caption — so where two tables share the counter (an appendix
    restarts it) the image may show the other table; and an id holding several
    captioned tables (`several_tables`) was pictured by its largest one. A page
    whose table captions repeat is refused outright — that is
    `dedup_tables.py`'s pile, and moving it would only spread it.

    Args:
        blocks: The page's top-level blocks.
        tables: `parse_tables` of the source, in document order.
        me: This integration's user id; None means nothing counts as ours.
        sections: `ef.source_sections` of the source (see `table_anchors`).

    Returns:
        `{"moves": [{"after": id, "items": [...]}], "recaption": [{"block",
        "was", "caption"}], "left": [...]}`, or `{"refused": why}`. Each move
        is one insert: its items go in source order, and an item names the old
        block, the table, the caption to write and the image's file URL.
        Applying the plan and planning again plans nothing.
    """
    import collect_papers

    if table_repeats(blocks) > 1:
        return {"refused": "a table caption repeats on this page; "
                           "clear the copies with dedup_tables.py first"}
    body = reference_section.body_blocks(blocks)
    where = {b["id"]: i for i, b in enumerate(body)}
    rank = {t["id"]: i for i, t in enumerate(tables)}
    anchors = table_anchors(tables, body, sections)
    anchor = {tid: found for tid, (found, _) in anchors.items()}
    legacy = _legacy_keys(tables)
    counters = {}
    for t in tables:
        counters[t["num"]] = counters.get(t["num"], 0) + 1
    plan = {"moves": [], "recaption": [], "left": []}

    shown, claims = [], {}
    for b in body:
        if b.get("type") != "image" or b.get("archived") or b.get("in_trash"):
            continue
        caption = _image_caption(b)
        if not is_table_key(_cap_key(caption)):
            continue
        t = _which_table(caption, tables, legacy)
        why = ("not in the source" if t is None
               else "its id holds several tables; the picture may show another"
               if t.get("several_tables")
               else "another table shares its id counter; the picture may show it"
               if counters[t["num"]] > 1 else None)
        if why:
            plan["left"].append({"block": b["id"], "why": why})
            continue
        shown.append((b, t))
        claims[t["id"]] = claims.get(t["id"], 0) + 1
    table_of = {b["id"]: t for b, t in shown if claims[t["id"]] == 1}

    in_place, staying = set(), {}
    for a in {a for a in anchor.values() if a}:
        last, j = -1, where[a] + 1
        while j < len(body) and body[j].get("type") == "image":
            t = table_of.get(body[j]["id"])
            if t and anchor[t["id"]] == a and rank[t["id"]] > last:
                last = rank[t["id"]]
                in_place.add(body[j]["id"])
                staying.setdefault(a, []).append((last, body[j]["id"]))
            j += 1

    batches = {}
    for b, t in shown:
        want = placed_caption(t["num"], t["caption"])
        has = _image_caption(b)
        if _cap_key(has) == _source_key(t) and not _cut_short(has, want):
            want = has
        ours = collect_papers._ours(b, me)
        if b["id"] not in table_of:
            why = "several images show this table"
        elif b["id"] in in_place:
            if ours and has != want[:2000]:
                plan["recaption"].append({"block": b["id"], "was": has,
                                          "caption": want})
            continue
        elif not anchor[t["id"]]:
            why = "no anchor on the page"
        elif (not anchors[t["id"]][1]
              and not _where_a_fallback_put_it(body, where[b["id"]])):
            why = "no text cites it, and it follows text that may"
        elif (not anchors[t["id"]][1]
              and ef.in_section(body, where[anchor[t["id"]]], where[b["id"]])):
            why = "no text cites it, and it is already in its own section"
        elif not ours:
            why = "written or edited by a person"
        elif not _image_url(b):
            why = "no file to copy"
        else:
            a = anchor[t["id"]]
            before = [bid for r, bid in staying.get(a, []) if r < rank[t["id"]]]
            batches.setdefault(before[-1] if before else a, []).append(
                (rank[t["id"]], {"old": b["id"], "table": t["id"], "num": t["num"],
                                 "caption": want, "url": _image_url(b)}))
            continue
        plan["left"].append({"block": b["id"], "why": why})

    for after in sorted(batches, key=lambda bid: where[bid]):
        plan["moves"].append({"after": after,
                              "items": [it for _, it in sorted(batches[after],
                                                               key=lambda x: x[0])]})
    return plan


def relocation_refusal(page_id: str, page: dict, skip: set):
    """Why `--relocate` must leave this page alone, or None.

    `skip` is the owner's `heal_skip.txt` (dashless ids): a page they arrange by
    hand. A page that is not a paper has no tables of a paper to place.
    """
    import page_kind

    if page_id.replace("-", "") in skip:
        return "listed in heal_skip.txt"
    if not page_kind.is_paper(page):
        return f"not a paper: {page_kind.venue(page)}"
    return None


def relocate_tables(page_id: str, arxiv_id: str = None, apply: bool = False) -> dict:
    """Move a page's misplaced table images to their anchors (`plan_relocation`).

    Dry run unless `apply`. Each move downloads the image from its own block,
    uploads it privately, inserts it after the anchor, and archives the old block
    only once that insert succeeded, so a failure leaves the old one where it was.
    """
    import time
    import collect_papers
    import verify_sections as vs
    from heal_paper_pages import _skipped_pages
    from translate_fulltext import notion

    rep = {"page": page_id, "moved": 0, "recaptioned": 0}
    why = relocation_refusal(page_id, notion("GET", f"/pages/{page_id}"),
                             _skipped_pages())
    arxiv_id = arxiv_id or (None if why else ef.arxiv_id_from_page(page_id))
    if not why and not arxiv_id:
        why = "no arxiv id in the page's Paper URL"
    html_text = None if why else ef.fetch_html(arxiv_id)[0]
    if not why and not html_text:
        why = "no HTML source"
    me = None if why else collect_papers._me_id()
    if not why and not me:
        why = "cannot read this integration's user id (GET /users/me)"
    if why:
        rep["refused"] = why
        return rep
    plan = plan_relocation(vs.fetch_blocks(page_id), parse_tables(html_text), me,
                           sections=ef.source_sections(html_text))
    if "refused" in plan:
        rep["refused"] = plan["refused"]
        return rep
    rep["would_move"] = [{"table": it["table"], "block": it["old"], "after": mv["after"]}
                         for mv in plan["moves"] for it in mv["items"]]
    rep["would_recaption"] = plan["recaption"]
    rep["left"] = plan["left"]
    if not apply:
        return rep

    fail = rep.setdefault("failed", [])
    archived = rep.setdefault("archived", [])
    for move in plan["moves"]:
        children, olds = [], []
        for it in move["items"]:
            fid = ef.upload_image_from_url(it["url"])
            if not fid:
                fail.append(f"{it['table']}: could not copy the image")
                continue
            children.append(ef._image_block(fid, it["caption"]))
            olds.append(it["old"])
            time.sleep(0.2)
        if not children:
            continue
        try:
            notion("PATCH", f"/blocks/{page_id}/children",
                   {"children": children, "after": move["after"]})
        except Exception as e:
            fail.append(f"insert after {move['after']}: {e}"[:200])
            continue
        time.sleep(0.34)
        for old in olds:
            try:
                notion("PATCH", f"/blocks/{old}", {"archived": True})
                archived.append(old)
                rep["moved"] += 1
            except Exception as e:
                fail.append(f"archive {old}: {e}"[:200])
            time.sleep(0.34)
    for fix in plan["recaption"]:
        try:
            notion("PATCH", f"/blocks/{fix['block']}",
                   {"image": {"caption": [{"type": "text",
                                           "text": {"content": fix["caption"][:2000]}}]}})
            rep["recaptioned"] += 1
        except Exception as e:
            fail.append(f"recaption {fix['block']}: {e}"[:200])
        time.sleep(0.34)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv", help="arxiv id (NNNN.NNNNN); required except with "
                                    "--relocate, which reads it from the Paper URL")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-text", action="store_true",
                    help="inject images but do not archive any flattened-table text")
    ap.add_argument("--relocate", action="store_true",
                    help="move the table images already on the page to where the "
                         "anchor rule puts them; prints the plan unless --apply")
    ap.add_argument("--apply", action="store_true",
                    help="with --relocate: make the changes")
    a = ap.parse_args()
    if a.relocate:
        rep = relocate_tables(a.page, a.arxiv, apply=a.apply)
    else:
        if not a.arxiv:
            ap.error("--arxiv is required unless --relocate is given")
        rep = inject_tables(a.page, a.arxiv, apply=not a.dry_run,
                            force=a.force, keep_text=a.keep_text)
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
