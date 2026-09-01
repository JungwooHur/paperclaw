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
_CITE = re.compile(r"\[\s*(\d+(?:\s*[-\u2013,]\s*\d+)*)\s*\]")


def expand(group: str) -> list:
    """`1-5` -> [1,2,3,4,5]; `5, 7, 10` -> [5,7,10].

    A range is not a formatting variant, it is N citations. Reading only the
    comma form matched `[1-5]` not at all, so a section written in ranges counted
    ZERO markers, never equalled the source's count, and was skipped every time —
    which is why an introduction citing 25 works had none of them linked.
    """
    out = []
    for part in re.split(r"\s*,\s*", group):
        m = re.match(r"(\d+)\s*[-\u2013]\s*(\d+)$", part.strip())
        if m and int(m.group(1)) <= int(m.group(2)):
            out += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            out.append(int(part.strip()))
    return out

# arXiv numbers a bibliography's ids `bib.bib7` when entries are cited by number
# and `bib.bibx7` when they are cited by an author-initials label. The `x` is the
# only structural difference between the two, and missing it made every
# alphabetically-cited paper report "no bibliography in source" — which reads as
# a paper without references rather than as a parser that could not see them.
_BIB_ITEM = re.compile(r'<li[^>]+id="bib\.bibx?(\d+)"[^>]*>(.*?)</li>',
                       re.S | re.I)
# The label the body actually cites: `[7]` in a numeric list, `[ACDE12]` in an
# alphabetic one. It is stripped from the entry text, because it is written back
# as the entry's own marker and would otherwise appear twice.
_BIB_TAG_OPEN = re.compile(
    r'<span[^>]*class="[^"]*ltx_tag_bibitem[^"]*"[^>]*>', re.I)
_SPAN_EDGE = re.compile(r'<span\b|</span>', re.I)


def _tag_span(body: str):
    """(inner_html, start, end) of the label's span, or None.

    Walked rather than matched: a label like `[DGV+18]` puts its `+` in a
    superscript that contains a span of its own, so stopping at the first
    closing tag reads the label as `DGV+` and leaves `18]` stranded at the head
    of the entry text — where it then looks like part of the citation.
    """
    open_tag = _BIB_TAG_OPEN.search(body)
    if not open_tag:
        return None
    depth, at = 1, open_tag.end()
    while depth:
        edge = _SPAN_EDGE.search(body, at)
        if not edge:
            return None
        depth += 1 if edge.group(0).lower().startswith('<span') else -1
        at = edge.end()
    return body[open_tag.end():at - len('</span>')], open_tag.start(), at
_SECTION = re.compile(r'<section[^>]+id="(S\d+)"[^>]*>.*?<h2[^>]*>(.*?)</h2>', re.S | re.I)
_ANCHOR = re.compile(r'href="#bib\.bibx?(\d+)"')


def parse_bibliography(html: str) -> list:
    """[{num, text}] for every entry in the source's reference list, in order."""
    out = []
    for m in _BIB_ITEM.finditer(html):
        body = m.group(2)
        tag = _tag_span(body)
        label = ef._clean(tag[0]).strip() if tag else ""
        if tag:
            body = body[:tag[1]] + body[tag[2]:]
        text = ef._clean(body)
        if text:
            out.append({"num": int(m.group(1)),
                        "label": label.strip("[]").strip() or m.group(1),
                        "text": text})
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
            for num in expand(m.group(1)):
                out.setdefault(cur, []).append((b["id"], num))
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


_LABEL = re.compile(r"^.*?[\[(]\d{4}[a-z]?[\])]\s*")
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
                            "text": {"content":
                                f"[{e.get('label') or e['num']}] {e['text']}"[:2000]}}]}})
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


MAX_SPANS = 100


