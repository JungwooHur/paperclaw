#!/usr/bin/env python3
r"""Structurally enforce the verify_sections audit on built paper pages.

Why this exists
---------------
`verify_sections.py` is a MANDATORY Step 2-C gate — but the agent runs it by PROSE
rule only, and skips it. So pages assembled by the agent's hand-rolled multi-batch
Notion PATCH ship broken and silent: Notion returns `401` on a large `children`
payload (a size issue, NOT auth — the token is a long-lived `ntn_` integration
token), the agent splits/retries and loses track of what it uploaded, and whole
sections get DROPPED (a paper came out with only its appendix) or DUPLICATED /
reordered (every section twice). The heading-count check passes anyway.

This runs the audit on the 5-minute healer so broken pages are ALWAYS surfaced
regardless of the agent, and auto-repairs the one safe case:

  * DUPLICATE  -> archive the redundant copy (heading + its body), KEEPING the
                 richest occurrence (most body chars) so content is never lost.
                 Gated on verify_sections also reporting a DUPLICATE, and capped so
                 it can never archive most of the page.
  * MISSING / CONTENT_LOSS / SUMMARIZED -> flagged only (the content was never
                 uploaded; the healer can't recreate it), so they show up in the
                 journal for re-processing instead of shipping as "looks fine".
"""
import json
import os
import subprocess
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_sections as vs
import heading_bloat
import paragraph_headings
import list_markers
import inline_emphasis
from translate_fulltext import notion

_HERE = os.path.dirname(os.path.abspath(__file__))
_HEAD = ("heading_1", "heading_2", "heading_3")
# Block types whose text `verify_sections.group_sections` counts.
_TEXT_TYPES = _HEAD + vs.BODY_TYPES + ("equation",)


def audit(page_id: str, arxiv: str = None) -> dict:
    """Run verify_sections --json and return its report dict ({findings: [...]}).
    RAISES on a real failure (crash / no JSON) so the healer logs it loudly rather
    than silently treating a broken page as clean. `--json` prints the findings
    regardless of exit code, so a non-zero exit (findings present) is NOT an error —
    only missing/invalid JSON is."""
    cmd = [sys.executable, os.path.join(_HERE, "verify_sections.py"),
           "--page", page_id, "--json"]
    if arxiv:
        cmd += ["--arxiv", arxiv]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout)
    except (ValueError, TypeError):
        raise RuntimeError(f"verify_sections failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout or '')[:200]}")


def _lvl(b):
    return int(b["type"][-1]) if b["type"] in _HEAD else 99


def _section_ranges(blocks):
    """heading_id -> [block ids from that heading up to the next heading at the
    SAME or SHALLOWER level]."""
    ranges = {}
    for i, b in enumerate(blocks):
        if b["type"] not in _HEAD:
            continue
        j = i + 1
        while j < len(blocks) and not (blocks[j]["type"] in _HEAD
                                       and _lvl(blocks[j]) <= _lvl(b)):
            j += 1
        ranges[b["id"]] = [x["id"] for x in blocks[i:j]]
    return ranges


# How much richer a later copy must be to beat the one in its proper place.
# Keep-richest exists to reject a copy cut short by a failed upload, and such a
# copy is much smaller, not marginally so. Without this margin two re-uploads of
# the same section are decided by a hair, which is how a page ended up with its
# section 1.1 content living inside section 1.3: the later copy won by a few
# characters and the correctly placed one was archived.
RICHER_FACTOR = 1.5

# The least fraction of an archived section's own distinct words, of three
# letters or more, that a copy holding its text has.
SHARED_WORDS = 0.6

# The fewest such words an archived section's own text has for `SHARED_WORDS` of
# them to hold it; a text with fewer is held only verbatim.
MIN_WORDS = 20


class DedupRefused(RuntimeError):
    """`dedupe_duplicates` refusing a page: nothing on it was archived."""


def choose_kept(occurrences):
    """The copy of a duplicated section to keep, given them in document order.

    Args:
        occurrences: Section dicts as `verify_sections.group_sections` returns
          them, in the order they appear on the page.

    Returns:
        The one to keep — the earliest, unless a later copy is substantially
        richer, in which case the earlier one was truncated and losing it is the
        point.
    """
    first = occurrences[0]
    richest = max(occurrences, key=vs.content_chars)
    if vs.content_chars(richest) > vs.content_chars(first) * RICHER_FACTOR:
        return richest
    return first


