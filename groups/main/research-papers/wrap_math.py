#!/usr/bin/env python3
"""Wrap bare inline LaTeX in $...$ so it renders as Notion equation objects.

Why this exists
---------------
NotebookLM does NOT reliably wrap math in delimiters even when the prompt tells
it to — over a paper's many section answers it wraps some spans ($...$ / \\(..\\))
and emits the rest as *bare* LaTeX (`\\mathbf{c}_{v}`, `L_{s}<m_{1}`, `\\pi_{0.5}`).
`build_answer_blocks` only turns *delimited* math into equation objects, so the
bare spans land in the body as raw source and the user has to fix each one by
hand (Ctrl+Shift+E). Prompt tweaks proved unreliable; the fix has to be
structural, in the same Prevent / Detect / Repair shape as the rest of this dir:

  * Prevent  — save_qa_callout._inline_rich_text() calls wrap_math_text() so
               anything built through the converter (books via translate_fulltext,
               and any paper body routed through it) gets math wrapped up front.
  * Repair   — wrap_math_page() sweeps a *built* page and wraps whatever bare
               LaTeX survived, regardless of how the agent assembled it. Wired
               into heal_paper_pages.py (the 5-minute qa-heal timer).
  * Detect   — verify_sections.py BARE_MATH flags any block still carrying bare
               LaTeX in a text span.

INSERT-ONLY SAFETY
------------------
wrap_math_text only ever *inserts* `$` characters:
    wrap_math_text(t).replace("$", "") == t.replace("$", "")
so it can never corrupt prose — the worst regex mistake is a cosmetically
over/under-wrapped span, never a changed Korean character. wrap_math_page
re-checks this invariant per block and skips on any drift. Both are idempotent:
text already inside $...$ / \\(..\\) / \\[..\\] is left untouched.

The regex is deliberately CONSERVATIVE: it wraps a run only when it carries a
strong LaTeX signal (a backslash command or a sub/superscript). Bare lone
letters and bare numbers — which a regex can't tell from prose — are left alone.

  wrap_math.py --page <id> [--dry-run]
  wrap_math.py --all       [--dry-run]     # whole research DB (slow)
"""
import re

# Already-delimited math — preserve these regions verbatim (idempotency). Mirrors
# save_qa_callout._MATH so we never touch or double-wrap existing equations.
_DELIMITED = re.compile(
    r"\$\$.+?\$\$"
    r"|\\\[.+?\\\]"
    r"|\$(?![\s$])[^$\n]+?(?<![\s$])\$"
    r"|\\\(.+?\\\)",
    re.DOTALL)

# Brace group with up to THREE levels of nesting, so a subscript whose content
# itself has nested braces — `_{t^{\prime}}`, `_{x_{t_{i}}}` — is ONE token.
# Without the nested alternatives the inner `{` truncates the token and `$` lands
# mid-expression. (Insert-only keeps even a deeper miss safe — just cosmetic.)
_BRACE = r"\{(?:[^{}\n]|\{(?:[^{}\n]|\{(?:[^{}\n]|\{[^{}\n]*\})*\})*\})*\}"

# A maximal run of bare-math tokens on a single line (no whitespace, no newline).
# Ordered longest-token-first so multi-char tokens win over the single-char fallback.
_RUN = re.compile(
    r"(?:"
    r"\\[A-Za-z]+\*?"            # \command  (\mathbf, \in, \times, \ldots)
    r"|\\[{}\[\]|,;:]"           # escaped delimiter  \{ \} \[ \] ...
    r"|[_^]" + _BRACE +          # sub/superscript {group} (nesting-aware)
    r"|[_^]\\[A-Za-z]+"         # sub/superscript \command
    r"|[_^][A-Za-z0-9]"        # sub/superscript single char
    r"|" + _BRACE +            # {group} (nesting-aware)
    r"|\d+(?:\.\d+)?"         # number (decimal allowed inside a run)
    r"|[A-Za-z]"             # single letter
    r"|[=<>+\-*/|,;:()\[\]]"  # operator / bracket that binds tokens
    r")+")

# A run is "real math" only with a strong signal: a backslash command or a
# sub/superscript. Lone letters / bare numbers stay untouched.
_STRONG = re.compile(r"\\[A-Za-z]|[_^]")

# Loose edge chars trimmed OUT of a $...$ so we emit `($x$)` not `$(x$)`. Never
# strip [ ] { } (structural brackets of the expression) or the sign of a number.
_EDGE_L = "(<>=+*/|,;:& \t"
_EDGE_R = ")<>=+*/|,;:.& \t"


def _wrap_run(m: "re.Match") -> str:
    run = m.group(0)
    if not _STRONG.search(run):
        return run
    i, j = 0, len(run)
    while i < j and run[i] in _EDGE_L:
        i += 1
    while j > i and run[j - 1] in _EDGE_R:
        j -= 1
    if i >= j or not _STRONG.search(run[i:j]):
        return run
    return run[:i] + "$" + run[i:j] + "$" + run[j:]


