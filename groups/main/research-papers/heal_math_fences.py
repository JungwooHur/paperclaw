#!/usr/bin/env python3
r"""Turn formulas that were stored as CODE blocks into real Notion equation blocks.

Why
---
Body math reaches Notion as `$…$` and becomes equation objects. A Q&A answer,
though, usually writes its math inside a ``` fence, and the converter faithfully
renders that as monospace code — so the very same formula shows as typeset maths in
the body and as plain text in the answer right below it. Answers should read like
the body.

This sweeps existing pages (the converter now handles it at creation time, but
every Q&A written before that is still code). Q&A answers live NESTED inside a
callout > toggle, so the walk has to descend — a top-level scan sees none of them.

Safety
------
`is_formula_fence` (shared with the converter, single source of truth) is
deliberately conservative: Korean text, multi-line ASCII-art matrices, flow
diagrams, string data and anything that looks like code stay exactly as they are.
Dry-run over the whole DB before changing it: on the page that motivated the rule it
was already clean, and only the full sweep exposed a weather forecast and python dict
reprs slipping through — a wrong conversion renders as a red KaTeX error, so a missed
formula is always the cheaper mistake.

  heal_math_fences.py --page <id> [--dry-run]
  heal_math_fences.py --all       [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_DEPTH = 4


def _code_text(block: dict) -> str:
    return "".join(s.get("plain_text", "")
                   for s in (block.get("code") or {}).get("rich_text", []))


def _iter_code_blocks(page_id: str, max_depth: int = MAX_DEPTH):
    """Every code block on the page, INCLUDING ones nested in a callout/toggle —
    which is exactly where Q&A answers live."""
    import verify_sections as vs
    from translate_fulltext import notion

    def children(bid):
        out, cur = [], None
        while True:
            path = f"/blocks/{bid}/children?page_size=100"
            if cur:
                path += f"&start_cursor={cur}"
            d = notion("GET", path)
            out += d.get("results", [])
            if not d.get("has_more"):
                return out
            cur = d["next_cursor"]

    found = []

    def walk(blocks, depth):
        for b in blocks:
            if b.get("type") == "code":
                found.append(b)
            if b.get("has_children") and depth < max_depth:
                try:
                    walk(children(b["id"]), depth + 1)
                except Exception:
                    continue

    walk(vs.fetch_blocks(page_id), 0)   # a failure here is the caller's to report
    return found


def heal_page(page_id: str, apply: bool = False) -> dict:
    import time
    from save_qa_callout import is_formula_fence

    rep = {"page": page_id, "code_blocks": 0, "converted": 0, "samples": []}
    try:
        blocks = _iter_code_blocks(page_id)
    except Exception as e:
        # Notion answers 429 on a long whole-DB sweep. One unreachable page must not
        # end the run — the next cycle picks it up.
        rep["error"] = f"{type(e).__name__}: {e}"
        return rep
    for b in blocks:
        rep["code_blocks"] += 1
        lang = (b.get("code") or {}).get("language", "")
        text = _code_text(b)
        if not is_formula_fence(lang, text):
            continue
        rep["converted"] += 1
        if len(rep["samples"]) < 5:
            rep["samples"].append(text.strip()[:70])
        if apply:
            # A code block cannot be retyped in place, so insert the equation right
            # after it and only then archive the code — that order keeps the new
            # block in the old one's position and never leaves a gap on failure.
            _swap(b, text)
            time.sleep(0.3)
    return rep


def _swap(block: dict, text: str) -> None:
    """Put an equation block where this code block is, then archive the code."""
    from translate_fulltext import notion
    parent = block.get("parent") or {}
    pid = (parent.get("page_id") or parent.get("block_id") or "").replace("-", "")
    if not pid:
        return
    notion("PATCH", f"/blocks/{pid}/children",
           {"children": [{"object": "block", "type": "equation",
                          "equation": {"expression": text.strip()[:1000]}}],
            "after": block["id"]})
    notion("PATCH", f"/blocks/{block['id']}", {"archived": True})


def main() -> int:
    import json
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--page")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    apply = not a.dry_run
    if a.page:
        pages = [a.page]
    else:
        from auto_fix_qa import query_paper_pages
        pages = query_paper_pages()
    import time
    total, failed = 0, 0
    for i, pid in enumerate(pages):
        rep = heal_page(pid, apply=apply)
        total += rep["converted"]
        failed += bool(rep.get("error"))
        if rep["converted"] or rep.get("error"):
            print(json.dumps(rep, ensure_ascii=False), flush=True)
        if len(pages) > 1 and i + 1 < len(pages):
            time.sleep(0.4)     # a whole-DB sweep otherwise trips Notion's rate limit
    print(f"converted {total} code block(s) to equations across {len(pages)} page(s)"
          f"{f', {failed} unreachable' if failed else ''}"
          f"{' (dry-run)' if not apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
