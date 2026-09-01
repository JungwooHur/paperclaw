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
import list_markers
import inline_emphasis
from translate_fulltext import notion

_HERE = os.path.dirname(os.path.abspath(__file__))
_HEAD = ("heading_1", "heading_2", "heading_3")


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
    richest = max(occurrences, key=lambda s: s["chars"])
    if richest["chars"] > first["chars"] * RICHER_FACTOR:
        return richest
    return first


def dedupe_duplicates(page_id, blocks, apply):
    """Archive redundant duplicate sections keeping the richest copy. Scopes keys
    hierarchically (parent chain) exactly like verify_sections, so a subsection
    letter reused under different parents (II>A vs III>A) is NOT a duplicate."""
    sections = vs.group_sections(blocks)
    scoped, stack = {}, []
    for s in sections:
        while stack and stack[-1][0] >= s["level"]:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        if s["key"]:
            scope = f"{parent}>{s['key']}"
            scoped.setdefault(scope, []).append(s)
        else:
            # Match verify_sections: an unlabelled heading IS dup-checked, but only
            # within its own parent — otherwise the healer reports a duplicate the
            # audit found and then archives nothing.
            scope = f"{parent}>~{vs._echo_norm(s['heading'])[:16]}"
            scoped.setdefault(scope, []).append(s)
        stack.append((s["level"], scope))
    ranges = _section_ranges(blocks)
    seen, to_archive = set(), []
    for occ in scoped.values():
        if len(occ) < 2:
            continue
        keep = choose_kept(occ)                # position first, richness to break it
        occ = [keep] + [o for o in occ if o is not keep]
        if not occ[0]["key"]:
            # Same rule the audit uses: an unlabelled title repeats legitimately,
            # so only archive a copy whose BODY matches the one being kept.
            occ = [occ[0]] + [o for o in occ[1:] if vs.dup_confirmed(occ[0], o)]
        for d in occ[1:]:
            for bid in ranges.get(d["heading_id"], []):
                if bid not in seen:            # overlapping parent/child dup ranges
                    seen.add(bid)              # must not double-PATCH a block
                    to_archive.append(bid)
    # safety cap: never archive most of the page (a mis-scope shouldn't nuke it)
    if len(to_archive) > len(blocks) // 2:
        return 0
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
           "split_headings": 0, "stripped_markers": 0, "emphasis_fixed": 0, "flags": []}
    # Split first, then look again: a bloated heading hides its own section's
    # duplicate from the dedup below, so the audit that ran before the split
    # cannot be trusted to have reported one.
    rep["split_headings"] = split_bloated_headings(page_id, findings, apply)
    rep["stripped_markers"] = strip_list_markers(page_id, findings, apply)
    rep["emphasis_fixed"] = apply_emphasis(page_id, findings, apply)
    if rep["split_headings"] and apply:
        result = audit(page_id, arxiv)
        findings = result.get("findings", [])
        kinds = Counter(f.get("type") for f in findings)
        rep["findings"] = dict(kinds)
    # auto-repair DUPLICATE only, and only if the audit agrees one exists
    if kinds.get("DUPLICATE"):
        blocks = vs.fetch_blocks(page_id)
        rep["deduped_blocks"] = dedupe_duplicates(page_id, blocks, apply)
    # surface the content-integrity findings (dropped/short/summarized/duplicated
    # sections) — these are the silent breakage the agent's assembly produces
    for f in findings:
        if f.get("type") in ("MISSING", "CONTENT_LOSS", "SUMMARIZED",
                              "DUPLICATE", "PARA_DUP", "SKIPPED_TRANSLATION"):
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
