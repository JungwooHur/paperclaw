#!/usr/bin/env python3
"""Audit (and conservatively repair) inline citation numbers on a translated
Notion paper page against the paper's REAL arxiv bibliography.

Root cause this guards against: NotebookLM translation does not preserve a
paper's citation markers. It renumbers them sequentially per-section, and for
author-year papers it *fabricates* numeric [N] markers that do not exist in the
source at all. Figure/Eq. references survive because the translation prompt
explicitly tells NotebookLM to keep them; citations had no such rule.

What this does:
  1. Fetch the real arxiv HTML (native first, ar5iv fallback) and classify the
     paper's citation style from its bibliography:
        - numeric     -> bib items tagged [1]..[N]; inline cites are numbers.
        - author-year -> bib items tagged "Author et al. [2023]"; the paper has
                         NO numeric inline citations, so every [N] on the page
                         is fabricated.
  2. Pull the Notion page blocks and collect every inline [..] citation token.
  3. Report anomalies:
        - author-year paper with numeric [N] tokens  -> FABRICATED.
        - numeric paper with a token number > real N  -> OUT-OF-RANGE.
        - numeric paper whose running cite numbers reset to 1 repeatedly while
          never approaching N -> RENUMBERED (per-section).
  4. --apply (author-year papers only): strip the fabricated [N] markers, the
     same policy en-mode reformatting already uses (a wrong number is worse than
     no number). Numeric-paper anomalies are reported but NOT auto-rewritten:
     remapping a renumbered numeric paper needs the source context per block
     (needs a hand-verified per-block remap, built per paper) and is unsafe to
     guess.

Exit code: 0 = clean, 1 = anomalies found (so the agent/healer can react).

Usage:
  verify_citations.py --page <page_id> [--arxiv <id>] [--apply]
  (arxiv id is auto-read from the page's "Paper URL" property if omitted)
"""
import argparse
import re
import sys
import urllib.request

import auto_save_qa as aq

# Native-latest first (matches the figures the reader sees), then ar5iv and
# explicit v1 as fallbacks — native-latest sometimes renders WITHOUT the
# bibliography (observed: latest render with no refs at all; v1 carried the
# full list). For citation auditing we need an HTML that actually carries the ref list.
ARXIV_CANDIDATES = ("https://arxiv.org/html/{id}",
                    "https://ar5iv.labs.arxiv.org/html/{id}",
                    "https://arxiv.org/html/{id}v1")
_HAS_BIB = re.compile(r'ltx_tag_bibitem">|ltx_bibitem|ltx_biblist')
# A citation token: [12]  /  [1, 2]  /  [3-5]  (numbers, commas, dashes only).
CITE_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")
# Removal also eats ONE optional leading space, so a Korean particle glued to
# the closing bracket reattaches to its noun: "models [26, 27]을" -> "models을"
# (not "models 을"); "습니다 [1]." -> "습니다.".
STRIP_RE = re.compile(r"[ \t]?\[(\d+(?:\s*[-,]\s*\d+)*)\]")


def fetch_arxiv_html(arxiv_id):
    """Return validated LaTeXML HTML that contains a bibliography. Falls back
    through candidates and prefers the first that actually carries a ref list;
    if none does, returns the first otherwise-valid HTML (classifier handles it)."""
    fallback = None
    for tmpl in ARXIV_CANDIDATES:
        url = tmpl.format(id=arxiv_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=aq.HTTP_TIMEOUT).read().decode(
                "utf-8", "ignore")
        except Exception:
            continue
        if len(html) > 50000 and "ltx_document" in html and "Fatal error" not in html:
            if _HAS_BIB.search(html):
                return html
            fallback = fallback or html
    return fallback


def classify_bibliography(html):
    """('numeric', max_n) or ('author-year', None) or ('unknown', None)."""
    nums = [int(n) for n in re.findall(r'ltx_tag_bibitem">\s*\[(\d+)\]', html)]
    if len(nums) >= 5:
        return "numeric", max(nums)
    # Author-year bib items tag like: ltx_tag ...>Chi et al. [2023]<
    ay = re.findall(r'ltx_bibitem[^>]*>.*?ltx_tag[^>]*>\s*([A-Z][^<\[]+\[\d{4})',
                    html, re.S)
    if len(ay) >= 5:
        return "author-year", None
    return "unknown", None


def page_arxiv_id(page_id):
    p = aq.api_get(f"/pages/{page_id}")
    url = (p.get("properties", {}).get("Paper URL", {}) or {}).get("url") or ""
    m = re.search(r"(?:abs|pdf|html)/(\d{4}\.\d{4,5})", url) or \
        re.search(r"(\d{4}\.\d{4,5})", url)
    return m.group(1) if m else None


def all_blocks(page_id):
    out, cur = [], None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cur}" if cur else "")
        d = aq.api_get(f"/blocks/{page_id}/children{q}")
        out += d["results"]
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def block_text(b):
    t = b["type"]
    if not isinstance(b.get(t), dict):
        return ""
    return "".join(r.get("plain_text", "") for r in b[t].get("rich_text", []))


