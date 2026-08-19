#!/usr/bin/env python3
r"""Put the paper's real reference list on the page and link the body to it.

Three jobs, in one pass because they are coupled — a citation cannot be linked
until the block it points at exists:

  1. Append the source's bibliography VERBATIM IN ENGLISH at the end of the page.
     A translated bibliography comes out mangled (author names pick up a Korean
     "그리고", entries fragment), which is why `strip_backmatter` removes one; the
     original English is the thing worth having, and it is exact — it comes from
     `<li id="bib.bibN">` in the LaTeXML source, no guessing anywhere.
  2. Repair the citation numbers that translation renumbered — but ONLY where the
     repair is provable (below).
  3. Turn each repaired `[N]` into a link to reference N's own block, so clicking
     it jumps there. Notion has no footnote anchor, but a rich_text span may carry
     `link.url = https://www.notion.so/<page-id>#<block-id>`, which is exactly what
     "Copy link to block" produces.

What can and cannot be repaired
-------------------------------
NotebookLM renumbers citations **sequentially per section, starting at 1, in the
source's own order**. Measured on a real page, section III read `[1,2,3,4]` where
the source cites `[10,5,32,28]` — so when the counts agree the mapping is exact
and order-preserving, and re-deriving it is not a heuristic.

The counts usually do NOT agree, because translation also DROPS citations: across
five recent papers the page carried 11, 13, 60, 61 and 67 markers against 192, 77,
43, 139 and 185 in the source. A section whose counts differ cannot be aligned —
the k-th marker on the page is no longer the k-th in the source — and guessing
there produces a confidently wrong reference, which is worse than a wrong number
because the link makes it look verified.

So a section is rewritten only when its marker count equals the source's, and
every other section is left EXACTLY as it is: not renumbered, not stripped, and
not linked. An unlinked number is visibly unverified; a wrong link is not.

  link_references.py --page <id> [--arxiv <id>] [--dry-run] [--force]

`--force` rebuilds an existing reference section instead of leaving it alone.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract_paper_figures as ef                      # noqa: E402
import verify_sections as vs                            # noqa: E402
from save_qa_callout import block_text, HEADING_LEVEL   # noqa: E402
from translate_fulltext import notion                   # noqa: E402
import reference_section                                # noqa: E402

REFS_HEADING = "References"

# A citation marker as it appears in the translated body: `[12]`, `[3, 4]`.
# Deliberately NOT matching `[0,1]`-style intervals (no spaces around a comma is
# ambiguous, so a group must be either a single number or comma-separated with the
# same shape the translator emits).
_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

_BIB_ITEM = re.compile(r'<li[^>]+id="bib\.bib(\d+)"[^>]*>(.*?)</li>', re.S | re.I)
_SECTION = re.compile(r'<section[^>]+id="(S\d+)"[^>]*>.*?<h2[^>]*>(.*?)</h2>', re.S | re.I)
_ANCHOR = re.compile(r'href="#bib\.bib(\d+)"')


def parse_bibliography(html: str) -> list:
    """[{num, text}] for every entry in the source's reference list, in order."""
    out = []
    for m in _BIB_ITEM.finditer(html):
        text = ef._clean(m.group(2))
        if text:
            out.append({"num": int(m.group(1)), "text": text})
    out.sort(key=lambda e: e["num"])
    return out


def source_citation_sequence(html: str) -> dict:
    """{section_key: [reference numbers cited, in reading order]}.

    Read off the source's own anchors, so this is the paper's truth rather than a
    reconstruction: each `<a href="#bib.bibN">` inside a section is one citation.
    """
    secs = [(m.start(), ef._clean(m.group(2))) for m in _SECTION.finditer(html)]
    bounds = [s[0] for s in secs] + [len(html)]
    out = {}
    for i, (pos, title) in enumerate(secs):
        key = vs.section_key(title)
        if not key:
            continue
        nums = [int(x) for x in _ANCHOR.findall(html[pos:bounds[i + 1]])]
        out.setdefault(key, []).extend(nums)
    return out


def page_citation_slots(blocks: list) -> dict:
    """{section_key: [(block, span_index, group_text) ...]} in reading order."""
    out, cur = {}, None
    for b in blocks:
        if HEADING_LEVEL.get(b["type"]):
            cur = vs.section_key(block_text(b))
            continue
        if cur is None or b["type"] not in vs.BODY_TYPES:
            continue
        for m in _CITE.finditer(block_text(b)):
            for tok in m.group(1).split(","):
                out.setdefault(cur, []).append((b["id"], int(tok.strip())))
    return out


def _find_refs_heading(blocks: list):
    """The id of a References heading this tool wrote, or None.

    Recognised by its BODY, not its title: every entry we write opens with `[N] `
    and holds no Korean. A translated back-matter section — the thing
    `strip_backmatter` exists to remove — never looks like that.
    """
    for i, b in enumerate(blocks):
        if not HEADING_LEVEL.get(b["type"]):
            continue
        if block_text(b).strip().lower() != REFS_HEADING.lower():
            continue
        body = [x for x in blocks[i + 1:] if x["type"] == "paragraph"]
        if body and reference_section.looks_like_list(body):
            return b["id"], [x["id"] for x in blocks[i + 1:]]
    return None