def coalesce(spans: list) -> list:
    """Merge adjacent unlinked text spans that share formatting.

    Linking a citation splits a paragraph into `[`, the number, `]` and the prose
    on either side — three extra spans per marker. Notion caps a block at 100
    rich_text spans, so a citation-dense paragraph blew past it: one had 32
    markers and produced 131 spans, and the PATCH failed with a 400 that aborted
    the whole run. The brackets carry no link, so they belong with the prose
    beside them; merging brings that paragraph to 65.
    """
    out = []
    for sp in spans:
        if (out and sp.get("type") == "text" and out[-1].get("type") == "text"
                and not (sp.get("text") or {}).get("link")
                and not (out[-1].get("text") or {}).get("link")
                and (sp.get("annotations") or {}) == (out[-1].get("annotations") or {})):
            out[-1] = dict(out[-1])
            out[-1]["text"] = dict(out[-1]["text"])
            out[-1]["text"]["content"] += sp["text"]["content"]
            continue
        out.append(sp)
    return out


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
            toks = expand(m.group(1))
            if len(mapping) < len(toks):
                continue                       # ran out: leave the rest untouched
            true = [mapping.pop(0) for _ in toks]
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
    out = coalesce(out)
    if len(out) > MAX_SPANS:
        raise ValueError(f"{len(out)} spans exceeds Notion's limit of {MAX_SPANS}")
    return out


def _clone(span: dict, content: str, link=None) -> dict:
    """A copy of `span` carrying `content`, keeping its formatting AND its link.

    Carrying the link matters more than it looks. A rewrite rebuilds every span in
    the block, so dropping it silently unlinked everything the previous pass had
    linked — and once a citation IS linked its number sits in its own span, so
    `[49]` no longer appears whole inside any single span and the rewriter cannot
    even see it to re-link. Two sections then took turns: each run linked one and
    stripped the other, forever.
    """
    new = {"type": "text", "text": {"content": content[:2000]},
           "annotations": dict(span.get("annotations") or {})}
    keep = (span.get("text") or {}).get("link") or (
        {"url": span["href"]} if span.get("href") else None)
    if link:
        new["text"]["link"] = {"url": link}
    elif keep:
        new["text"]["link"] = dict(keep)
    return new


def link_page(page_id: str, arxiv_id: str = None, apply: bool = False,
              force: bool = False, relink: bool = False) -> dict:
    rep = {"page": page_id, "entries": 0, "sections_linked": 0,
           "sections_skipped": 0, "slots_linked": 0, "refs": "kept"}
    # Cheap exit FIRST. This runs on the 5-minute healer against every recently
    # edited page, and fetching the paper's HTML to discover there is nothing to do
    # would put a network round-trip on each of them, every cycle. The reference
    # list is written once, with its links, so its presence means the work is done.
    blocks = vs.fetch_blocks(page_id)
    # `relink` re-runs the citation pass over an existing reference list, which is
    # how an improvement to the alignment reaches pages that were already built.
    if not force and not relink and _find_refs_heading(blocks):
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
        by_id = {x["id"]: block_text(x) for x in blocks}
        ref_ids = ref_ids_from_texts({bid: by_id.get(bid, "") for bid in found[1]},
                                     entries)
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
        # Equal counts are necessary, not sufficient. The translation renumbers a
        # section 1..N, so a number that recurs must resolve to the SAME source
        # reference every time — and where it does not, the alignment has slipped
        # and everything past that point is guesswork. One introduction matched on
        # count (25 = 25) while its closing group mapped [5] to two different
        # works. So take the longest PREFIX over which the map stays a function,
        # and leave the rest of the section untouched.
        seen, cut = {}, len(page_slots)
        for i, ((_bid, num), true) in enumerate(zip(page_slots, want)):
            if seen.setdefault(num, true) != true:
                cut = i
                break
        if not cut:
            rep["sections_skipped"] += 1
            continue
        if cut < len(page_slots):
            rep.setdefault("partial", {})[key] = f"{cut}/{len(page_slots)}"
        plan[key] = (page_slots[:cut], want[:cut])
    rep["sections_linked"] = len(plan)

    entries_by_num = {e["num"]: e["text"] for e in entries}
    ay_index = author_year_index(entries)
    if not apply or not ref_ids:
        rep["would_link"] = {k: len(v[1]) for k, v in plan.items()}
        rep["author_year_index"] = len(ay_index)
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
    # An author-year paper has no numeric markers to align at all; its citations
    # are linked by name, exactly.
    rep["label_linked"] = link_labels(page_id, vs.fetch_blocks(page_id),
                                      entries, ref_ids, apply)
    rep["author_year_linked"] = link_author_year(page_id, vs.fetch_blocks(page_id),
                                                 ay_index, ref_ids, entries_by_num, apply)
    return rep


