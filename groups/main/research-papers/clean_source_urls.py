#!/usr/bin/env python3
"""Strip leaked source URLs from an already-built Notion page's text blocks.

Two leak types, both from the source bleeding into the translated body:
  1. NotebookLM source-image URLs (lh3.googleusercontent.com/notebooklm/…) plus
     their image UUIDs and bare page-number markers — books built from
     `notebooklm source fulltext`.
  2. ar5iv inline-citation URLs — papers sourced off ar5iv HTML flatten each
     inline [N] link to "N https://ar5iv…#bib.bibN" text, so the body fills with
     "[ 1 <url> , 2 <url> ]" citation groups and "Figure 5 <url>" reference links.

This cleans both in place: it edits each text block's rich_text runs (URLs/markers
removed, citation groups dropped, Figure/Table references kept as text), and
archives any block left empty. Injected image BLOCKS are untouched — only text.

  clean_source_urls.py --page <id> [--apply]      # dry-run without --apply

Reuses strip_source_urls() so the page cleanup and the translator stay in lock-step.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from translate_fulltext import strip_source_urls, notion
import verify_sections as vs

# block types whose `.rich_text` holds translated body text
_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "callout", "bulleted_list_item", "numbered_list_item",
               "toggle", "to_do")

# Two leak types this cleans, both from the paper/book source leaking into body:
#   1. lh3.googleusercontent.com — NotebookLM source-image URLs (books).
#   2. ar5iv citation links — papers sourced off ar5iv HTML flatten each inline
#      [N] link to "N https://ar5iv…#bib.bibN" text.
_MARKERS = ("lh3.googleusercontent.com", "ar5iv", "#bib.bib")

# Bibliography citation groups "[ N url , N url ]" -> remove entirely (per the
# workflow's strip-inline-citations rule). A leftover Figure/Table/Eq reference
# URL (#S.. anchor) -> drop just the URL so the "Figure 5"/"Table 2" text survives.
_BIBCITE = re.compile(r"\s*\[?[\s\d,]*(?:https?://[^\s\]]*#bib\.bib\d+[\s\d,]*)+\]?", re.I)
_ARXIVURL = re.compile(r"\s*https?://[^\s]*(?:ar5iv|arxiv\.org)[^\s]*", re.I)


def strip_citation_urls(text):
    """Remove leaked ar5iv inline-citation URLs (bibliography groups) and any
    stray arxiv/ar5iv reference URL, keeping the 'Figure N'/'Table N' text."""
    if "ar5iv" not in text and "#bib.bib" not in text:
        return text
    text = _BIBCITE.sub(" ", text)
    text = _ARXIVURL.sub("", text)
    text = re.sub(r"\s+([.,;:)])", r"\1", text)     # " ." -> "."
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _clean_text(content):
    """Strip both leak types: NotebookLM image URLs and ar5iv citation URLs."""
    return strip_citation_urls(strip_source_urls(content))


def _has_junk(content):
    return any(m in content for m in _MARKERS)


def _clean_runs(rich_text):
    """Return (new_runs, changed). Strip the artifact from each text run; drop a
    run that becomes empty. Non-text runs (mentions/equations) pass through."""
    out, changed = [], False
    for r in rich_text:
        if r.get("type", "text") != "text":
            out.append(r)
            continue
        content = r.get("text", {}).get("content", "")
        if not _has_junk(content):
            out.append(r)
            continue
        cleaned = _clean_text(content)
        changed = True
        if not cleaned.strip():
            continue                              # whole run was URL noise
        nr = json.loads(json.dumps(r))            # deep copy, keep annotations
        nr["text"]["content"] = cleaned
        # drop read-only fields: they're now stale vs the cleaned content and
        # Notion recomputes them on write (sending stale ones can 400).
        nr.pop("plain_text", None)
        nr.pop("href", None)
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
        if not any(_has_junk(r.get("text", {}).get("content", "")) for r in rt):
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