def _wrap_segment(seg: str) -> str:
    return _RUN.sub(_wrap_run, seg)


def wrap_math_text(text: str) -> str:
    """Wrap bare inline LaTeX runs in $...$. INSERT-ONLY and idempotent (see
    module docstring). Returns text unchanged if it has no LaTeX signal."""
    if "\\" not in text and "_" not in text and "^" not in text:
        return text                                  # fast path: no LaTeX possible
    out, pos = [], 0
    for m in _DELIMITED.finditer(text):
        out.append(_wrap_segment(text[pos:m.start()]))
        out.append(m.group(0))                       # keep existing math verbatim
        pos = m.end()
    out.append(_wrap_segment(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Repair: sweep a built page. Everything below lazy-imports project modules to
# avoid an import cycle (save_qa_callout imports wrap_math_text at module load).
# ---------------------------------------------------------------------------

# Bare LaTeX left in a TEXT span (equation spans are fine — their plain_text IS
# the expression, which would false-match a naive scan).
_LATEX_DETECT = re.compile(r"\\[A-Za-z]{2,}|[_^]\{|\\[{}]")

# Q&A callouts (icon/color) and toggles are managed elsewhere; paper math is in
# paragraphs/headings/quotes/lists, and patching just rich_text is safe there.
_TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
               "bulleted_list_item", "numbered_list_item")


def _text_span_latex(b: dict) -> bool:
    t = b["type"]
    spans = (b.get(t) or {}).get("rich_text", [])
    text_only = "".join(s.get("plain_text", "") for s in spans
                        if s.get("type") != "equation")
    return bool(_LATEX_DETECT.search(text_only))


def _unsafe_annotations(b: dict) -> bool:
    """True if any span carries formatting the reconstruct round-trip can't
    preserve — a link (href), italic/underline/strikethrough/code, or a non-
    default color. Bold IS preserved (re-emitted as **), so it doesn't count.
    Such blocks are skipped rather than have their formatting silently dropped."""
    t = b["type"]
    for s in (b.get(t) or {}).get("rich_text", []):
        if s.get("href"):
            return True
        a = s.get("annotations") or {}
        if a.get("italic") or a.get("underline") or a.get("strikethrough") or a.get("code"):
            return True
        if a.get("color", "default") != "default":
            return True
    return False


def _reconstruct(b: dict) -> str:
    """Source text for the block: existing equation spans re-emitted as $expr$ and
    bold spans as **...** so _inline_rich_text reproduces both on the round-trip.
    (Blocks with formatting that CAN'T round-trip are skipped upstream — see
    _unsafe_annotations.)"""
    t = b["type"]
    parts = []
    for s in (b.get(t) or {}).get("rich_text", []):
        if s.get("type") == "equation":
            expr = (s.get("equation") or {}).get("expression", "").strip()
            parts.append(f"${expr}$" if expr else "")
        else:
            txt = s.get("plain_text", "")
            if txt and (s.get("annotations") or {}).get("bold"):
                txt = f"**{txt}**"
            parts.append(txt)
    return "".join(parts)


def wrap_math_page(page_id: str, apply: bool = False) -> dict:
    import time
    import verify_sections as vs                      # lazy: break import cycle
    from translate_fulltext import notion
    from save_qa_callout import _inline_rich_text

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "scanned": len(blocks), "edited": 0}
    for b in blocks:
        t = b["type"]
        if t not in _TEXT_TYPES:
            continue
        if not _text_span_latex(b):                   # only act on real bare LaTeX
            continue
        if _unsafe_annotations(b):                    # don't drop formatting we
            continue                                  # can't round-trip (links/italic/…)
        src = _reconstruct(b)
        wrapped = wrap_math_text(src)
        if wrapped == src:
            continue                                  # nothing the regex can wrap
        if wrapped.replace("$", "") != src.replace("$", ""):
            continue                                  # insert-only guard: never corrupt
        rt = _inline_rich_text(wrapped)
        if len(rt) > 100:                             # Notion per-block rich_text cap
            continue
        if not any(s.get("type") == "equation" for s in rt):
            continue                                  # produced no equation -> skip
        rep["edited"] += 1
        if apply:
            notion("PATCH", f"/blocks/{b['id']}", {t: {"rich_text": rt}})
            time.sleep(0.35)
    return rep


def main() -> int:
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", help="wrap a single page id")
    g.add_argument("--all", action="store_true", help="sweep the whole research DB (slow)")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    a = ap.parse_args()
    apply = not a.dry_run
    if a.page:
        pages = [a.page]
    else:
        from auto_fix_qa import query_paper_pages
        pages = query_paper_pages()
    total = 0
    for pid in pages:
        rep = wrap_math_page(pid, apply=apply)
        total += rep["edited"]
        if rep["edited"]:
            print(f"  {pid}: math-wrapped {rep['edited']} block(s)"
                  f"{' (dry-run)' if not apply else ''}")
    print(f"wrapped {total} block(s) across {len(pages)} page(s)"
          f"{' (dry-run)' if not apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