def first_nums(token):
    return [int(x) for x in re.findall(r"\d+", token)]


def detect_renumbering(seq):
    """seq = ordered list of first-number-of-each-cite. Count downward resets
    to a low number after climbing higher (the per-section restart signature)."""
    resets, peak = 0, 0
    for n in seq:
        if n > peak:
            peak = n
        elif peak >= 4 and n <= 2:
            resets += 1
            peak = n
    return resets


def patch_block_text(b, new_text):
    """Rewrite a block's rich_text to a single plain run carrying new_text,
    preserving the run-0 annotations/link. Only used for the author-year strip,
    where the affected runs are plain prose."""
    t = b["type"]
    runs = b[t].get("rich_text", [])
    ann = runs[0]["annotations"] if runs else None
    link = runs[0]["text"].get("link") if runs and runs[0]["type"] == "text" else None
    body = {"type": "text", "text": {"content": new_text, "link": link}}
    if ann:
        body["annotations"] = ann
    req = urllib.request.Request(
        aq.API + f"/blocks/{b['id']}", method="PATCH",
        data=__import__("json").dumps({t: {"rich_text": [body]}}).encode(),
        headers=aq.headers())
    urllib.request.urlopen(req, timeout=aq.HTTP_TIMEOUT).read()


def strip_cites(text):
    """Remove fabricated [N] citation tokens and tidy whitespace/punctuation.
    Skips bracket groups containing 0 or a negative sign (likely math intervals
    like [0, 1] or [-1, 1], never citation numbers)."""
    def repl(m):
        nums = first_nums(m.group(1))
        if any(n == 0 for n in nums) or "-0" in m.group(0):
            return m.group(0)
        return ""
    out = STRIP_RE.sub(repl, text)
    out = re.sub(r"\s+([.,;:)\]])", r"\1", out)   # space before punctuation
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    arxiv_id = args.arxiv or page_arxiv_id(args.page)
    if not arxiv_id:
        sys.exit("could not determine arxiv id (pass --arxiv); non-arxiv papers "
                 "are not auditable against a canonical bibliography")
    html = fetch_arxiv_html(arxiv_id)
    if not html:
        sys.exit(f"could not fetch valid arxiv HTML for {arxiv_id}")
    style, max_n = classify_bibliography(html)
    print(f"paper {arxiv_id}: citation style = {style}"
          + (f" (real refs 1..{max_n})" if style == "numeric" else ""))
    if style == "unknown":
        sys.exit("bibliography style unrecognized; aborting (no safe audit)")

    blocks = all_blocks(args.page)
    seq, total, out_of_range, fabricated_blocks = [], 0, [], []
    for b in blocks:
        text = block_text(b)
        toks = CITE_RE.findall(text)
        if not toks:
            continue
        block_fab = False
        for tk in toks:
            nums = first_nums(tk)
            total += 1
            seq.append(nums[0])
            if style == "numeric" and any(n > max_n for n in nums):
                out_of_range.append((b["id"], tk))
            if style == "author-year":
                block_fab = True
        if block_fab:
            fabricated_blocks.append(b)

    print(f"page {args.page}: {total} inline citation tokens across "
          f"{len(blocks)} blocks")

    anomalies = False
    if style == "author-year":
        if total:
            anomalies = True
            print(f"  FABRICATED: paper uses author-year citations but the page "
                  f"has {total} numeric [N] tokens — all invented by translation.")
    else:  # numeric
        resets = detect_renumbering(seq)
        distinct_max = max(seq) if seq else 0
        if out_of_range:
            anomalies = True
            print(f"  OUT-OF-RANGE: {len(out_of_range)} token(s) exceed real "
                  f"max {max_n}: " + ", ".join(f"[{t}]@{b[:8]}"
                  for b, t in out_of_range[:10]))
        if resets >= 2 and distinct_max < max_n * 0.6:
            anomalies = True
            print(f"  RENUMBERED: {resets} per-section restarts and highest used "
                  f"[{distinct_max}] << real [{max_n}] — looks resequenced. "
                  f"Needs a hand-built per-block remap; not "
                  f"auto-fixable.")
        if not anomalies:
            print(f"  OK: numbers within 1..{max_n}, no resequencing signature.")

    if anomalies and args.apply and style == "author-year":
        changed = 0
        for b in fabricated_blocks:
            t = b["type"]
            before = block_text(b)
            after = strip_cites(before)
            if after != before:
                patch_block_text(b, after)
                changed += 1
                print(f"  stripped {b['id'][:8]}: "
                      f"{len(CITE_RE.findall(before)) - len(CITE_RE.findall(after))} token(s)")
        print(f"APPLIED: stripped fabricated citations from {changed} block(s).")
    elif anomalies and args.apply:
        print("NOTE: numeric-paper anomalies are not auto-fixed; remap by hand.")
    elif anomalies:
        print("DRY-RUN: re-run with --apply to strip (author-year papers only).")

    sys.exit(1 if anomalies else 0)


if __name__ == "__main__":
    main()
