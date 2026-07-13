#!/usr/bin/env python3
r"""Extract an arxiv paper's figures and inject them into its Notion page.

Why this exists
---------------
Figure extraction/injection was the ONE part of the paper workflow with no
structural backstop — Phase 3 was pasted-prose the agent had to copy-run, and it
skipped it (the recurring "prose isn't load-bearing" pattern: most recently
processed papers ended up with 0 figures even though the source HTML has them).
Everything else (back-matter, source URLs, math, Q&A, citations) is enforced by a
script + healer + verify check; figures now are too.

Deterministic placement
------------------------
arxiv-native / ar5iv LaTeXML gives every figure `<figure id="S3.F2">` where the
`F<m>` is the figure NUMBER. The translated Notion body references each figure as
`그림 <m>` / `Figure <m>` / `Fig. <m>` (NotebookLM keeps figure refs), and section
headings keep their number (`4 GAM`, `부록 A`). So each figure is inserted right
after the paragraph that first mentions its number; fallback to the numbered
section heading; fallback to the page end. No NotebookLM round-trip needed.

Idempotent: if the page already has image blocks it is left alone (unless --force).
Figures are uploaded PRIVATELY into Notion via notion_upload (never a public host).

  extract_paper_figures.py --page <id> --arxiv <id> [--dry-run] [--force]
"""
import argparse
import html as _html
import os
import re
import sys
import urllib.request
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_FIG = re.compile(r'<figure[^>]*\bid="([^"]+)"[^>]*>(.*?)</figure>', re.DOTALL | re.I)
_IMG = re.compile(r'<img[^>]+\bsrc="([^"]+)"', re.I)
_CAP = re.compile(r'<figcaption[^>]*>(.*?)</figcaption>', re.DOTALL | re.I)
_TAG = re.compile(r'<[^>]+>')
_FNUM = re.compile(r'F(\d+)')

TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
              "bulleted_list_item", "numbered_list_item", "callout")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub("", s))).strip()