_LABEL = re.compile(r"^.*?\[\d{4}[a-z]?\]\s*")
_CONNECTOR = {"and", "others", "et", "al", "van", "von", "de", "der", "den", "di",
              "da", "el", "bin", "ibn", "jr", "sr", "the"}


def _is_author_list(seg: str) -> bool:
    """Is this sentence the entry's author list rather than its title?

    Every word is either capitalised or a name connector. A title fails this on
    its very first ordinary word — "High-resolution image synthesis…",
    "Gpt-4 technical report", "Attention is all you need" — while an author list
    of any length passes, single-author entries ("Qiang Liu") included. Counting
    commas does not work: a one-author entry has none.
    """
    words = [w for w in re.split(r"[\s,]+", seg.strip()) if w]
    if not words:
        return False
    # A SHARE, not "every word". Requiring all of them failed on two things that
    # are everywhere in a bibliography: the `et al.` that ends a long author list,
    # and names LaTeXML splits mid-ligature (`Ł ukasz` -> a stray lowercase word).
    # Titles are nowhere near the threshold — "Attention is all you need" scores
    # 0.2 and "Gpt-4 technical report" 0.33, against 0.93+ for an author list.
    # No length cap either: author lists here run to hundreds of names.
    named = sum(1 for w in words if w[0].isupper() or w.lower().strip(".") in _CONNECTOR)
    return named >= 0.85 * len(words)


def title_slug(entry: str) -> str:
    """The cited paper's TITLE, as a URL slug.

    Notion has no way to give a link a tooltip — hovering shows the raw URL, and
    the API cannot say otherwise: `link_mention` and `link_preview` mentions are
    read-only (creating one returns 400, and the error enumerates the only
    creatable kinds: user, date, page, database, template_mention, custom_emoji),
    while `plain_text` is computed and silently ignored on write.

    But a Notion address is `notion.so/<slug>-<32-hex-id>` and the slug is
    decorative — it is how every Notion URL carries its page name. Putting the
    paper's title there makes the hover text READ as the title while the link
    still resolves to the same block. A bibliography entry opens with its
    `Author et al. [YEAR]` label, then the full author list, then the title.
    """
    text = _LABEL.sub("", entry or "").strip()
    parts = text.split(". ")
    # At most two leading segments, so a mis-read can never eat a whole entry.
    for _ in range(2):
        if len(parts) > 1 and _is_author_list(parts[0]):
            parts = parts[1:]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ". ".join(parts)).strip("-")[:70].strip("-")
    return slug or "reference"


def _link(page_id: str, block_id: str, entry: str = "") -> str:
    return (f"https://www.notion.so/{title_slug(entry)}-{page_id.replace('-', '')}"
            f"#{block_id.replace('-', '')}")


def inject_references(page_id: str, entries: list, apply: bool) -> dict:
    """Append the English reference list; return {num: block_id}."""
    ids = {}
    blocks = [{"object": "block", "type": "heading_1",
               "heading_1": {"rich_text": [{"type": "text",
                                            "text": {"content": REFS_HEADING}}]}}]
    for e in entries:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [
                           {"type": "text",
                            "text": {"content": f"[{e['num']}] {e['text']}"[:2000]}}]}})
    if not apply:
        return {}
    made = []
    for i in range(0, len(blocks), 90):
        r = notion("PATCH", f"/blocks/{page_id}/children",
                   {"children": blocks[i:i + 90]})
        made.extend(r["results"])
        time.sleep(0.4)
    for e, blk in zip(entries, made[1:]):
        ids[e["num"]] = blk["id"]
    return ids


def _rewrite_block(block: dict, mapping: list, page_id: str, ref_ids: dict,
                   entries: dict = None) -> list:
    """New rich_text for `block`, consuming `mapping` (true numbers, in order).

    Splits only TEXT spans. An equation span is left untouched — its `plain_text`
    is its expression, and a bracket in a formula is not a citation.
    """
    kind = block["type"]
    spans, out = block[kind].get("rich_text", []), []
    for sp in spans:
        if sp.get("type") != "text":
            out.append(sp)
            continue
        text, pos = sp["text"]["content"], 0
        for m in _CITE.finditer(text):
            toks = [t.strip() for t in m.group(1).split(",")]
            true = [mapping.pop(0) for _ in toks if mapping]
            if len(true) != len(toks):
                continue                       # ran out: leave the rest untouched
            if pos < m.start():
                out.append(_clone(sp, text[pos:m.start()]))
            out.append(_clone(sp, "["))
            for j, n in enumerate(true):
                if j:
                    out.append(_clone(sp, ", "))
                out.append(_clone(sp, str(n),
                                  link=(_link(page_id, ref_ids[n], (entries or {}).get(n, ""))
                                        if n in ref_ids else None)))
            out.append(_clone(sp, "]"))
            pos = m.end()
        if pos < len(text):
            out.append(_clone(sp, text[pos:]))
    return out


