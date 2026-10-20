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
(`extract_paper_figures.mention_pattern`): the image goes right after the
paragraph that first mentions the table number (`표 N` / `Table N`, a particle
glued on or not), fallback the heading of the section the table sits in (the `S3`
of `S3.T5`, never the heading numbered like the table), fallback end of body.
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


def _cap_key(cap: str) -> str:
    """Normalised caption text — the label-agnostic identity of a placed float."""
    return re.sub(r"\s+", " ", (cap or "")).strip().lower()[:80]


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


def _element_span(html_text: str, at: int):
    """`(inner_start, inner_end, end)` of the element whose start tag holds `at`.

    Walks the nesting of that element's own tag, so a table figure that wraps its
    panels in figures of their own ends at ITS closing tag, not the first one.
    An element that is never closed runs to the end of the text.
    """
    start = html_text.rfind("<", 0, at + 1)
    tag = re.match(r"<([A-Za-z][\w-]*)", html_text[start:]) if start >= 0 else None
    open_end = html_text.find(">", at)
    if not tag or open_end < 0:
        return None
    opener = re.compile(rf"<{tag.group(1)}\b", re.I)
    closer = re.compile(rf"</{tag.group(1)}\s*>", re.I)
    depth, i = 1, open_end + 1
    while True:
        close = closer.search(html_text, i)
        if not close:
            return open_end + 1, len(html_text), len(html_text)
        nested = opener.search(html_text, i, close.start())
        if nested:
            depth, i = depth + 1, nested.end()
            continue
        depth -= 1
        if not depth:
            return open_end + 1, close.start(), close.end()
        i = close.end()


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
    caption — the one carrying its number — sits beside them.
    """
    own = [ef._clean(c) for c in ef._CAP.findall(_without_nested_figures(body))]
    for cap in own:
        if _OPENS_AS_TABLE.match(cap):
            return cap
    if own:
        return own[0]
    for cap in ef._CAP.findall(body):
        if caption_number(ef._clean(cap)) == num:
            return ef._clean(cap)
    return ""


def parse_tables(html_text: str) -> list:
    """Ordered list of {id, num, caption} for each table id `SnTm` / `AnTm`.
    Scans ids directly (not <figure> boundaries) so nested table-figures — which
    truncate a non-greedy `<figure>…</figure>` match — are not dropped. The id is
    what render_tables screenshots, so this is the robust key.

    The caption is read from the table's OWN element. It used to be the first
    `<figcaption>` within 8,000 characters of the id, so a long table (a page of
    prompt examples) had none — it was written as a bare `Table 5` — and a table
    with no caption of its own borrowed the next table's."""
    out, seen = [], set()
    for m in _TID.finditer(html_text):
        fid = m.group(1)
        if fid in seen:
            continue
        seen.add(fid)
        num = int(m.group(2))
        cap = _table_caption(_element_body(html_text, m.start()), num)
        out.append({"id": fid, "num": num, "caption": cap[:1900]})
    return out


def _table_anchor(num, blocks: list, fid: str = ""):
    """Where table `num` goes: the first block mentioning it (`표 N` / `Table N`,
    roman too), else the heading of the section its id `fid` puts it in."""
    if num is None:
        return None
    roman = {v: k for k, v in _ROMAN.items()}.get(num, "")
    return (ef.first_mention("table", num, blocks, roman)
            or ef.section_anchor(fid, blocks))


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
    """Return [{num, caption, path}], one crisp image per table number.

    Pass 1 (preferred): element.screenshot() the substantial <table>/panel element
    — it paints ONLY that element, so no adjacent-column text or over-wide caption
    bleeds in, and tiny helper <table>s (fragmentation) are filtered by size.
    Pass 2 (fallback): for any number left without an image — e.g. a transform-
    scaled panel whose box collapses — clip-screenshot the union of the nearest
    ancestor that actually holds table content."""
    from playwright.sync_api import sync_playwright
    cap = {t["num"]: t["caption"] for t in tables}
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
        group_el, group_nums = {}, {}
        for t in sorted(tables, key=lambda x: x["num"]):
            idel = pg.query_selector(f'[id="{t["id"]}"]')
            if not idel:
                continue
            c = idel.evaluate_handle(_CONTENT_JS).as_element()
            wmid = c.evaluate(_GROUP_JS)
            group_el.setdefault(wmid, c)
            group_nums.setdefault(wmid, []).append(t["num"])
        # pass 1: crisp element screenshots
        for wmid, c in group_el.items():
            nums = sorted(group_nums[wmid])
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
                out.setdefault(nums[i] if i < len(nums) else nums[-1], path)
        # pass 2: page-clip fallback for any number still missing
        for t in tables:
            if t["num"] in out:
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
                out[t["num"]] = path
        browser.close()
    return [{"num": n, "caption": cap.get(n, ""), "path": pth}
            for n, pth in sorted(out.items())]



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
    existing, existing_caps = set(), set()
    for b in blocks:
        if b["type"] == "image":
            cap = _image_caption(b)
            key = _cap_key(cap)
            if key:
                existing_caps.add(key)
            n = caption_number(cap)
            if n is not None:
                existing.add(n)
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
              if force or (t["num"] not in existing
                           and _cap_key(t["caption"]) not in existing_caps)]
    rep["found"] = len(all_tables)
    if not tables:
        rep["skipped_existing"] = True
        return rep
    if not apply:
        rep["would_place"] = [t["num"] for t in tables]
        if force:
            # render_tables keys captions by number, last one winning; mirror it.
            caps = {t["num"]: t["caption"] for t in tables}
            rep["would_replace"] = len(replaced_by(
                blocks, {_cap_key(placed_caption(n, c)) for n, c in caps.items()}))
        rep["would_archive"] = sum(1 for b in blocks
                                   if b["type"] in ef.TEXT_TYPES
                                   and _is_pure_table(ef._block_text(b)))
        return rep

    outdir = tempfile.mkdtemp(prefix="paper_tables_")
    images = render_tables(arxiv_id, tables, outdir, html_src)  # [{num, caption, path}]
    rep["rendered"] = len(images)

    # place images grouped by anchor (first `표 N` mention), in document order.
    # render_tables keys by number, last one winning; mirror it for the id.
    ids = {t["num"]: t["id"] for t in tables}
    groups, order = {}, []
    for im in images:
        anchor = _table_anchor(im["num"], blocks, ids.get(im["num"], "")) or "__end__"
        groups.setdefault(anchor, []).append(im)
        if anchor not in order:
            order.append(anchor)
    placed_caps = set()
    for key in order:
        children, these = [], []
        for im in groups[key]:
            fid = upload_image(im["path"])
            if fid:
                cap = placed_caption(im["num"], im["caption"])
                children.append(ef._image_block(fid, cap))
                these.append(_cap_key(cap))
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
        placed_caps.update(these)
        time.sleep(0.34)

    # --force REPLACES: archive the copies the new images stand in for, once the
    # new ones are in. It used to add beside them, so a forced run left two of
    # every table.
    if force:
        for bid in replaced_by(blocks, placed_caps):
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


