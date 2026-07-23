#!/usr/bin/env python3
r"""Repair KaTeX-invalid inline equations built with bare-paren `(...)` semantics
instead of `\(...\)`.

Why this exists
---------------
Some paper pages were assembled by a hand-rolled path — NOT the shared
`build_answer_blocks` / `_MATH` converter, whose `\(...\)` regex (`\\\(...\\\)`) is
correct. That path matched inline math with BARE-parenthesis `(...)` semantics, so
for every source `\(EXPR\)` it produced:

  * an equation span `EXPR\`  — the content PLUS the closing `\)`'s backslash, and
  * a stray leading `\` (the opening `\(`) left at the end of the PRECEDING text span.

A lone trailing `\` is ALWAYS invalid KaTeX (an incomplete control sequence), so
every such equation renders as a red error on Notion. Observed on real pages: ALL
of a page's inline equations ending in `\` (`100\`, `\mathbf{x}_{l}\`, `1 \times 1\`),
each preceded by a text span ending in a stray `\`. Source `우리는 또한 \(100\) 및
\(1000\) layers` came out as text`…또한 \` + eq`100\` + text` 및 \` + eq`1000\` + text` layers`.

The shape is an exact, invertible signature: the bug appends exactly one `\` to the
expression and one `\` to the preceding text. So the repair is deterministic:

  * an equation expr with an ODD trailing-backslash run  -> drop one backslash.
    (A valid expr ends in an EVEN run — 0, or `\\` line-break — so `\)` makes it
    odd; stripping one can only FIX, never corrupt a valid equation, because a
    valid equation never ends in a lone `\`.)
  * the text span immediately BEFORE such an equation, if it ends in an odd
    trailing-backslash run  -> drop one backslash (the leaked `\(` opener). Gated
    on the following equation actually being corrupted, so clean prose is untouched.

The repair edits raw rich_text spans SURGICALLY (all annotations/links preserved);
only the parasitic backslashes are removed.

Prevent / Repair / Detect (same shape as the rest of this dir):
  * Prevent — `build_answer_blocks._MATH` already parses `\(...\)` correctly; the
    structural guard is that every page runs through the healer + verify gate.
  * Repair  — `heal_equations()` PATCHes only changed blocks. Wired into
    `heal_paper_pages.py` (the 5-minute qa-heal timer).
  * Detect  — `verify_sections.py` INVALID_EQ flags any equation span KaTeX would
    reject (lone trailing backslash, empty, unbalanced braces).

  heal_equations.py --page <id> [--dry-run]
  heal_equations.py --all       [--dry-run]     # whole research DB (slow)
"""
import re

# Types whose rich_text carries paper-body inline equations + prose. Mirrors
# wrap_math._TEXT_TYPES — deliberately EXCLUDES callout/toggle (Q&A callouts are
# managed by auto_fix_qa; paper math lives in paragraphs/headings/quotes/lists).
_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "bulleted_list_item", "numbered_list_item")

_TRAIL_BS = re.compile(r"\\+$")
_ESC_BRACE = re.compile(r"\\[{}]")


def _odd_trailing_backslashes(s: str) -> bool:
    """True if s ends in an ODD run of backslashes (ignoring trailing whitespace)."""
    m = _TRAIL_BS.search(s.rstrip())
    return bool(m) and len(m.group()) % 2 == 1


def _strip_one_trailing_bs(s: str) -> str:
    r"""Drop exactly one trailing backslash when the run is odd; else unchanged.
    Trailing whitespace after the backslash is dropped with it (a `\(` opener has
    none, so this only tidies)."""
    r = s.rstrip()
    if _odd_trailing_backslashes(r):
        return r[:-1]
    return s


def katex_invalid(expr: str):
    """Reason `expr` is KaTeX-invalid, or None. Conservative — only shapes that
    ALWAYS fail to render, so a heal/flag never touches a valid equation. Escaped
    braces `\\{` / `\\}` are excluded from the brace-balance count (they are literal
    characters, not grouping)."""
    e = (expr or "").strip()
    if not e:
        return "empty"
    if _odd_trailing_backslashes(e):
        return "trailing-backslash"
    bare = _ESC_BRACE.sub("", e)
    if bare.count("{") != bare.count("}"):
        return "unbalanced-braces"
    return None


def _to_input_span(s: dict) -> dict:
    """Fetched rich_text span -> PATCH-able input span, preserving annotations and
    any link. plain_text/href are output-only and dropped."""
    if s.get("type") == "equation":
        out = {"type": "equation",
               "equation": {"expression": (s.get("equation") or {}).get("expression", "")}}
    else:
        txt = s.get("text") or {}
        t = {"content": txt.get("content", s.get("plain_text", ""))}
        if txt.get("link"):
            t["link"] = txt["link"]
        out = {"type": "text", "text": t}
    ann = s.get("annotations")
    if ann:
        out["annotations"] = ann
    return out