def _clone(span: dict, content: str, link=None) -> dict:
    new = {"type": "text", "text": {"content": content[:2000]},
           "annotations": dict(span.get("annotations") or {})}
    if link:
        new["text"]["link"] = {"url": link}
    return new


def link_page(page_id: str, arxiv_id: str = None, apply: bool = False,
              force: bool = False) -> dict:
    rep = {"page": page_id, "entries": 0, "sections_linked": 0,
           "sections_skipped": 0, "slots_linked": 0, "refs": "kept"}
    # Cheap exit FIRST. This runs on the 5-minute healer against every recently
    # edited page, and fetching the paper's HTML to discover there is nothing to do
    # would put a network round-trip on each of them, every cycle. The reference
    # list is written once, with its links, so its presence means the work is done.
    blocks = vs.fetch_blocks(page_id)
    if not force and _find_refs_heading(blocks):
        rep["refs"] = "already present"
        return rep
    arxiv_id = arxiv_id or ef.arxiv_id_from_page(page_id)
    if not arxiv_id:
        rep["error"] = "no arxiv id"
        return rep
    html, _src = ef.fetch_html(arxiv_id)
    if not html:
        rep["error"] = "no HTML source"
        return rep
    entries = parse_bibliography(html)
    rep["entries"] = len(entries)
    if not entries:
        rep["error"] = "no bibliography in source"
        return rep

    found = _find_refs_heading(blocks)
    if found and not force:
        ref_ids = {}
        for bid in found[1]:
            blk = next((x for x in blocks if x["id"] == bid), None)
            m = blk and re.match(r"\s*\[(\d+)\]", block_text(blk))
            if m:
                ref_ids[int(m.group(1))] = bid
        rep["refs"] = f"already present ({len(ref_ids)})"
    else:
        if found and force and apply:
            for bid in [found[0]] + found[1]:
                try:
                    notion("PATCH", f"/blocks/{bid}", {"archived": True})
                except Exception:
                    pass
                time.sleep(0.15)
            blocks = vs.fetch_blocks(page_id)
        ref_ids = inject_references(page_id, entries, apply)
        rep["refs"] = f"{'injected' if apply else 'would inject'} {len(entries)}"

    src = source_citation_sequence(html)
    slots = page_citation_slots(blocks)
    plan = {}
    for key, page_slots in slots.items():
        want = src.get(key)
        if not want or len(want) != len(page_slots):
            rep["sections_skipped"] += 1
            continue
        plan[key] = (page_slots, want)
    rep["sections_linked"] = len(plan)

    if not apply or not ref_ids:
        rep["would_link"] = {k: len(v[1]) for k, v in plan.items()}
        return rep

    for key, (page_slots, want) in plan.items():
        per_block = {}
        for (bid, _old), true in zip(page_slots, want):
            per_block.setdefault(bid, []).append(true)
        for bid, mapping in per_block.items():
            blk = next((x for x in blocks if x["id"] == bid), None)
            if not blk:
                continue
            rich = _rewrite_block(blk, list(mapping), page_id, ref_ids,
                                  {e["num"]: e["text"] for e in entries})
            try:
                notion("PATCH", f"/blocks/{bid}",
                       {blk["type"]: {"rich_text": rich}})
                rep["slots_linked"] += len(mapping)
            except Exception as e:
                rep.setdefault("errors", []).append(f"{bid}: {type(e).__name__}")
            time.sleep(0.2)
    return rep


# Only pages built since this landed get a reference list on the healer. The
# owner asked for it on NEW papers; back-filling 770 existing pages is a separate,
# explicit decision (run the CLI on a page to do one by hand).
HEAL_MAX_AGE_DAYS = 7


def heal_references(page_id: str, apply: bool = False, created_time: str = None) -> dict:
    """Healer entry point — no-op on a page that already has its reference list."""
    if created_time is None:
        try:
            created_time = notion("GET", f"/pages/{page_id}").get("created_time")
        except Exception:
            created_time = None
    if created_time:
        import datetime
        try:
            made = datetime.datetime.fromisoformat(created_time.replace("Z", "+00:00"))
        except ValueError:
            return {"page": page_id, "skipped": "unparseable created_time"}
        age = datetime.datetime.now(datetime.timezone.utc) - made
        if age.days > HEAL_MAX_AGE_DAYS:
            return {"page": page_id, "skipped": f"page is {age.days}d old"}
    return link_page(page_id, apply=apply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="rebuild an existing reference section")
    a = ap.parse_args()
    import json
    print(json.dumps(link_page(a.page, a.arxiv, apply=not a.dry_run,
                               force=a.force), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
