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


def render_tables(arxiv_id: str, ids: list, outdir: str) -> dict:
    """Screenshot each table element from the live arxiv HTML. {id: png_path}."""
    from playwright.sync_api import sync_playwright
    paths = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1500, "height": 2400},
                              device_scale_factor=2)
        try:
            pg.goto(f"https://arxiv.org/html/{arxiv_id}", wait_until="networkidle",
                    timeout=60000)
        except Exception:
            pg.goto(f"https://arxiv.org/html/{arxiv_id}", timeout=60000)
        for tid in ids:
            el = pg.query_selector(f'[id="{tid}"]')
            if not el:
                continue
            path = os.path.join(outdir, f"table_{tid.replace('.', '_')}.png")
            try:
                el.screenshot(path=path)
                if os.path.getsize(path) > 300:
                    paths[tid] = path
            except Exception:
                pass
        browser.close()
    return paths


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
    paths = render_tables(arxiv_id, [t["id"] for t in tables], outdir)

    # place images grouped by anchor, in document order
    groups, order = {}, []
    for t in tables:
        if t["id"] not in paths:
            continue
        anchor = _table_anchor(t["num"], blocks) or "__end__"
        groups.setdefault(anchor, []).append((t, paths[t["id"]]))
        if anchor not in order:
            order.append(anchor)
    for key in order:
        children = []
        for t, path in groups[key]:
            fid = upload_image(path)
            if fid:
                children.append(ef._image_block(fid, t["caption"]))
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
