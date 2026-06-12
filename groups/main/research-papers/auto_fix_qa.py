#!/usr/bin/env python3
"""Self-healer for paper Q&A callouts on Notion paper-DB pages.

Recurring problem: the agent keeps hand-rolling `PATCH /blocks/{paragraph}/children`
for Q&A saves instead of calling `save_qa_callout.py`, which leaves callouts
nested under random paragraphs and/or in the legacy default-color layout.
Four rounds of increasingly strict CLAUDE.md rules have not broken this habit,
so this script runs out-of-band (systemd timer) to self-heal the state.

For every paper page in $NOTION_RESEARCH_DB:
  1. Walk all blocks recursively to find callouts.
  2. A callout is "broken" if ANY of:
       - parent is not the page (it's nested under a paragraph/heading/etc.)
       - color is not gray_background
       - rich_text is non-empty (legacy question-in-callout format)
       - first child is not a toggle
  3. Extract question + answer children, rebuild as the toggle layout
     (gray 💡 callout → toggle(question) → answer blocks), place at the
     correct top-level position, delete the broken original.
  4. Section placement: look for a "<num>.<num>" prefix in the question;
     otherwise use the paper-page children order and put it after the
     section heading that most literally contains a question keyword.
     Fall back: append at end of page.

Safe to run repeatedly — well-formed callouts are skipped.

Env: NOTION_TOKEN, NOTION_RESEARCH_DB
Usage: python3 auto_fix_qa.py [--page PAGE_ID] [--dry-run]
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error

API = "https://api.notion.com/v1"
# Notion occasionally stops responding mid-request (observed: 502s followed by
# a TCP-ESTABLISHED connection that hangs indefinitely on read). Without an
# explicit timeout, systemd runs of this healer can block the whole qa-heal
# service — including the downstream auto_save_qa.py ExecStart — for hours,
# causing Q&A callouts to silently not get saved. 30s per request is plenty
# for any well-behaved Notion call.
HTTP_TIMEOUT = 30


def headers() -> dict:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        sys.exit("NOTION_TOKEN not set")
    return {
        "Authorization": f"Bearer {tok}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def api_get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers=headers())
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


def api_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path, method="POST",
        data=json.dumps(body).encode(), headers=headers(),
    )
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


def api_patch(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path, method="PATCH",
        data=json.dumps(body).encode(), headers=headers(),
    )
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


def api_delete(block_id: str) -> None:
    req = urllib.request.Request(
        f"{API}/blocks/{block_id}", method="DELETE", headers=headers()
    )
    urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read()


def fetch_children(pid: str) -> list[dict]:
    cur, out = None, []
    while True:
        path = f"/blocks/{pid}/children?page_size=100"
        if cur: path += f"&start_cursor={cur}"
        d = api_get(path)
        out.extend(d["results"])
        if d.get("has_more"): cur = d["next_cursor"]
        else: break
    return out


def block_text(b: dict) -> str:
    t = b["type"]
    rts = b.get(t, {}).get("rich_text", [])
    return "".join(r["plain_text"] for r in rts)


HEADING_LEVEL = {"heading_1": 1, "heading_2": 2, "heading_3": 3}


def strip_block(b: dict) -> dict:
    """Clone a block tree (sans ids/timestamps) for re-creation."""
    t = b["type"]
    src = b[t]
    clean: dict = {"object": "block", "type": t, t: {}}
    if "rich_text" in src:
        clean[t]["rich_text"] = [
            {"type": "text", "text": {"content": r["plain_text"]},
             **({"annotations": r["annotations"]} if r.get("annotations") else {})}
            for r in src["rich_text"]
        ]
    if t == "callout":
        clean[t]["icon"] = src.get("icon", {"type": "emoji", "emoji": "💡"})
        clean[t]["color"] = src.get("color", "default")
    elif t == "code":
        clean[t]["language"] = src.get("language") or "plain text"
    elif t == "image":
        clean[t].update({k: v for k, v in src.items() if k in ("external", "file", "caption")})
    return clean


def clone_with_children(b: dict) -> dict:
    c = strip_block(b)
    if b.get("has_children"):
        c[b["type"]]["children"] = [clone_with_children(k) for k in fetch_children(b["id"])]
    return c


def is_well_formed_qa_callout(b: dict, is_top_level: bool) -> bool:
    if b["type"] != "callout":
        return False
    co = b["callout"]
    if not is_top_level:
        return False
    if co.get("color") != "gray_background":
        return False
    if block_text(b):
        return False
    # Must have exactly a toggle as the single child
    if not b.get("has_children"):
        return False
    kids = fetch_children(b["id"])
    if len(kids) < 1 or kids[0]["type"] != "toggle":
        return False
    return True


def find_qa_callouts(page_id: str) -> list[tuple[dict, str, str | None]]:
    """Return list of (callout_block, parent_id, top_level_ancestor_id).

    top_level_ancestor_id is the id of the top-level block on the page that
    contains this callout (or the callout itself if it's already top-level).
    When a callout is nested (e.g. under a paragraph — the classic recurring
    bug), this lets us place the replacement right after that top-level
    ancestor, so the Q&A stays in the section the agent was aiming at
    instead of drifting to the page end when the text heuristic fails.
    """
    out: list[tuple[dict, str, str | None]] = []

    def walk(pid: str, top_ancestor: str | None):
        for b in fetch_children(pid):
            # the outermost block under the page IS its own top-level ancestor
            self_top = top_ancestor if top_ancestor is not None else b["id"]
            if b["type"] == "callout":
                text = block_text(b)
                first_kid_type = None
                if b.get("has_children"):
                    first = fetch_children(b["id"])
                    if first:
                        first_kid_type = first[0]["type"]
                if (text.strip().startswith(("Q:", "Q ", "질문"))
                        or (first_kid_type == "toggle")):
                    out.append((b, pid, self_top))
            if b.get("has_children") and b["type"] != "callout":
                walk(b["id"], self_top)
    walk(page_id, None)
    return out


def extract_question_and_answer(callout: dict) -> tuple[str, list[dict]]:
    """Return (question_text, answer_children_clones) regardless of layout."""
    co_text = block_text(callout)
    kids = fetch_children(callout["id"]) if callout.get("has_children") else []

    if kids and kids[0]["type"] == "toggle":
        toggle = kids[0]
        question = block_text(toggle).strip()
        ans_kids = fetch_children(toggle["id"]) if toggle.get("has_children") else []
        return question, [clone_with_children(k) for k in ans_kids]

    # Legacy: question in callout rich_text, answers are direct children
    question = co_text.strip()
    return question, [clone_with_children(k) for k in kids]


def build_toggle_callout(question: str, answer_children: list[dict]) -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "💡"},
            "color": "gray_background",
            "rich_text": [],
            "children": [{
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": question[:2000]}}],
                    "children": answer_children,
                },
            }],
        },
    }


SECTION_RE = re.compile(r"(?<![\w.])(\d{1,2}\.\d{1,2}(?:\.\d+)?)\b")
FIGURE_RE = re.compile(r"(?:Figure|Fig\.?|Table|Eq\.?|Equation|Algorithm|Alg\.?)\s*(\d+)", re.IGNORECASE)


def section_ranges(page_children: list[dict]) -> list[tuple[int, int, int, str]]:
    """Return [(heading_idx, end_idx_exclusive, level, heading_text), ...]."""
    out = []
    for i, b in enumerate(page_children):
        lvl = HEADING_LEVEL.get(b["type"])
        if not lvl:
            continue
        end = len(page_children)
        for j in range(i + 1, len(page_children)):
            lvl2 = HEADING_LEVEL.get(page_children[j]["type"])
            if lvl2 and lvl2 <= lvl:
                end = j
                break
        out.append((i, end, lvl, block_text(b)))
    return out


def guess_section_after(page_children: list[dict], question: str) -> str | None:
    """Pick the `after` block (last top-level block of the inferred section).
    Returns None → append at end of page.

    Strategies tried in order:
      1. Explicit "N.M" section number in the question text → match heading prefix.
      2. "Figure K" / "Table K" / "Eq K" reference → find the section whose body
         text mentions it (accounts for a question citing an artifact from a
         section different from the one named in its heading).
      3. Token overlap between the question and section heading text.
      4. Token overlap between the question and section body text.
    """
    ranges = section_ranges(page_children)

    # 1) explicit "N.M"
    candidates = SECTION_RE.findall(question)
    for num in candidates:
        for hi, _, _, htxt in ranges:
            if htxt.strip().startswith(num):
                return last_block_id_of_section(page_children, hi)

    # 2) Figure/Table/Eq reference → deepest section whose body mentions it.
    # Parent sections (heading_1) trivially contain their subsections' artifacts,
    # so we prefer the deepest heading_3 > heading_2 > heading_1 on ties.
    fig_refs = {m.group(0).lower().replace(" ", "").replace(".", "") for m in FIGURE_RE.finditer(question)}
    if fig_refs:
        best_hi, best_score, best_level = -1, 0, 0
        for hi, end, lvl, _ in ranges:
            body = " ".join(block_text(b) for b in page_children[hi + 1:end])
            body_norm = body.lower().replace(" ", "").replace(".", "")
            score = sum(1 for r in fig_refs if r in body_norm)
            if score > best_score or (score == best_score and score > 0 and lvl > best_level):
                best_hi, best_score, best_level = hi, score, lvl
        if best_hi >= 0 and best_score >= 1:
            return last_block_id_of_section(page_children, best_hi)

    # 3) heading-token overlap (deepest wins on ties)
    q_tokens = {w.lower() for w in re.findall(r"[A-Za-z가-힣]{3,}", question)}
    best_hi, best_score, best_level = -1, 0, 0
    for hi, _, lvl, htxt in ranges:
        h_tokens = {w.lower() for w in re.findall(r"[A-Za-z가-힣]{3,}", htxt)}
        score = len(q_tokens & h_tokens)
        if score > best_score or (score == best_score and score > 0 and lvl > best_level):
            best_hi, best_score, best_level = hi, score, lvl
    if best_hi >= 0 and best_score >= 2:
        return last_block_id_of_section(page_children, best_hi)

    # 4) body-token overlap (deepest wins on ties; skip heading_1 unless nothing else matches)
    if len(q_tokens) >= 3:
        best_hi, best_score, best_level = -1, 0, 0
        for hi, end, lvl, _ in ranges:
            body_tokens = set()
            for b in page_children[hi + 1:end]:
                body_tokens.update(re.findall(r"[A-Za-z가-힣]{4,}", block_text(b).lower()))
            score = len(q_tokens & body_tokens)
            if score > best_score or (score == best_score and score > 0 and lvl > best_level):
                best_hi, best_score, best_level = hi, score, lvl
        if best_hi >= 0 and best_score >= 3:
            return last_block_id_of_section(page_children, best_hi)

    return None


def last_block_id_of_section(page_children: list[dict], heading_idx: int) -> str:
    """Last top-level block of the section whose heading is at page_children[heading_idx]."""
    start_level = HEADING_LEVEL[page_children[heading_idx]["type"]]
    end = len(page_children)
    for j in range(heading_idx + 1, len(page_children)):
        lvl = HEADING_LEVEL.get(page_children[j]["type"])
        if lvl and lvl <= start_level:
            end = j
            break
    return page_children[end - 1]["id"]


def query_paper_pages() -> list[str]:
    db = os.environ.get("NOTION_RESEARCH_DB")
    if not db:
        sys.exit("NOTION_RESEARCH_DB not set")
    out: list[str] = []
    cur = None
    # Notion's unsorted DB query drops pages when the DB is large enough to paginate;
    # passing an explicit sort forces stable ordering so every page is returned.
    # Without this, pages can be silently missing from the scan (verified empirically).
    while True:
        body: dict = {
            "page_size": 100,
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        }
        if cur: body["start_cursor"] = cur
        d = api_post(f"/databases/{db}/query", body)
        out.extend(p["id"] for p in d["results"])
        if d.get("has_more"): cur = d["next_cursor"]
        else: break
    return out


def heal_page(page_id: str, dry_run: bool = False) -> int:
    broken: list[tuple[dict, str, str | None]] = []
    for co, parent_id, top_ancestor in find_qa_callouts(page_id):
        is_top = parent_id == page_id or parent_id.replace("-", "") == page_id.replace("-", "")
        if is_well_formed_qa_callout(co, is_top):
            continue
        broken.append((co, parent_id, top_ancestor))

    if not broken:
        return 0

    page_children = fetch_children(page_id)
    fixed = 0
    for co, parent_id, top_ancestor in broken:
        q, ans = extract_question_and_answer(co)
        if not q:
            print(f"  SKIP {co['id']}: no question text extractable", file=sys.stderr)
            continue

        # Placement priority:
        #  1. Already top-level and only format-broken → preserve exact position.
        #  2. Nested under some top-level block → place right after that block.
        #     This recovers the agent's original intent: it PATCHed the callout
        #     into a paragraph that lived in some section, so the correct
        #     section is "whichever section that paragraph is in".
        #  3. Last resort: text heuristic (section number / figure ref / tokens).
        was_top_level = parent_id == page_id or parent_id.replace("-", "") == page_id.replace("-", "")
        after_id: str | None
        if was_top_level:
            idx = next((i for i, b in enumerate(page_children) if b["id"] == co["id"]), None)
            after_id = page_children[idx - 1]["id"] if (idx is not None and idx > 0) else None
            placement = f"preserve@{after_id[-12:] if after_id else 'start'}"
        elif top_ancestor:
            # Place directly after the top-level block that currently contains
            # the nested callout (usually the paragraph the agent wrongly
            # PATCHed as parent). This is the *recovered* location.
            idx = next((i for i, b in enumerate(page_children)
                        if b["id"] == top_ancestor), None)
            if idx is not None:
                after_id = page_children[idx]["id"]
                placement = f"anchor@{after_id[-12:]}"
            else:
                after_id = guess_section_after(page_children, q)
                placement = f"guess@{after_id[-12:] if after_id else '(end)'}"
        else:
            after_id = guess_section_after(page_children, q)
            placement = f"guess@{after_id[-12:] if after_id else '(end)'}"

        new_block = build_toggle_callout(q, ans)
        print(f"  heal {co['id'][-12:]} | {q[:70]} | {placement}", file=sys.stderr)
        if dry_run:
            fixed += 1
            continue
        body: dict = {"children": [new_block]}
        if after_id:
            body["after"] = after_id
        try:
            res = api_patch(f"/blocks/{page_id}/children", body)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors='ignore')[:300]
            print(f"    ERR insert {e.code}: {err}", file=sys.stderr)
            continue
        new_id = res["results"][0]["id"]
        # verify top-level
        time.sleep(0.3)
        top_ids = {b["id"] for b in fetch_children(page_id)}
        if new_id not in top_ids:
            print(f"    FAIL: replacement {new_id} not top-level, rolling back", file=sys.stderr)
            try: api_delete(new_id)
            except Exception: pass
            continue
        # Delete original
        try:
            api_delete(co["id"])
        except urllib.error.HTTPError as e:
            print(f"    WARN delete old {co['id']}: {e.code}", file=sys.stderr)
        fixed += 1
        # Re-fetch page children once so subsequent `after` lookups stay current
        page_children = fetch_children(page_id)
        time.sleep(0.3)
    return fixed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="Fix only this page (default: scan whole Paper DB)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pages = [args.page] if args.page else query_paper_pages()
    total_fixed = 0
    for p in pages:
        n = heal_page(p, dry_run=args.dry_run)
        if n:
            print(f"page {p}: fixed {n} callout(s)")
            total_fixed += n
    if total_fixed == 0:
        print("all callouts already well-formed")
    else:
        print(f"total: {total_fixed} callout(s) healed across {len(pages)} page(s)")


if __name__ == "__main__":
    main()
