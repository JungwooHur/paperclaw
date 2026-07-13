#!/usr/bin/env python3
r"""Archive leaked arxiv HTML page furniture from a paper's Notion body.

Root cause
----------
When a paper is translated from its arxiv HTML *fulltext* (e.g. via
translate_fulltext, which pulls `notebooklm source fulltext`), the indexed text
includes the page's chrome BEFORE the real content: the nav bar, the "Report an
issue" GitHub widget, the donation banner, "Download PDF", `javascript:` nav
links, the table of contents, and the "License: CC BY ... arXiv:NNNN.NNNNNvN
[cs.RO]" line. NotebookLM translates that furniture straight into the body
(the tell-tale sign is literal `javascript:toggleNavTOC()` sitting in Korean
prose). The per-section paper path never hits this — bounded per-section asks
return only section content; this is specific to whole-fulltext translation of
an arxiv-HTML source.

This archives any block carrying an unambiguous furniture marker. High-precision
markers only (chrome-exclusive strings), so real body content is never touched.
Idempotent; no-op on a clean page. Applies by default; --dry-run reports only.

  strip_furniture.py --page <id> [--dry-run]
"""
import argparse
import json
import re
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import verify_sections as vs
from translate_fulltext import notion

# Chrome-exclusive markers (Korean as translated + English source form). Each is
# unambiguous enough that its presence alone marks the WHOLE block as furniture.
FURNITURE = re.compile(
    r"toggleNavTOC|toggleReadingMode|javascript:"                       # JS nav links
    r"|왜\s*HTML인가|Why\s+HTML"                                         # "Why HTML?"
    r"|PDF\s*다운로드|Download\s+PDF"                                     # download pdf
    r"|arXiv\s*로고|arXiv로\s*돌아가기|Back\s+to\s+arXiv|초록으로\s*돌아가기"  # nav
    r"|GitHub\s*Issue\s*보고|콘텐츠\s*선택이\s*저장"                        # report widget
    r"|GitHub\s*(?:없이|에서)\s*제출|Submit(?:\s+without)?\s+GitHub"
    r"|arXiv는\s*이제\s*독립|arXiv\s+is\s+committed"                       # donation banner
    r"|라이선스:\s*CC\s*BY|License:\s*CC\s*BY"                            # license line
    r"|arXiv:\d{4}\.\d{4,5}v\d+\s*\[[a-z][a-z.\-]+\]",                  # arXiv id/subject line
    re.I)

# INLINE furniture: arxiv-native HTML puts a "Report an issue with the previous
# element" link after EVERY element; sourced from that HTML it leaks as this phrase
# repeated throughout the body (standalone blocks AND mid-paragraph). STRIP it from
# the text (keep the real content) rather than archive the block; a block that is
# nothing BUT the phrase ends up empty and is archived.
INLINE = re.compile(
    r"\s*이전\s*요소에?\s*대한?\s*문제\s*보고\s*"
    r"|\s*Report\s+an?\s+issue(?:\s+(?:with|for|on)\s+(?:the\s+)?previous\s+element)?\s*",
    re.I)

_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "bulleted_list_item", "numbered_list_item", "callout")


def _text(b: dict) -> str:
    t = b["type"]
    return "".join(x.get("plain_text", "") for x in (b.get(t) or {}).get("rich_text", []))


def _strip_inline_runs(rich_text):
    """Strip INLINE furniture from each text run; keep equations/mentions. Returns
    (new_runs, changed)."""
    out, changed = [], False
    for r in rich_text:
        if r.get("type", "text") != "text":
            out.append(r)
            continue
        content = r.get("text", {}).get("content", "")
        if not INLINE.search(content):
            out.append(r)
            continue
        new = re.sub(r"[ \t]{2,}", " ", INLINE.sub(" ", content))
        changed = True
        if not new.strip():
            continue                                   # run was pure furniture
        nr = json.loads(json.dumps(r))                 # keep annotations
        nr["text"]["content"] = new
        nr.pop("plain_text", None)                     # read-only; Notion recomputes
        nr.pop("href", None)
        out.append(nr)
    return out, changed


def strip_furniture(page_id: str, apply: bool = False) -> dict:
    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "scanned": len(blocks), "archived": 0, "edited": 0}
    for b in blocks:
        if b["type"] not in _TEXT_TYPES:
            continue
        t = b["type"]
        text = _text(b)
        # 1) whole-block furniture (nav / TOC widget / license / …) -> archive
        if FURNITURE.search(text):
            rep["archived"] += 1
            if apply:
                notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                time.sleep(0.34)
            continue
        # 2) inline furniture phrase -> strip; archive the block if nothing remains
        if INLINE.search(text):
            rt = (b.get(t) or {}).get("rich_text", [])
            new_rt, changed = _strip_inline_runs(rt)
            if not changed:
                continue
            remaining = "".join(x.get("text", {}).get("content", "")
                                for x in new_rt if x.get("type", "text") == "text")
            has_eq = any(x.get("type") == "equation" for x in new_rt)
            if not remaining.strip() and not has_eq:
                rep["archived"] += 1
                if apply:
                    notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                    time.sleep(0.34)
            else:
                rep["edited"] += 1
                if apply:
                    notion("PATCH", f"/blocks/{b['id']}", {t: {"rich_text": new_rt}})
                    time.sleep(0.34)
    rep["would_archive"] = rep["archived"]
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    a = ap.parse_args()
    import json
    print(json.dumps(strip_furniture(a.page, apply=not a.dry_run),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
