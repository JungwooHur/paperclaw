#!/usr/bin/env python3
r"""Rebuild a paragraph whose math delimiters were mis-parsed, so prose ended up
stored INSIDE equation spans and formulas ended up as plain text.

The defect
----------
This is the severe form of the bare-delimiter parsing bug `heal_equations` repairs.
There, `\(EXPR\)` merely lost a backslash into the neighbouring text. Here the
opening delimiter of a run was damaged, so every following delimiter flipped the
math/prose phase and the block was stored with the two SWAPPED:

    span[6]  EQUATION  '및 '                       <- a Korean word, as an equation
    span[8]  EQUATION  '가 position information과 …'  <- a whole Korean sentence
    span[9]  EQUATION  '\Theta_q(\mathbf{x}_q'      <- formula cut at the comma
    span[10] TEXT      ', m) - '                     <- its other half, as prose

and the block's text still carried literal `\(` / `\)` debris. A whole derivation
section rendered as unreadable soup.

Why the delimiters can't be trusted to fix it
---------------------------------------------
Re-splitting on `\(`…`\)` reproduces the same inversion: the surviving delimiters
wrap prose and formulas alternately, because the run that lost its opener shifted
the phase for everything after it. Both a strict and a lenient delimiter scan were
tried and both put Korean back inside equations. So the rebuild ignores delimiters
entirely and classifies by CONTENT, which is unambiguous here: a `\begin{aligned}`
environment is a display equation, Korean text is prose, and bare LaTeX left in the
prose is wrapped by `wrap_math` (insert-only, already trusted elsewhere).

What it does NOT claim to do
----------------------------
It cannot restore text the translation never produced. A run that ends inside an
unterminated environment stays truncated — the block is emitted and reported as
such, because that needs the section re-translated, not re-parsed.

  heal_mangled_math.py --page <id> [--dry-run]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KOREAN = re.compile(r"[가-힣]")
# Display environments, including one left unterminated by a truncated translation.
ENV = re.compile(r"\\begin\{(aligned|align|alignat|cases|matrix|bmatrix|pmatrix|gather)\}"
                 r".*?(?:\\end\{\1\}|$)", re.S)
TEXT_TYPES = ("paragraph", "quote", "bulleted_list_item", "numbered_list_item")


def is_mangled(block: dict) -> bool:
    r"""True when this block's equation spans hold prose, or its text still carries
    `\(` / `\)` debris — the two fingerprints of the swapped parse."""
    t = block.get("type")
    if t not in TEXT_TYPES:
        return False
    spans = (block.get(t) or {}).get("rich_text", [])
    for s in spans:
        if s.get("type") == "equation":
            expr = (s.get("equation") or {}).get("expression", "")
            # A formula never contains Korean; a Korean run inside an equation span
            # is prose that the parse swallowed.
            if KOREAN.search(expr):
                return True
            if re.search(r"\\{1,2}[()]", expr):
                return True
    return False


def flatten(block: dict) -> str:
    """The block's original text: equation spans re-emitted as their raw expression,
    which is what they were before the parse mangled the boundaries."""
    t = block["type"]
    return "".join(s["equation"]["expression"] if s.get("type") == "equation"
                   else s.get("plain_text", "")
                   for s in (block.get(t) or {}).get("rich_text", []))


def _balance_dollar_runs(text: str) -> str:
    r"""Extend a `$…$` run that stops inside an unclosed `(` so it takes its closing
    paren with it: `$\Theta_q(\mathbf{x}_q$, m)` -> `$\Theta_q(\mathbf{x}_q, m)$`.
    wrap_math is deliberately conservative and stops at the comma; left alone the
    formula renders split across a plain-text tail."""
    out, pos = [], 0
    for m in re.finditer(r"\$([^$\n]+)\$", text):
        out.append(text[pos:m.start()])
        expr = m.group(1)
        pos = m.end()
        need = expr.count("(") - expr.count(")")
        if need > 0:
            tail = text[pos:]
            take = 0
            for i, ch in enumerate(tail):
                if ch == ")":
                    need -= 1
                    if need == 0:
                        take = i + 1
                        break
                elif ch in "$\n":
                    break
            if take:
                expr += tail[:take]
                pos += take
        out.append("$" + expr + "$")
    out.append(text[pos:])
    return "".join(out)


def rebuild(text: str):
    """[(kind, content)] with kind in {'paragraph','equation'}; plus a truncated flag."""
    from wrap_math import wrap_math_text

    held = []

    def hold(m):
        held.append(m.group(0))
        return f"\x00E{len(held) - 1}\x00"

    protected = ENV.sub(hold, text)
    # Strip delimiter debris — only outside the protected environments, so the `\\`
    # line breaks that carry an environment's row structure are never touched.
    protected = re.sub(r"\\{1,2}[()]", " ", protected)
    protected = re.sub(r"(?<![A-Za-z0-9])\\{1,2}(?=\s|$)", " ", protected)
    protected = re.sub(r"(?<![A-Za-z])\\(?=[a-zA-Z]\b)", "", protected)
    protected = re.sub(r"[ \t]{2,}", " ", protected)

    parts, pos, truncated = [], 0, False
    for m in re.finditer(r"\x00E(\d+)\x00", protected):
        pre = protected[pos:m.start()].strip()
        if pre:
            parts.append(("paragraph", pre))
        body = held[int(m.group(1))]
        if "\\end{" not in body:
            truncated = True
        tail = protected[m.end():m.end() + 30]
        num = re.match(r"\s*\\quad\s*\((\d+[a-z]?)\)", tail)
        parts.append(("equation", body + (f" \\quad ({num.group(1)})" if num else "")))
        pos = m.end() + (num.end() if num else 0)
    rest = protected[pos:].strip()
    if rest:
        parts.append(("paragraph", rest))

    out = []
    for kind, c in parts:
        if kind == "equation":
            out.append((kind, c.strip()))
        else:
            out.append((kind, _balance_dollar_runs(wrap_math_text(c))))
    return out, truncated


def heal_page(page_id: str, apply: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion
    from save_qa_callout import _inline_rich_text

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "mangled_blocks": 0, "new_blocks": 0,
           "truncated_runs": 0, "runs": []}

    # Consecutive mangled blocks are ONE run: Notion splits a long paragraph at its
    # block size limit, so a formula can straddle the boundary and only the joined
    # text parses correctly.
    runs, cur = [], []
    for i, b in enumerate(blocks):
        if is_mangled(b):
            cur.append(i)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)

    for run in runs:
        text = "".join(flatten(blocks[i]) for i in run)
        parts, truncated = rebuild(text)
        if not parts:
            continue
        rep["mangled_blocks"] += len(run)
        rep["new_blocks"] += len(parts)
        rep["truncated_runs"] += 1 if truncated else 0
        rep["runs"].append({"blocks": run, "chars": len(text),
                            "paragraphs": sum(1 for k, _ in parts if k == "paragraph"),
                            "equations": sum(1 for k, _ in parts if k == "equation"),
                            "truncated": truncated})
        if not apply:
            continue
        children = []
        for kind, c in parts:
            if kind == "equation":
                children.append({"object": "block", "type": "equation",
                                 "equation": {"expression": c[:1000]}})
            else:
                children.append({"object": "block", "type": "paragraph",
                                 "paragraph": {"rich_text": _inline_rich_text(c)}})
        # insert after the run, then archive the originals — never the other way
        # round, so a failed insert can't leave the section empty.
        notion("PATCH", f"/blocks/{page_id}/children",
               {"children": children, "after": blocks[run[-1]]["id"]})
        time.sleep(0.4)
        for i in run:
            notion("PATCH", f"/blocks/{blocks[i]['id']}", {"archived": True})
            time.sleep(0.25)
    return rep


def main() -> int:
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(heal_page(a.page, apply=not a.dry_run), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
