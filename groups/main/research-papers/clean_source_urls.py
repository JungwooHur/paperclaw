#!/usr/bin/env python3
"""Strip leaked NotebookLM source-image artifacts from an already-built Notion
page's text blocks.

`notebooklm source fulltext` emits each source PDF's embedded-image URLs
(lh3.googleusercontent.com/notebooklm/<token>=w..-h..-v0) plus their image UUIDs
and bare page-number markers. A faithful "do not summarize" translation echoes
them as paragraph text, so pages built before strip_source_urls() carry that
noise. This cleans them in place: it edits each text block's rich_text runs
(URL/markers removed), and archives any block left empty. The privately-uploaded
image BLOCKS are untouched — only text runs are cleaned.

  clean_source_urls.py --page <id> [--apply]      # dry-run without --apply

Reuses strip_source_urls() so the page cleanup and the translator stay in lock-step.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from translate_fulltext import strip_source_urls, notion
import verify_sections as vs

# block types whose `.rich_text` holds translated body text
_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "callout", "bulleted_list_item", "numbered_list_item",
               "toggle", "to_do")

_MARKER = "lh3.googleusercontent.com"


def _clean_runs(rich_text):
    """Return (new_runs, changed). Strip the artifact from each text run; drop a
    run that becomes empty. Non-text runs (mentions/equations) pass through."""
    out, changed = [], False
    for r in rich_text:
        if r.get("type", "text") != "text":
            out.append(r)
            continue
        content = r.get("text", {}).get("content", "")
        if _MARKER not in content:
            out.append(r)
            continue
        cleaned = strip_source_urls(content)
        changed = True
        if not cleaned.strip():
            continue                              # whole run was URL noise
        nr = json.loads(json.dumps(r))            # deep copy, keep annotations
        nr["text"]["content"] = cleaned
        if nr["text"].get("link") is None and "plain_text" in nr:
            nr["plain_text"] = cleaned
        out.append(nr)
    return out, changed


def clean_page(page_id, apply=False):
    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "scanned": len(blocks), "edited": 0, "archived": 0}
    for b in blocks:
        t = b["type"]
        if t not in _TEXT_TYPES:
            continue
        rt = b.get(t, {}).get("rich_text", [])
        if not any(_MARKER in r.get("text", {}).get("content", "") for r in rt):
            continue
        new_rt, changed = _clean_runs(rt)
        if not changed:
            continue
        if not new_rt:                            # nothing left -> remove block
            rep["archived"] += 1
            if apply:
                notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                time.sleep(0.35)
        else:
            rep["edited"] += 1
            if apply:
                notion("PATCH", f"/blocks/{b['id']}", {t: {"rich_text": new_rt}})
                time.sleep(0.35)
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run report only)")
    a = ap.parse_args()
    print(json.dumps(clean_page(a.page, a.apply), indent=2))