def _holds(copy, section):
    """Does `copy` hold `section`'s whole own text, subsections left out: at least
    `SHARED_WORDS` of its words, all of it verbatim when it has fewer than
    `MIN_WORDS` words or `vs.EMPTY_TWIN_CHARS` characters, and at least as many
    characters."""
    own = vs.own_content_chars(section)
    words = vs._words(section["own_text"])
    if len(words) >= MIN_WORDS and own >= vs.EMPTY_TWIN_CHARS:
        shares = len(words & vs._words(copy["own_text"])) >= SHARED_WORDS * len(words)
    else:
        shares = " ".join(section["own_text"].split()) in " ".join(copy["own_text"].split())
    return shares and own <= vs.own_content_chars(copy)


def _own_blocks(ordered, sections):
    """Heading id -> the blocks of `ordered` after that section's heading, up to
    the next section's: its own span, subsections left out."""
    own, cur = {}, None
    starts = {s["heading_id"] for s in sections}
    for b in ordered:
        if b["id"] in starts:
            cur = own.setdefault(b["id"], [])
        elif cur is not None:
            cur.append(b)
    return own


def _unread(block):
    """Does `block` hold what the text check does not read: children, or content
    of a type `group_sections` counts no text of? A `column_list` left in
    reading order holds no block."""
    return block["type"] != "column_list" and bool(
        block.get("has_children") or block["type"] not in _TEXT_TYPES)


def _key(block):
    """`block` as compared across copies: its type, its caption with whitespace
    normalised, the rest of its payload and its children's keys; a captioned hosted
    file's payload leaves out `file`, the url its upload signed."""
    payload = dict(block.get(block["type"]) or {})
    caption = " ".join("".join(t["plain_text"] for t in payload.pop("caption", None) or [])
                       .split())
    if caption and payload.get("type") == "file":
        del payload["file"]
    kids = [_key(c) for c in vs.fetch_blocks(block["id"])] if block.get("has_children") else []
    return json.dumps([block["type"], caption, payload, kids], ensure_ascii=False,
                      sort_keys=True)


def _top_level_range(page_id, section, span, held, top):
    """The top-level block ids that hold exactly `span`.

    Args:
        page_id: The page, named in the error.
        section: The `group_sections` section `span` belongs to.
        span: Its block ids in reading order, heading first.
        held: Top-level block id -> its blocks in reading order.
        top: Block id in reading order -> the top-level block id holding it.

    Raises:
        DedupRefused: The section's heading sits in a column, or a column_list
          holds both blocks of `span` and blocks outside it.
    """
    if top[section["heading_id"]] != section["heading_id"]:
        raise DedupRefused(f"page {page_id}: duplicate {section['heading']!r} has its "
                           f"heading inside a column; no top-level range archives it")
    ids = list(dict.fromkeys(top[bid] for bid in span))
    inside = set(span)
    for bid in ids:
        if any(b["id"] not in inside for b in held[bid]):
            raise DedupRefused(f"page {page_id}: duplicate {section['heading']!r} ends "
                               f"inside column_list {bid}, which holds the next section")
    return ids