def _source_key(table: dict) -> str:
    return _cap_key(placed_caption(table["num"], table["caption"]))


def _which_table(caption: str, tables: list):
    """The source table an image's caption names, or None when that is unclear.

    The caption first, since the injector copies it verbatim; the number second,
    for an image written with a bare `Table 5`. A number the source uses twice
    (an appendix restarts the id counter) names nothing.
    """
    key = _cap_key(caption)
    by_key = [t for t in tables if _source_key(t) == key]
    if len(by_key) == 1:
        return by_key[0]
    num = caption_number(caption)
    by_num = [t for t in (by_key or tables) if num is not None and t["num"] == num]
    return by_num[0] if len(by_num) == 1 else None


def _image_url(block: dict) -> str:
    image = block.get("image") or {}
    return ((image.get("file") or {}).get("url")
            or (image.get("external") or {}).get("url") or "")


def plan_relocation(blocks: list, tables: list, me) -> dict:
    """Which table images to move where, and which captions to correct. Pure.

    A table image is in place when it sits in the run of images right after its
    anchor (`_table_anchor`), after every table of that anchor that comes before
    it in the source. Images of figures may sit in that run too: the figure
    injector anchors on the same paragraphs, and whichever ran second inserted
    its images first. Everything else that this integration wrote and no person
    has edited since (`collect_papers._ours`, the rule `--clear-unprocessed`
    uses) is moved there; a move is a new image with the SOURCE caption, so a
    bare `Table 5` comes back with its caption. An image already in place whose
    caption differs from the source's is recaptioned where it is.

    Left alone: anything after the injected reference list
    (`reference_section.body_blocks`), anchors there included; an image a person
    wrote or edited; one the source does not have; one whose table the rule
    gives no anchor, since moving it to the end of the body would trade one
    guess for another. A page whose table captions repeat is refused outright —
    that is `dedup_tables.py`'s pile, and moving it would only spread it.

    Args:
        blocks: The page's top-level blocks.
        tables: `parse_tables` of the source, in document order.
        me: This integration's user id; None means nothing counts as ours.

    Returns:
        `{"moves": [{"after": id, "items": [...]}], "recaption": [...],
        "left": [...]}`, or `{"refused": why}`. Each move is one insert: its
        items go in source order, and an item names the old block, the table,
        the caption to write and the image's file URL. Applying the plan and
        planning again plans nothing.
    """
    import collect_papers

    if table_repeats(blocks) > 1:
        return {"refused": "a table caption repeats on this page; "
                           "clear the copies with dedup_tables.py first"}
    body = reference_section.body_blocks(blocks)
    where = {b["id"]: i for i, b in enumerate(body)}
    rank = {t["id"]: i for i, t in enumerate(tables)}
    anchor = {t["id"]: _table_anchor(t["num"], body, t["id"]) for t in tables}
    plan = {"moves": [], "recaption": [], "left": []}

    shown, claims = [], {}
    for b in body:
        if b.get("type") != "image" or b.get("archived") or b.get("in_trash"):
            continue
        caption = _image_caption(b)
        if not is_table_key(_cap_key(caption)):
            continue
        t = _which_table(caption, tables)
        if t is None:
            plan["left"].append({"block": b["id"], "why": "not in the source"})
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
        ours = collect_papers._ours(b, me)
        if b["id"] not in table_of:
            why = "several images show this table"
        elif b["id"] in in_place:
            if ours and _image_caption(b) != want[:2000]:
                plan["recaption"].append({"block": b["id"], "caption": want})
            continue
        elif not anchor[t["id"]]:
            why = "no anchor on the page"
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
    plan = plan_relocation(vs.fetch_blocks(page_id), parse_tables(html_text), me)
    if "refused" in plan:
        rep["refused"] = plan["refused"]
        return rep
    rep["would_move"] = [{"table": it["table"], "block": it["old"], "after": mv["after"]}
                         for mv in plan["moves"] for it in mv["items"]]
    rep["would_recaption"] = [fix["block"] for fix in plan["recaption"]]
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
