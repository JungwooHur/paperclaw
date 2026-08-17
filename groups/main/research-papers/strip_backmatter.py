#!/usr/bin/env python3
"""Remove erroneously-translated back-matter from a translated paper page.

A paper's translated page is meant to be the BODY only (Abstract .. Conclusion);
verify_sections even measures the source "cutting the tail at References". But the
section-by-section workflow pulls its section list from NotebookLM (which lists
"References"), so the agent translates the bibliography like body prose — and a
bibliography run through translation comes out mangled: author names pick up a
Korean "그리고", citation numbers renumber per chunk ([1],[2],[12],[1]…), and
entries fragment across blocks. References/Acknowledgements should never be
translated.

This finds the FIRST back-matter heading (References / Bibliography / 참고문헌 /
Acknowledgements / Disclosure of Funding) and archives it plus everything after it.

  strip_backmatter.py --page <id> [--apply]     # dry-run without --apply
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
# Heading text (normalized) that marks the start of non-body back-matter. Anchored
# at the start (after an optional section number) so body headings like "Related
# Work" or "Neural Rendering" never match.
_BACKMATTER = re.compile(
    r"^\s*\d*\.?\s*(references|bibliography|참고\s*문헌|"
    r"acknowledge?ments?|disclosure of funding)\b", re.I)


def _headers():
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        # raise (don't sys.exit) so this stays importable by a healer/workflow
        raise ValueError("NOTION_TOKEN environment variable must be set")
    return {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _api(method, path, body=None, tries=8):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(
                API + path, data=json.dumps(body).encode() if body else None,
                method=method, headers=_headers())
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 5)) + 2 * a)
                continue
            if e.code in (500, 502, 503, 504):  # transient server error — retry
                time.sleep(2 + 2 * a)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 + 2 * a)
    raise last


def fetch_blocks(page_id):
    out, cur = [], None
    while True:
        d = _api("GET", f"/blocks/{page_id}/children?page_size=100"
                 + (f"&start_cursor={cur}" if cur else ""))
        out += d.get("results", [])
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def _text(b):
    o = b.get(b["type"], {})
    # plain_text is present on every rich_text type (text/mention/equation)
    return "".join(x.get("plain_text", "") for x in o.get("rich_text", []))


# Back matter is, by definition, the TAIL. If the match says most of the page is
# back matter, the match is wrong — and acting on it destroys the paper. A leaked
# arxiv table of contents once produced a heading "Acknowledgments https://…#S7…"
# near the TOP, and archiving "everything after it" took 463 of 475 blocks: the
# entire translated body, 129k characters, gone in one pass.
MAX_BACKMATTER_FRACTION = 0.5


def strip_backmatter(page_id, apply=False):
    blocks = fetch_blocks(page_id)
    start = None
    for i, b in enumerate(blocks):
        if not b["type"].startswith("heading"):
            continue
        text = _text(b).strip()
        if not _BACKMATTER.match(text):
            continue
        # A real section heading does not carry a URL. One that does is leaked page
        # chrome (a TOC entry), which strip_furniture removes — never a section
        # boundary to cut the page at.
        if "http://" in text or "https://" in text:
            continue
        start = i
        break
    rep = {"page": page_id, "scanned": len(blocks), "backmatter_from": None, "archived": 0}
    if start is None:
        return rep
    victims = blocks[start:]
    rep["backmatter_from"] = _text(blocks[start]).strip()[:60]
    rep["would_archive"] = len(victims)
    if blocks and len(victims) > len(blocks) * MAX_BACKMATTER_FRACTION:
        rep["refused"] = (f"{len(victims)}/{len(blocks)} blocks is not back matter — "
                          f"refusing to archive; check for leaked page chrome")
        rep["would_archive"] = 0
        return rep
    if apply:
        for b in victims:
            _api("PATCH", f"/blocks/{b['id']}", {"archived": True})
            rep["archived"] += 1
            time.sleep(0.34)
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="archive the back-matter (default: dry-run report)")
    a = ap.parse_args()
    print(json.dumps(strip_backmatter(a.page, a.apply), ensure_ascii=False, indent=2))
