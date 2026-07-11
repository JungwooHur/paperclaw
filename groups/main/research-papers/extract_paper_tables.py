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

Placement mirrors figures: the image goes right after the paragraph that first
mentions the table number (`표 N` / `Table N`), fallback numbered section heading,
fallback page end. Uploaded PRIVATELY via notion_upload.

Safe removal (default)
----------------------
The flattened table text is entangled with prose (one block can hold a table's
data tail AND the next real paragraph). So only PURE-table blocks are archived —
high numeric density, low Korean-prose ratio, no leaked heading, no prose sentence
in the tail. Mixed blocks are left untouched (no prose is ever lost); a little
numeric residue may remain. Idempotent (skips if the page already has table
images, detected by an image caption starting "Table").

  extract_paper_tables.py --page <id> --arxiv <id> [--dry-run] [--force] [--keep-text]
"""
import argparse
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_paper_figures as ef   # fetch_html, _clean, _block_text, _image_block

_TNUM = re.compile(r"T(\d+)")
_TABLE_REF = None  # built per-number


_TID = re.compile(r'\bid="((?:S|A)\d+\.T(\d+))"')


def parse_tables(html_text: str) -> list:
    """Ordered list of {id, num, caption} for each table id `SnTm` / `AnTm`.
    Scans ids directly (not <figure> boundaries) so nested table-figures — which
    truncate a non-greedy `<figure>…</figure>` match — are not dropped. The id is
    what render_tables screenshots, so this is the robust key."""
    out, seen = [], set()
    for m in _TID.finditer(html_text):
        fid = m.group(1)
        if fid in seen:
            continue
        seen.add(fid)
        cap = ""
        cm = re.search(re.escape(fid) + r'".*?<figcaption[^>]*>(.*?)</figcaption>',
                       html_text[m.start():m.start() + 8000], re.DOTALL | re.I)
        if cm:
            cap = ef._clean(cm.group(1))[:1900]
        out.append({"id": fid, "num": int(m.group(2)), "caption": cap})
    return out


def _table_anchor(num, blocks: list):
    """First block mentioning `표 N` / `Table N`; fallback numbered section heading."""
    if num is None:
        return None
    ref = re.compile(rf"(?:표|Table)\s*0*{num}\b")
    for b in blocks:
        if b["type"] in ef.TEXT_TYPES and ref.search(ef._block_text(b)):
            return b["id"]
    head = re.compile(rf"^\s*{num}(?:[.\s])")
    for b in blocks:
        if b["type"].startswith("heading") and head.match(ef._block_text(b)):
            return b["id"]
    return None


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

# Union of the container's actual table/panel/caption boxes, in PAGE coordinates.
# Clipping to this (not to the wrapper's own box) captures the full table without
# a width reset (no over-expansion) and merges a table's sub-parts into ONE image
# instead of fragmenting them; headings are excluded (not in the selector).
_UNION_JS = """e => {
  const els = e.querySelectorAll('table,.ltx_figure_panel,.ltx_tabular,figcaption');
  let l=1e9,t=1e9,r=-1e9,b=-1e9;
  for (const n of els){ const c=n.getBoundingClientRect();
    if (c.width<10||c.height<10) continue;
    l=Math.min(l,c.left); t=Math.min(t,c.top); r=Math.max(r,c.right); b=Math.max(b,c.bottom); }
  if (r<0) return null;
  return {x:l+window.scrollX, y:t+window.scrollY, w:r-l, h:b-t};
}"""


def render_tables(arxiv_id: str, tables: list, outdir: str) -> list:
    """Screenshot each table container once. Returns [{num, caption, path}] — one
    image per container, placed at the container's lowest table number."""
    from playwright.sync_api import sync_playwright
    cap = {t["num"]: t["caption"] for t in tables}
    out = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 2400, "height": 3200},
                              device_scale_factor=2)
        try:
            pg.goto(f"https://arxiv.org/html/{arxiv_id}", wait_until="networkidle",
                    timeout=60000)
        except Exception:
            pg.goto(f"https://arxiv.org/html/{arxiv_id}", timeout=60000)
        pg.evaluate("for (const n of document.querySelectorAll('*')) "
                    "{ n.style.overflow='visible'; n.style.maxWidth='none'; }")
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
        idx = 0
        for wmid, c in group_el.items():
            num = sorted(group_nums[wmid])[0]
            u = c.evaluate(_UNION_JS)
            if not u or u["w"] < 40 or u["h"] < 20:
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
            if not os.path.exists(path) or os.path.getsize(path) < 300:
                continue
            out.append({"num": num, "caption": cap.get(num, ""), "path": path})
        browser.close()
    return out


def inject_tables(page_id: str, arxiv_id: str, apply: bool = False,
                  force: bool = False, keep_text: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion
    from notion_upload import upload_image

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "found": 0, "placed": 0, "archived": 0,
           "skipped_existing": False}

    def _has_table_images():
        for b in blocks:
            if b["type"] == "image":
                for c in (b.get("image", {}).get("caption") or []):
                    if c.get("plain_text", "").strip().lower().startswith("table"):
                        return True
        return False

    if _has_table_images() and not force:
        rep["skipped_existing"] = True
        return rep

    html_text, _ = ef.fetch_html(arxiv_id)
    if not html_text:
        rep["error"] = "no HTML source"
        return rep
    tables = parse_tables(html_text)
    rep["found"] = len(tables)
    if not apply:
        rep["would_place"] = [t["num"] for t in tables]
        rep["would_archive"] = sum(1 for b in blocks
                                   if b["type"] in ef.TEXT_TYPES
                                   and _is_pure_table(ef._block_text(b)))
        return rep

    outdir = tempfile.mkdtemp(prefix="paper_tables_")
    images = render_tables(arxiv_id, tables, outdir)   # [{num, caption, path}]
    rep["rendered"] = len(images)

    # place images grouped by anchor (first `표 N` mention), in document order
    groups, order = {}, []
    for im in images:
        anchor = _table_anchor(im["num"], blocks) or "__end__"
        groups.setdefault(anchor, []).append(im)
        if anchor not in order:
            order.append(anchor)
    for key in order:
        children = []
        for im in groups[key]:
            fid = upload_image(im["path"])
            if fid:
                children.append(ef._image_block(fid, im["caption"]))
                time.sleep(0.2)
        if not children:
            continue
        body = {"children": children}
        if key != "__end__":
            body["after"] = key
        notion("PATCH", f"/blocks/{page_id}/children", body)
        rep["placed"] += len(children)
        time.sleep(0.34)

    # safe removal: archive only pure-table blocks (never mixed/prose)
    if not keep_text:
        for b in blocks:
            if b["type"] in ef.TEXT_TYPES and _is_pure_table(ef._block_text(b)):
                notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                rep["archived"] += 1
                time.sleep(0.34)
    return rep


def heal_tables(page_id: str, apply: bool = False) -> dict:
    aid = ef.arxiv_id_from_page(page_id)
    if not aid:
        return {"page": page_id, "arxiv": None, "placed": 0}
    return inject_tables(page_id, aid, apply=apply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv", required=True, help="arxiv id (NNNN.NNNNN)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-text", action="store_true",
                    help="inject images but do not archive any flattened-table text")
    a = ap.parse_args()
    rep = inject_tables(a.page, a.arxiv, apply=not a.dry_run,
                        force=a.force, keep_text=a.keep_text)
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