def dedupe_duplicates(page_id, blocks, apply):
    """Archive redundant duplicate sections keeping the richest copy. Scopes keys
    hierarchically (parent chain) exactly like verify_sections, so a subsection
    letter reused under different parents (II>A vs III>A) is NOT a duplicate.

    Copies are measured and compared in reading order (`vs.reading_order`), as
    the audit reads them; what is archived is whole top-level blocks of `blocks`,
    the page's top-level children. A group keeps one of the copies that the
    groups before it, its parent's among them, left standing.

    Raises:
        DedupRefused: Before archiving anything, when a copy to archive has no
          top-level range (`_top_level_range`), when the plan archives more than
          half the page's top-level blocks, or when a section the plan archives
          has a heading with children, text of its own that no copy outside the
          plan `_holds`, or `_unread` own blocks that such copies do not match
          one for one by `_key`.
        RuntimeError: A PATCH failed; the other blocks were archived.
    """
    # A top-level block with nothing in reading order, an empty column_list,
    # stands for itself.
    held = {b["id"]: vs.reading_order([b]) or [b] for b in blocks}
    ordered = [x for b in blocks for x in held[b["id"]]]
    top = {x["id"]: b["id"] for b in blocks for x in held[b["id"]]}
    sections = vs.group_sections(ordered)
    scoped = vs.duplicate_groups(sections)
    ranges = _section_ranges(ordered)
    seen, to_archive = set(), []

    def archived(section):
        return top[section["heading_id"]] in seen

    for occ in scoped.values():
        standing = [o for o in occ if not archived(o)]
        if len(occ) < 2 or not standing:
            continue
        keep = choose_kept(standing)           # position first, richness to break it
        occ = [keep] + [o for o in occ if o is not keep]
        # Same rule the audit uses: only archive a copy whose BODY matches the
        # one being kept. Unlabelled titles repeat legitimately, and a label can
        # be mis-read — `A-A` … `A-G` were once all keyed `A`, and trusting the
        # key archived six real subsections.
        occ = [occ[0]] + [o for o in occ[1:] if vs.dup_confirmed(occ[0], o)]
        for d in occ[1:]:
            if all(top[x] in seen for x in ranges[d["heading_id"]]):
                continue
            for bid in _top_level_range(page_id, d, ranges[d["heading_id"]], held, top):
                if bid not in seen:            # overlapping parent/child dup ranges
                    seen.add(bid)              # must not double-PATCH a block
                    to_archive.append(bid)
    # safety cap: never archive most of the page (a mis-scope shouldn't nuke it)
    if len(to_archive) > len(blocks) // 2:
        raise DedupRefused(f"page {page_id}: the dedup would archive {len(to_archive)} of "
                           f"{len(blocks)} top-level blocks, more than half; nothing archived")
    own = _own_blocks(ordered, sections)
    by_id = {b["id"]: b for b in ordered}

    def unread(section):
        """`section`'s `_unread` own blocks, counted by `_key`."""
        return Counter(_key(b) for b in own[section["heading_id"]] if _unread(b))

    for occ in scoped.values():
        for s in occ:
            if not archived(s):
                continue
            if by_id[s["heading_id"]].get("has_children"):
                raise DedupRefused(f"page {page_id}: the dedup would archive {s['heading']!r}, "
                                   f"a heading with children; nothing archived")
            standing = [o for o in occ if not archived(o)]
            if vs.own_content_chars(s) and not any(_holds(o, s) for o in standing):
                raise DedupRefused(f"page {page_id}: the dedup would archive {s['heading']!r} "
                                   f"and every copy holding its text; nothing archived")
            missing = unread(s) - sum((unread(o) for o in standing), Counter())
            if missing:
                kinds = ", ".join(sorted({json.loads(k)[0] for k in missing}))
                raise DedupRefused(f"page {page_id}: the dedup would archive {s['heading']!r} "
                                   f"with {sum(missing.values())} {kinds} block(s) no copy "
                                   f"left standing holds; nothing archived")
    if apply:
        failures = []
        for bid in to_archive:
            try:
                notion("PATCH", f"/blocks/{bid}", {"archived": True})
            except Exception as e:               # archive all we can, report failures
                failures.append((bid, str(e)))
            time.sleep(0.34)
        if failures:
            raise RuntimeError(f"dedup failed on {len(failures)} block(s): {failures[:3]}")
    return len(to_archive)


def promote_paragraph_headings(page_id, findings, apply):
    """Turn section titles the assembler left as paragraphs into headings.

    Runs before everything else: with no headings a page has no sections, so the
    audit that ran first could not have reported a duplicate, a bloated heading
    or anything else that reads structure.
    """
    ids = [bid for f in findings if f.get("type") == "HEADINGS_AS_PARAGRAPHS"
           for bid in f.get("block_ids", [])]
    fixed = 0
    for bid in ids:
        block = notion("GET", f"/blocks/{bid}")
        new = paragraph_headings.promote(block)
        if new is None:
            continue
        if apply:
            kind = new["type"]
            # A block's type cannot be changed in place, so the heading is
            # inserted after the paragraph before the paragraph is archived —
            # a failed insert can then never empty the section.
            notion("PATCH", f"/blocks/{page_id}/children",
                   {"children": [new], "after": bid})
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            time.sleep(0.34)
        fixed += 1
    return fixed


