#!/usr/bin/env python3
r"""Repair inline citation numbers that translation renumbered, by re-deriving each
one from the SOURCE the page was actually translated from.

The defect
----------
NotebookLM does not preserve a paper's citation markers: it renumbers them
**sequentially per section**, restarting at 1. So a body sentence that cites
reference [5] in the source ships as "[1]" — a number that points at a completely
different paper, and looks perfectly plausible. `verify_citations.py` DETECTS this
(RENUMBERED) but has never been able to repair it, because repairing needs the
source's own citation sequence, not a heuristic.

Why the source has to be the RIGHT one
--------------------------------------
Getting this wrong silently produces confident nonsense. A real page carried the
publisher (journal) version of a paper — numeric citations, extra experiment
sections, related work moved to §5 — while its Paper URL pointed at arxiv, whose
HTML is author-year and structurally different. Aligning against arxiv mapped a
sentence to the wrong reference entirely. So `--source` is explicit, and the
per-section counts are checked before anything is rewritten.

How the mapping is derived
--------------------------
Translation preserves two things that make this tractable: section numbering
(`3.3`, `4.8.1` survive into the translated headings) and citation ORDER within a
section. So for each section, the k-th citation occurrence in the translation is
the k-th citation occurrence of that section in the source:

    source  §3.3 : [5] ... [23] [23] [24]
    page    §3.3 : [1] ... [ 2] [ 2] [ 3]      ->  1→5, 2→23, 3→24

Verified against a real page by checking each rewritten marker against the source
sentence: where the body names the work it cites, the new number lands on exactly
that entry in the reference list. One section mapped `1→4 2→32 3→58 4→59`; another,
whose numbers were ALREADY right, mapped to itself (`4→4 6→6 8→8 …`), so the rule
is safe on the parts translation happened to get correct.

A section whose counts DON'T match is left completely alone and reported. That is
deliberate: a missing repair is recoverable, a confidently wrong citation is not.
(An earlier attempt used a global order-preserving alignment across the whole
document instead; it drifted across section boundaries and mapped a citation onto a
reference from an unrelated later section — exactly the failure this must not
produce, because the result still looks perfectly plausible.)

  remap_citations.py --page <id> --source <pdf> [--dry-run] [--report]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# `[12]`, `[1,4]`, `[8–10]`. Requires a digit-only body so math like `[0,1)` or a
# matrix `[i]` index can never be mistaken for a citation.
CITE = re.compile(r"\[(\d+(?:\s*[,–—-]\s*\d+)*)\]")
# A numbered heading, in the source PDF or in the translated page:
#   "3.4.1. Derivation of the method" / "4 Experiments and Evaluation"
SRC_HEAD = re.compile(r"^[ \t]*(\d+(?:\.\d+)*)\.?[ \t]+([A-Z][^\n]{2,70})[ \t]*$", re.M)
TR_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


def expand(tok: str) -> list:
    """'1,4,6,8–10' -> [1,4,6,8,9,10] (ranges inclusive)."""
    out = []
    for part in re.split(r"\s*,\s*", tok):
        m = re.match(r"(\d+)\s*[–—-]\s*(\d+)$", part.strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b >= a and b - a < 60:
                out += list(range(a, b + 1))
        elif part.strip().isdigit():
            out.append(int(part.strip()))
    return out


def source_sections(pdf_path: str) -> dict:
    """{section key: [cited numbers, in order of occurrence]} from the paper PDF.

    The reference list is cut first — its own `[N]` labels are not body citations.
    """
    import fitz

    doc = fitz.open(pdf_path)
    try:
        full = "\n".join(doc[p].get_text() for p in range(doc.page_count))
    finally:
        doc.close()
    m = re.search(r"\n\s*References\s*\n", full)
    body = full[:m.start()] if m else full

    marks = [(mm.start(), mm.group(1)) for mm in SRC_HEAD.finditer(body)]
    marks.sort()

    def sec_of(pos):
        cur = None
        for p, k in marks:
            if p <= pos:
                cur = k
            else:
                break
        return cur

    seq = {}
    for mm in CITE.finditer(body):
        seq.setdefault(sec_of(mm.start()), []).extend(expand(mm.group(1)))
    return {k: v for k, v in seq.items() if k}


def _block_text(b: dict) -> str:
    t = b["type"]
    p = b.get(t)
    if not isinstance(p, dict) or "rich_text" not in p:
        return ""
    return "".join("$" + s["equation"]["expression"] + "$" if s.get("type") == "equation"
                   else s.get("plain_text", "") for s in p["rich_text"])


def page_citations(blocks: list) -> list:
    """[{block, bid, section, span, nums}] for every citation token on the page."""
    cur = None
    out = []
    for i, b in enumerate(blocks):
        text = _block_text(b)
        stripped = text.strip()
        m = TR_HEAD.match(stripped)
        if m and len(stripped) < 95:
            cur = m.group(1)
        for cm in CITE.finditer(text):
            out.append({"block": i, "bid": b["id"], "section": cur,
                        "raw": cm.group(0), "nums": expand(cm.group(1))})
    return out


def _align_with_gaps(tr: list, src: list, max_gap_ratio: float = 0.25):
    """Order-preserving alignment when the translation DROPPED some citations.

    Returns the source number for each page slot, or None when the section is too
    ambiguous to touch. Only source-side gaps are allowed: translation omitting a
    citation is common, whereas a page citation with no source counterpart means the
    section boundaries disagree and positional substitution would be guesswork.

    Anchors carry the alignment: a page slot whose number ALREADY equals a source
    number is strong evidence of where it belongs (a real section had `[16–19]`
    surviving untouched while the numbers around it were renumbered).
    """
    m, n = len(tr), len(src)
    if m > n or n == 0 or (n - m) / n > max_gap_ratio:
        return None
    NEG = float("-inf")
    dp = [[NEG] * (n + 1) for _ in range(m + 1)]
    bt = [[0] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for j in range(1, n + 1):
        dp[0][j] = 0.0          # leading source citations may be unmatched
        bt[0][j] = 2
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            take = dp[i - 1][j - 1]
            if take > NEG:
                take += 2.0 if tr[i - 1] == src[j - 1] else 0.35
            skip = dp[i][j - 1] - 0.05      # source citation absent from translation
            if take >= skip:
                dp[i][j], bt[i][j] = take, 0
            else:
                dp[i][j], bt[i][j] = skip, 2
    if dp[m][n] == NEG:
        return None
    out = [None] * m
    i, j = m, n
    while i > 0 and j > 0:
        if bt[i][j] == 0:
            out[i - 1] = src[j - 1]; i -= 1; j -= 1
        else:
            j -= 1
    return None if any(v is None for v in out) else out


def build_map(cites: list, src_seq: dict):
    """(per-section ordered source numbers, per-section status).

    The mapping is BY OCCURRENCE, not by number: the k-th citation on the page in a
    section becomes the k-th citation of that section in the source. Keying it on the
    page's own numbers instead would be wrong, because translation does not corrupt
    them uniformly — a real page's §1 kept 22 of 26 positions correct while reusing a
    stale number in the other 4 (`…[8],[9],[10]` where the source has `[20],[21],[22]`).
    A number-keyed map sees that as "the number 8 means two different things" and
    refuses the whole section — rejecting exactly the sections that need fixing. The
    page's numbers are unreliable labels; their ORDER is the reliable signal.

    A section is mapped only when both sides hold the same number of citations, which
    is what makes position-for-position substitution safe.
    """
    maps, status = {}, {}
    sections = []
    for c in cites:
        if c["section"] not in sections:
            sections.append(c["section"])
    for sec in sections:
        tr = [n for c in cites if c["section"] == sec for n in c["nums"]]
        src = src_seq.get(sec, [])
        if not sec or not src:
            status[sec] = (f"SKIP no citations in source section (page has {len(tr)} — "
                           f"likely fabricated by translation)")
            continue
        if len(tr) != len(src):
            aligned = _align_with_gaps(tr, src)
            if aligned is None:
                status[sec] = f"SKIP count mismatch: page {len(tr)} vs source {len(src)}"
                continue
            same = sum(1 for a, b in zip(tr, aligned) if a == b)
            maps[sec] = aligned
            status[sec] = (f"OK (gap-aligned) {len(aligned)-same}/{len(aligned)} change "
                           f"({same} already correct, {len(src)-len(tr)} source citation(s) "
                           f"absent from the translation)")
            continue
        maps[sec] = list(src)
        same = sum(1 for a, b in zip(tr, src) if a == b)
        status[sec] = (f"OK {len(src)-same}/{len(src)} change "
                       f"({same} already correct)")
    return maps, status


def remap(page_id: str, pdf_path: str, apply: bool = False, report: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)
    cites = page_citations(blocks)
    src_seq = source_sections(pdf_path)
    maps, status = build_map(cites, src_seq)

    rep = {"page": page_id, "tokens": len(cites),
           "slots": sum(len(c["nums"]) for c in cites),
           "sections_mapped": len(maps), "sections_skipped": len(status) - len(maps),
           "rewritten_blocks": 0, "changed_slots": 0, "status": status}
    if report:
        return rep

    # rewrite block by block, consuming each section's source numbers IN ORDER.
    # page_citations() walks blocks (and each block's text) in document order, so the
    # cursor below stays in step with the sequence build_map validated.
    cursor = {sec: 0 for sec in maps}
    by_block = {}
    for c in cites:
        if c["section"] in maps:
            by_block.setdefault(c["block"], []).append(c)
    for bi, items in sorted(by_block.items()):
        b = blocks[bi]
        t = b["type"]
        spans = (b.get(t) or {}).get("rich_text", [])
        sec = items[0]["section"]
        seq = maps[sec]
        changed = 0
        new_spans = []
        for s in spans:
            if s.get("type") == "equation":
                new_spans.append({"type": "equation",
                                  "equation": {"expression": (s.get("equation") or {}).get("expression", "")},
                                  **({"annotations": s["annotations"]} if s.get("annotations") else {})})
                continue
            txt = (s.get("text") or {}).get("content", s.get("plain_text", ""))

            def sub(cm):
                nonlocal changed
                nums = expand(cm.group(1))
                i = cursor[sec]
                if not nums or i + len(nums) > len(seq):
                    cursor[sec] = i + len(nums)
                    return cm.group(0)
                new = seq[i:i + len(nums)]
                cursor[sec] = i + len(nums)
                changed += sum(1 for a, bb in zip(nums, new) if a != bb)
                return "[" + ", ".join(str(x) for x in new) + "]"

            out = {"content": CITE.sub(sub, txt)}
            if (s.get("text") or {}).get("link"):
                out["link"] = s["text"]["link"]
            new_spans.append({"type": "text", "text": out,
                              **({"annotations": s["annotations"]} if s.get("annotations") else {})})
        if not changed:
            continue
        rep["rewritten_blocks"] += 1
        rep["changed_slots"] += changed
        if apply:
            notion("PATCH", f"/blocks/{b['id']}", {t: {"rich_text": new_spans}})
            time.sleep(0.25)
    return rep


def main() -> int:
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--source", required=True, help="the paper PDF the page was translated from")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true", help="only print the per-section plan")
    a = ap.parse_args()
    rep = remap(a.page, a.source, apply=not (a.dry_run or a.report), report=a.report)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