# Only pages built since this landed get a reference list on the healer. The
# owner asked for it on NEW papers; back-filling 770 existing pages is a separate,
# explicit decision (run the CLI on a page to do one by hand).
HEAL_MAX_AGE_DAYS = 7



# --- author-year papers -------------------------------------------------------
# Half the papers here do not cite by number at all: the body says
# "(Brohan et al. 2022)" and the reference list is labelled "Brohan et al. [2022]".
# The numeric path above can do nothing with that, so those pages got a reference
# list and not one working link — on one of them, 73 citations.
#
# This needs no alignment and makes no assumption: the label IS the citation. Name
# plus year identifies one entry outright, and the year's `a`/`b` suffix is what
# separates an author's two papers in the same year, so it is part of the key.
# The label is bracketed in one paper and parenthesised in the next —
# "Belkhale et al. (2024)" and "Achiam et al. [2023]" are the same thing.
_AY_LABEL = re.compile(r"^(.+?)\s*[\[(](\d{4}[a-z]?)[\])]")
_AY_CITE = re.compile(
    r"([A-Z][\w.\-']*(?:\s+(?:and|&)\s+[A-Z][\w.\-']*)?(?:\s+et\s+al\.?)?)"
    r"[\s,]+(\d{4}[a-z]?)(?![\d])")


def label_pattern(labels: list):
    """A regex matching any of these citation labels where the body cites one.

    An alphabetic bibliography labels its entries `[ACDE12]`, and the body cites
    exactly that string, so the mapping is EXACT — none of the alignment
    guessing the numeric path needs. What varies is only spacing: the
    translation writes `[VSP+17]`, `[VSP + 17]` and `[ [RNSS18] ]` for the same
    citation, so whitespace inside the label is matched loosely while the label
    itself must match in full.

    Args:
        labels: The labels the bibliography defines.

    Returns:
        A compiled pattern whose group 1 is the label as the body wrote it. With
        no labels it matches nothing, rather than matching everything.
    """
    if not labels:
        return re.compile(r'(?!x)x')
    # Longest first, so `[VSP17]` cannot be claimed by a label that is a prefix
    # of it. The brackets around the group anchor both ends of the label.
    parts = [r'\s*'.join(re.escape(t) for t in label.split())
             for label in sorted(labels, key=len, reverse=True)]
    return re.compile(r'\[\s*(' + '|'.join(parts) + r')\s*\]')


def _ay_key(name: str, year: str) -> tuple:
    return (re.sub(r"[^a-z0-9 ]", "", name.lower()).strip(), year)


def author_year_index(entries: list) -> dict:
    """{(normalised name, year): reference number} from the bibliography labels."""
    out = {}
    for e in entries:
        m = _AY_LABEL.match(e["text"] or "")
        if m:
            out[_ay_key(m.group(1), m.group(2))] = e["num"]
    return out


def link_author_year(page_id: str, blocks: list, index: dict, ref_ids: dict,
                     entries_by_num: dict, apply: bool) -> int:
    """Link every `Author et al. YEAR` in the body to its reference block."""
    linked = 0
    for b in body_blocks_of(blocks):
        kind = b["type"]
        spans = (b.get(kind) or {}).get("rich_text")
        if not spans:
            continue
        out, hits = [], 0
        for sp in spans:
            if sp.get("type") != "text" or sp.get("href"):
                out.append(sp); continue
            text, pos = sp["text"]["content"], 0
            for m in _AY_CITE.finditer(text):
                num = index.get(_ay_key(m.group(1), m.group(2)))
                if num is None or num not in ref_ids:
                    continue
                if pos < m.start():
                    out.append(_clone(sp, text[pos:m.start()]))
                out.append(_clone(sp, m.group(0),
                                  link=_link(page_id, ref_ids[num],
                                             entries_by_num.get(num, ""))))
                pos = m.end(); hits += 1
            if pos < len(text):
                out.append(_clone(sp, text[pos:]))
        if not hits:
            continue
        before = "".join(s.get("plain_text", "") for s in spans)
        after = "".join(o["text"]["content"] if o.get("type") == "text"
                        else o.get("plain_text", "") for o in out)
        assert before == after, f"text changed in {b['id']}"   # link only
        linked += hits
        if apply:
            notion("PATCH", f"/blocks/{b['id']}", {kind: {"rich_text": out}})
            time.sleep(0.2)
    return linked