def apply_emphasis(page_id, findings, apply):
    """Turn leftover markdown emphasis into an italic span."""
    ids = [bid for f in findings if f.get("type") == "EMPHASIS_MARKER"
           for bid in f.get("block_ids", [])]
    fixed = 0
    for bid in ids:
        new = inline_emphasis.convert(notion("GET", f"/blocks/{bid}"))
        if new is None:
            continue
        if apply:
            notion("PATCH", f"/blocks/{bid}", {new["type"]: new[new["type"]]})
            time.sleep(0.34)
        fixed += 1
    return fixed


def strip_list_markers(page_id, findings, apply):
    """Remove the bullet a list item kept from its markdown source."""
    ids = [bid for f in findings if f.get("type") == "LIST_MARKER"
           for bid in f.get("block_ids", [])]
    fixed = 0
    for bid in ids:
        block = notion("GET", f"/blocks/{bid}")
        new = list_markers.strip_marker(block)
        if new is None:
            continue
        if apply:
            kind = new["type"]
            notion("PATCH", f"/blocks/{bid}", {kind: new[kind]})
            time.sleep(0.34)
        fixed += 1
    return fixed


def split_bloated_headings(page_id, findings, apply):
    """Split headings that swallowed their paragraph, reporting how many moved.

    Runs before the dedup on purpose. A heading carrying its body does not match
    the same section's other copy, so while it stands the dedup cannot tell the
    section was uploaded twice — the cosmetic defect is what hides the expensive
    one. Splitting restores the heading text the other copy has, and the dedup
    then sees a duplicate where it previously saw two different sections.

    Blocks are inserted before the original is archived, so a failed insert
    cannot leave the section empty.
    """
    ids = [bid for f in findings if f.get("type") == "HEADING_BLOAT"
           for bid in f.get("block_ids", [])]
    if not ids:
        return 0
    moved = 0
    for bid in ids:
        block = notion("GET", f"/blocks/{bid}")
        pair = heading_bloat.split_block(block)
        if pair is None:      # not a run-in title; the audit keeps reporting it
            continue
        if apply:
            notion("PATCH", f"/blocks/{page_id}/children",
                   {"children": list(pair), "after": bid})
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            time.sleep(0.34)
        moved += 1
    return moved


def heal_verify(page_id: str, apply: bool = False) -> dict:
    from extract_paper_figures import arxiv_id_from_page
    arxiv = arxiv_id_from_page(page_id)
    result = audit(page_id, arxiv)
    findings = result.get("findings", [])
    kinds = Counter(f.get("type") for f in findings)
    rep = {"page": page_id, "findings": dict(kinds), "deduped_blocks": 0,
           "promoted_headings": 0, "split_headings": 0, "stripped_markers": 0,
           "emphasis_fixed": 0, "dedup_refused": None, "flags": []}
    # Split first, then look again: a bloated heading hides its own section's
    # duplicate from the dedup below, so the audit that ran before the split
    # cannot be trusted to have reported one.
    rep["promoted_headings"] = promote_paragraph_headings(page_id, findings, apply)
    rep["split_headings"] = split_bloated_headings(page_id, findings, apply)
    rep["stripped_markers"] = strip_list_markers(page_id, findings, apply)
    rep["emphasis_fixed"] = apply_emphasis(page_id, findings, apply)
    if (rep["split_headings"] or rep["promoted_headings"]) and apply:
        result = audit(page_id, arxiv)
        findings = result.get("findings", [])
        kinds = Counter(f.get("type") for f in findings)
        rep["findings"] = dict(kinds)
    # auto-repair DUPLICATE only, and only if the audit agrees one exists
    if kinds.get("DUPLICATE"):
        blocks = vs.fetch_blocks(page_id)
        try:
            rep["deduped_blocks"] = dedupe_duplicates(page_id, blocks, apply)
        except DedupRefused as e:
            rep["dedup_refused"] = str(e)
    # surface the content-integrity findings (dropped/short/summarized/duplicated
    # sections) — these are the silent breakage the agent's assembly produces
    for f in findings:
        if f.get("type") in ("MISSING", "CONTENT_LOSS", "SUMMARIZED",
                              "DUPLICATE", "PARA_DUP", "SKIPPED_TRANSLATION",
                              "HEADINGS_AS_PARAGRAPHS"):
            rep["flags"].append(f"{f['type']}({f.get('section') or ''}): "
                                f"{(f.get('detail') or '')[:70]}")
    return rep


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(heal_verify(a.page, apply=not a.dry_run),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