def fetch_html(arxiv_id: str):
    """Return (html, source_url). arxiv-native first (latest version), ar5iv
    fallback (often stale v1 — see the ar5iv note in CLAUDE.md)."""
    for url in (f"https://arxiv.org/html/{arxiv_id}",
                f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read().decode("utf-8", "replace")
                final = r.geturl()
            if "<figure" in data:
                return data, final
        except Exception:
            continue
    return None, None


def parse_figures(html_text: str, source_url: str) -> list:
    """Ordered list of {id, num, img_url, caption}, one per figure image,
    deduped by image URL (composite figures expose one img per subfigure)."""
    # Resolve relative img srcs against the page's <base href> if present. Some
    # arxiv HTML pages set `<base href="/html/<id>vN/">` and give srcs relative to
    # it (`images/x.jpg`); urljoin against the page URL alone would drop the version
    # dir and 404. Others have no <base> and srcs already include the version dir.
    bm = re.search(r'<base[^>]+href="([^"]+)"', html_text, re.I)
    base = urljoin(source_url, _html.unescape(bm.group(1))) if bm else source_url
    out, seen = [], set()
    for fid, body in _FIG.findall(html_text):
        img = _IMG.search(body)
        if not img:
            continue
        url = urljoin(base, _html.unescape(img.group(1)))
        if url in seen:
            continue
        seen.add(url)
        num = _FNUM.search(fid)
        cap = _CAP.search(body)
        out.append({"id": fid,
                    "num": int(num.group(1)) if num else None,
                    "img_url": url,
                    "caption": _clean(cap.group(1))[:1900] if cap else ""})
    return out


def _block_text(b: dict) -> str:
    t = b["type"]
    return "".join(x.get("plain_text", "")
                   for x in (b.get(t) or {}).get("rich_text", []))


def _anchor_for(num, blocks: list):
    """Block id to insert a figure `num` after: first body mention of the figure
    number, else the section heading whose number matches, else None (page end)."""
    if num is None:
        return None
    ref = re.compile(rf"(?:그림|Figure|Fig\.?)\s*0*{num}\b")
    for b in blocks:
        if b["type"] in TEXT_TYPES and ref.search(_block_text(b)):
            return b["id"]
    # fallback: a numbered section heading `N ...` / `N.M ...` starting with num
    head = re.compile(rf"^\s*{num}(?:[.\s])")
    for b in blocks:
        if b["type"].startswith("heading") and head.match(_block_text(b)):
            return b["id"]
    return None


def _image_block(fid: str, caption: str) -> dict:
    img = {"type": "file_upload", "file_upload": {"id": fid}}
    if caption:
        img["caption"] = [{"type": "text", "text": {"content": caption[:2000]}}]
    return {"object": "block", "type": "image", "image": img}


def inject_figures(page_id: str, arxiv_id: str, apply: bool = False,
                   force: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion
    from notion_upload import upload_image

    blocks = vs.fetch_blocks(page_id)
    # Count only NON-table images: table images (caption "Table N", injected by
    # extract_paper_tables) must not make the figure healer think figures exist.
    def _is_fig_img(b):
        if b["type"] != "image":
            return False
        cap = "".join(c.get("plain_text", "") for c in
                      (b.get("image", {}).get("caption") or [])).strip().lower()
        return not cap.startswith("table")
    have_imgs = sum(1 for b in blocks if _is_fig_img(b))
    rep = {"page": page_id, "existing_images": have_imgs,
           "found": 0, "placed": 0, "skipped_existing": False}
    if have_imgs and not force:
        rep["skipped_existing"] = True          # idempotent: don't duplicate
        return rep

    html_text, src = fetch_html(arxiv_id)
    if not html_text:
        rep["error"] = "no HTML source with figures"
        return rep
    figs = parse_figures(html_text, src)
    rep["found"] = len(figs)
    rep["source"] = src

    # group images by anchor block, preserving document order within a group
    groups, order = {}, []
    for f in figs:
        anchor = _anchor_for(f["num"], blocks)
        key = anchor or "__end__"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    for key in order:
        children = []
        for f in groups[key]:
            if apply:
                fid = upload_image_from_url(f["img_url"])
                if not fid:
                    continue
                children.append(_image_block(fid, f["caption"]))
                time.sleep(0.2)
            else:
                children.append({"_fig": f["num"], "_after": key})
        if not children:
            continue
        rep["placed"] += len(children)
        if apply:
            body = {"children": children}
            if key != "__end__":
                body["after"] = key
            notion("PATCH", f"/blocks/{page_id}/children", body)
            time.sleep(0.34)
    return rep


_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|html|pdf)/(\d{4}\.\d{4,5})", re.I)


def arxiv_id_from_page(page_id: str):
    """Return the arxiv id from the page's 'Paper URL' property, or None."""
    from translate_fulltext import notion
    pg = notion("GET", f"/pages/{page_id}")
    for prop in (pg.get("properties") or {}).values():
        if prop.get("type") == "url" and prop.get("url"):
            m = _ARXIV_RE.search(prop["url"])
            if m:
                return m.group(1)
    return None


def heal_figures(page_id: str, apply: bool = False) -> dict:
    """Healer entry: inject figures when the page has none and its Paper URL
    resolves to an arxiv id. Idempotent no-op otherwise (missing id, or the page
    already has images — inject_figures guards on both)."""
    aid = arxiv_id_from_page(page_id)
    if not aid:
        return {"page": page_id, "arxiv": None, "placed": 0}
    return inject_figures(page_id, aid, apply=apply)


def upload_image_from_url(url: str):
    """Download an image to a temp file and upload it privately into Notion."""
    from notion_upload import upload_image
    import tempfile
    ext = os.path.splitext(url.split("?")[0])[1] or ".png"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception:
        return None
    if len(data) < 100:
        return None
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        return upload_image(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv", required=True, help="arxiv id (NNNN.NNNNN)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="inject even if the page already has image blocks")
    a = ap.parse_args()
    rep = inject_figures(a.page, a.arxiv, apply=not a.dry_run, force=a.force)
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