def _eq_corrupt(expr: str) -> bool:
    """A corrupted equation from this bug: an odd trailing-backslash run."""
    return _odd_trailing_backslashes(expr or "")


def _starts_with_corrupt_eq(block: dict) -> bool:
    r"""Does `block` BEGIN with a corrupted equation, so its `\(`/`\[` opener leaked
    as a trailing `\` into the PREVIOUS block's tail? True for a corrupted standalone
    equation block, or a text block whose first span is a corrupted inline equation."""
    t = block.get("type")
    if t == "equation":
        return _eq_corrupt((block.get("equation") or {}).get("expression", ""))
    if t in _TEXT_TYPES:
        spans = (block.get(t) or {}).get("rich_text", [])
        return bool(spans) and spans[0].get("type") == "equation" \
            and _eq_corrupt((spans[0].get("equation") or {}).get("expression", ""))
    return False


def _repair_block_spans(spans: list, strip_tail: bool):
    r"""New rich_text list with parasitic backslashes removed, or None if unchanged.
    Fixes inline equation exprs with an odd trailing-backslash run and the text `\(`
    opener immediately before each. If `strip_tail`, also strips a trailing `\` from
    the block's LAST text span — that span is the `\[`/`\(` opener for a corrupted
    equation in the FOLLOWING block."""
    n = len(spans)
    fix_eq = [s.get("type") == "equation"
              and _eq_corrupt((s.get("equation") or {}).get("expression", ""))
              for s in spans]
    if not any(fix_eq) and not strip_tail:
        return None
    out, changed = [], False
    for i, s in enumerate(spans):
        inp = _to_input_span(s)
        if fix_eq[i]:
            new_expr = _strip_one_trailing_bs(inp["equation"]["expression"])
            changed = changed or new_expr != inp["equation"]["expression"]
            if not new_expr.strip():
                continue                       # degenerate `\(\)` -> drop empty eq
            inp["equation"]["expression"] = new_expr
        elif s.get("type") != "equation":
            # a text span is a leaked opener if a corrupted equation follows it —
            # the next span in THIS block, or (for the last span) the next block.
            opener = (i + 1 < n and fix_eq[i + 1]) or (i == n - 1 and strip_tail)
            if opener:
                c = inp["text"]["content"]
                if _odd_trailing_backslashes(c):
                    nc = _strip_one_trailing_bs(c)
                    changed = True
                    if nc == "":
                        continue               # opener was a lone `\` -> drop span
                    inp["text"]["content"] = nc
        out.append(inp)
    return out if changed else None


def heal_equations(page_id: str, apply: bool = False) -> dict:
    import time
    import verify_sections as vs                      # lazy: avoid import cycle
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "scanned": len(blocks), "edited": 0, "equations": 0}
    for i, b in enumerate(blocks):
        t = b["type"]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        strip_tail = bool(nxt) and _starts_with_corrupt_eq(nxt)
        patch = None
        n_eq = 0
        if t == "equation":
            expr = (b.get("equation") or {}).get("expression", "")
            if _eq_corrupt(expr):
                new_expr = _strip_one_trailing_bs(expr)
                if new_expr.strip():                     # skip degenerate `\[\]` -> empty
                    patch = {"equation": {"expression": new_expr}}
                    n_eq = 1
        elif t in _TEXT_TYPES:
            spans = (b.get(t) or {}).get("rich_text", [])
            if spans:
                new = _repair_block_spans(spans, strip_tail)
                if new is not None:
                    patch = {t: {"rich_text": new}}
                    n_eq = sum(1 for s in spans if s.get("type") == "equation"
                               and _eq_corrupt((s.get("equation") or {}).get("expression", "")))
        if patch is None:
            continue
        rep["edited"] += 1
        rep["equations"] += n_eq
        if apply:
            notion("PATCH", f"/blocks/{b['id']}", patch)
            time.sleep(0.35)
    return rep


def main() -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", help="heal a single page id")
    g.add_argument("--all", action="store_true", help="sweep the whole research DB (slow)")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    a = ap.parse_args()
    apply = not a.dry_run
    if a.page:
        pages = [a.page]
    else:
        from auto_fix_qa import query_paper_pages
        pages = query_paper_pages()
    total_blocks = total_eqs = 0
    for pid in pages:
        rep = heal_equations(pid, apply=apply)
        total_blocks += rep["edited"]
        total_eqs += rep["equations"]
        if rep["edited"]:
            print(f"  {pid}: fixed {rep['equations']} equation(s) in {rep['edited']} block(s)"
                  f"{' (dry-run)' if not apply else ''}", file=sys.stderr)
    print(f"repaired {total_eqs} equation(s) across {total_blocks} block(s) / "
          f"{len(pages)} page(s){' (dry-run)' if not apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
