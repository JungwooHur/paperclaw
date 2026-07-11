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
import re
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import verify_sections as vs
from translate_fulltext import notion

# Chrome-exclusive markers (Korean as translated + English source form). Each is
# unambiguous enough that its presence alone marks the block as page furniture.
FURNITURE = re.compile(
    r"toggleNavTOC|toggleReadingMode|javascript:"                       # JS nav links
    r"|왜\s*HTML인가|Why\s+HTML"                                         # "Why HTML?"
    r"|PDF\s*다운로드|Download\s+PDF"                                     # download pdf
    r"|arXiv\s*로고|arXiv로\s*돌아가기|Back\s+to\s+arXiv|초록으로\s*돌아가기"  # nav
    r"|GitHub\s*Issue\s*보고|Report\s+an?\s+issue|콘텐츠\s*선택이\s*저장"     # report widget
    r"|GitHub\s*(?:없이|에서)\s*제출|Submit(?:\s+without)?\s+GitHub"
    r"|arXiv는\s*이제\s*독립|arXiv\s+is\s+committed"                       # donation banner
    r"|라이선스:\s*CC\s*BY|License:\s*CC\s*BY"                            # license line
    r"|arXiv:\d{4}\.\d{4,5}v\d+\s*\[[a-z][a-z.\-]+\]",                  # arXiv id/subject line
    re.I)

_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "bulleted_list_item", "numbered_list_item", "callout")


def _text(b: dict) -> str:
    t = b["type"]
    return "".join(x.get("plain_text", "") for x in (b.get(t) or {}).get("rich_text", []))


def strip_furniture(page_id: str, apply: bool = False) -> dict:
    blocks = vs.fetch_blocks(page_id)
    hits = [b for b in blocks
            if b["type"] in _TEXT_TYPES and FURNITURE.search(_text(b))]
    rep = {"page": page_id, "scanned": len(blocks),
           "archived": 0, "would_archive": len(hits),
           "block_ids": [b["id"] for b in hits][:50]}
    if apply:
        for b in hits:
            notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
            time.sleep(0.34)
            rep["archived"] += 1
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