def ref_ids_from_texts(texts: dict, entries: list) -> dict:
    """{entry number: block id} for reference blocks already on the page.

    Keyed on the marker each block opens with, because that is what the block
    has: matching on the number alone worked only for numeric bibliographies and
    silently found nothing for a paper whose entries are labelled by author
    initials — which read as "no references present" and rebuilt the list.

    Args:
        texts: {block id: the block's text}.
        entries: The bibliography as parsed from the source.

    Returns:
        The mapping, skipping any block whose marker no entry claims.
    """
    # Whitespace is dropped entirely on both sides: the source writes
    # `[VSP + 17]` and the translated body writes `[VSP+17]` for the same entry.
    tight = lambda text: re.sub(r"\s+", "", text)
    by_label = {tight(e["label"]): e["num"] for e in entries if e.get("label")}
    found = {}
    for bid, text in texts.items():
        head = re.match(r"\s*\[([^\]]{1,32})\]", text or "")
        if not head:
            continue
        num = by_label.get(tight(head.group(1)))
        if num is not None:
            found[num] = bid
    return found


def is_stale_marker(span: dict, labels) -> bool:
    """Is this span a citation marker whose link needs re-pointing?

    Rebuilding the reference list gives every entry a new block, so the links
    already in the body point at blocks that are about to be archived. A linker
    that skips anything already linked would leave the whole body pointing at
    nothing — which reads exactly like the links working, until one is clicked.

    Only a span that IS the marker qualifies. A link someone put on ordinary
    prose is theirs and is left alone.
    """
    if span.get("type") != "text":
        return False
    linked = span.get("href") or (span.get("text") or {}).get("link")
    if not linked:
        return False
    text = (span.get("text") or {}).get("content", "").strip()
    return bool(text.startswith("[") and text.endswith("]")
                and " ".join(text[1:-1].split()) in labels)


def link_labels(page_id: str, blocks: list, entries: list, ref_ids: dict,
                apply: bool) -> int:
    """Link every `[ACDE12]` in the body to the entry that defines it.

    Only alphabetic labels go through here. A numeric bibliography is cited as
    `[7]`, which the numeric path already aligns section by section; a label is
    unique text, so it needs no alignment at all and cannot be mapped wrong.
    """
    by_label = {e["label"]: e for e in entries
                if e.get("label") and not e["label"].isdigit()}
    pattern = label_pattern(list(by_label))
    linked = 0
    for b in body_blocks_of(blocks):
        kind = b["type"]
        spans = (b.get(kind) or {}).get("rich_text")
        if not spans:
            continue
        out, hits = [], 0
        for sp in spans:
            stale = is_stale_marker(sp, set(by_label))
            if sp.get("type") != "text" or (sp.get("href") and not stale):
                out.append(sp)
                continue
            if stale:                      # drop the old target before relinking
                sp = dict(sp, href=None,
                          text=dict(sp.get("text") or {}, link=None))
            text, pos = sp["text"]["content"], 0
            for m in pattern.finditer(text):
                entry = by_label.get(" ".join(m.group(1).split()))
                if entry is None or entry["num"] not in ref_ids:
                    continue
                if pos < m.start():
                    out.append(_clone(sp, text[pos:m.start()]))
                out.append(_clone(sp, m.group(0),
                                  link=_link(page_id, ref_ids[entry["num"]],
                                             entry["text"])))
                pos = m.end()
                hits += 1
            if pos < len(text):
                out.append(_clone(sp, text[pos:]))
        if not hits:
            continue
        before = "".join(s.get("plain_text", "") for s in spans)
        after = "".join(o["text"]["content"] if o.get("type") == "text"
                        else o.get("plain_text", "") for o in out)
        assert before == after, f"text changed in {b['id']}"   # link only
        out = coalesce(out)
        linked += hits
        if apply:
            notion("PATCH", f"/blocks/{b['id']}", {kind: {"rich_text": out}})
            time.sleep(0.2)
    return linked


def body_blocks_of(blocks: list) -> list:
    return reference_section.body_blocks(blocks)


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
    ap.add_argument("--relink", action="store_true",
                    help="keep the reference list, re-run the citation linking")
    a = ap.parse_args()
    import json
    print(json.dumps(link_page(a.page, a.arxiv, apply=not a.dry_run,
                               force=a.force, relink=a.relink),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
