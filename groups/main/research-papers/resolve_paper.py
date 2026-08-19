#!/usr/bin/env python3
"""Resolve which paper's Notion page a Q&A belongs to — from the message itself.

The recurring bug (a Q&A filed under the wrong paper) is fundamentally a
*paper-identification* failure: the agent reused a stale page ID from an earlier
task instead of working out which paper the current question is actually about.
`save_qa_callout.py --expect-title` catches a page/title contradiction, but it
can't tell the agent which paper to use. This script does.

It anchors on concrete evidence in the message, in priority order:

  1. arxiv id / Notion page URL in the text      -> exact page          (CONFIDENT)
  2. distinctive paper-title keywords             -> title match, clear  (CONFIDENT)
     winner only
  3. pasted body excerpt vs candidate page bodies -> substring match     (CONFIDENT)
     (handles a pasted 번역본 with no title/link: the passage exists
      verbatim in exactly one page body)
  4. nothing conclusive                           -> ASK_USER

It NEVER guesses when the evidence is weak — for a Q&A save, asking the user
"which paper?" is strictly better than silently writing to the wrong one. That
weak-evidence case is also the original-bug case (a bare follow-up like "그럼
online이야?" with no paper signal): there is no honest answer except to ask.

Output (stdout), one of:
  CONFIDENT <page_id>\t<title>\t<how>      # how = arxiv|url|title|body
  ASK_USER                                  # followed by "  - <title>" candidate lines
Body-grep only runs over candidates pre-narrowed by title-token overlap (the DB
has ~500 papers; fetching every body per question is not viable), so a pure
paste with zero token overlap with any title falls through to ASK_USER.

Usage:
  python3 resolve_paper.py --text "<full user message, including any pasted text>"
  python3 resolve_paper.py --text-file /tmp/msg.txt
Env: NOTION_TOKEN, NOTION_RESEARCH_DB
"""
from __future__ import annotations
import argparse, os, re, sys

import auto_save_qa as aq  # reuse DB load, keyword + IDF machinery, api helpers

# A body excerpt window this long matching verbatim is not a coincidence —
# Korean prose has far more than 2^(48*entropy) distinct 48-grams.
WINDOW = 48
STEP = 32
# Cap candidate bodies we fetch when disambiguating (bounded API cost).
MAX_BODY_CANDIDATES = 8


def extract_arxiv_ids(text: str) -> list[str]:
    ids = set(re.findall(r"\b(\d{4}\.\d{4,5})\b", text))
    ids |= set(re.findall(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", text, re.I))
    return list(ids)


def extract_page_ids(text: str) -> list[str]:
    """32-hex (notion.so/...) or dashed-uuid page references."""
    out = set(re.findall(r"\b([0-9a-fA-F]{32})\b", text))
    out |= {m.replace("-", "") for m in
            re.findall(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", text)}
    return list(out)


def page_title(page_id: str) -> str | None:
    try:
        d = aq.api_get(f"/pages/{page_id}")
    except SystemExit:
        return None
    except Exception:
        return None
    for v in d.get("properties", {}).values():
        if v.get("type") == "title":
            return "".join(r["plain_text"] for r in v.get("title", []))
    return None


def query_by_paper_url(arxiv_id: str) -> list[dict]:
    db = os.environ["NOTION_RESEARCH_DB"]
    body = {"filter": {"property": "Paper URL", "url": {"contains": arxiv_id}}}
    try:
        d = aq.api_post(f"/databases/{db}/query", body)
    except Exception:
        return []
    out = []
    for p in d.get("results", []):
        title = ""
        for v in p["properties"].values():
            if v.get("type") == "title":
                title = "".join(r["plain_text"] for r in v["title"])
                break
        out.append({"id": p["id"], "title": title})
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def fetch_page_plaintext(page_id: str) -> str:
    """Concatenate top-level block text of a page (translations are top-level
    paragraphs/headings, so this is enough and cheap)."""
    cur, parts = None, []
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cur:
            path += f"&start_cursor={cur}"
        try:
            d = aq.api_get(path)
        except Exception:
            break
        for b in d.get("results", []):
            parts.append(aq._block_text(b))
        if d.get("has_more"):
            cur = d["next_cursor"]
        else:
            break
    return _norm(" ".join(parts))


def body_windows(text: str) -> list[str]:
    n = _norm(text)
    return [n[i:i + WINDOW] for i in range(0, max(1, len(n) - WINDOW + 1), STEP)
            if len(n[i:i + WINDOW]) >= WINDOW]


def resolve(text: str) -> tuple[str, list[dict]]:
    """Return ("CONFIDENT", [paper, how]) or ("ASK_USER", candidates)."""
    # ---- 1. arxiv id / page URL ----------------------------------------
    for aid in extract_arxiv_ids(text):
        hits = query_by_paper_url(aid)
        if len(hits) == 1:
            return "CONFIDENT", [{"how": "arxiv", **hits[0]}]
        if len(hits) > 1:
            return "ASK_USER", hits
    for pid in extract_page_ids(text):
        t = page_title(pid)
        if t:
            return "CONFIDENT", [{"how": "url", "id": pid, "title": t}]

    papers = aq.load_paper_pages()  # also populates aq._KW_DF

    # ---- 2. distinctive title-keyword match, clear winner only ----------
    scored = []
    for p in papers:
        n = aq._distinct_kw(text, p["keywords"])
        if n < 2:
            continue
        w = aq._weighted_kw(text, p["keywords"])
        scored.append((w, n, p))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    if scored:
        top_w, top_n, top_p = scored[0]
        second_w = scored[1][0] if len(scored) > 1 else 0.0
        # Clear winner: a distinctive hit (rare kw) AND no close rival.
        if top_w >= 0.5 and (second_w == 0.0 or top_w >= 2 * second_w):
            return "CONFIDENT", [{"how": "title", **top_p}]

    # ---- 3. pasted-body substring match over narrowed candidates --------
    cands: list[dict] = []
    wins = body_windows(text)
    if wins:
        cands = [p for p in papers if aq._distinct_kw(text, p["keywords"]) >= 1]
        cands.sort(key=lambda p: -aq._weighted_kw(text, p["keywords"]))
        cands = cands[:MAX_BODY_CANDIDATES]
        best = None
        for p in cands:
            body = fetch_page_plaintext(p["id"])
            if not body:
                continue
            hits = sum(1 for w in wins if w in body)
            if hits and (best is None or hits > best[0]):
                best = (hits, p)
        # >=2 matched 48-char windows == a real contiguous passage.
        if best and best[0] >= 2:
            return "CONFIDENT", [{"how": "body", **best[1]}]

    # ---- 4. give up honestly -------------------------------------------
    top = [p for _, _, p in scored[:5]] or cands[:5]
    return "ASK_USER", top


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="The full user message (include any pasted excerpt)")
    g.add_argument("--text-file", help="Path to a file with the message text")
    args = ap.parse_args()
    text = args.text if args.text else open(args.text_file, encoding="utf-8").read()

    verdict, items = resolve(text)
    if verdict == "CONFIDENT":
        p = items[0]
        print(f"CONFIDENT\t{p['id']}\t{p['title']}\t{p['how']}")
    else:
        print("ASK_USER")
        for p in items:
            print(f"  - {p.get('title', '')}  [{p['id']}]")
        sys.exit(2)  # non-zero so the agent can't mistake it for a resolved page


if __name__ == "__main__":
    main()
