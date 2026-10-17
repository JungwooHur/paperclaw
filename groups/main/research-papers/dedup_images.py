#!/usr/bin/env python3
"""Archive repeated copies of the same figure or table image on a paper page.

Root cause
----------
An injector decides what it has already placed by reading its own captions back.
Each time that recognition failed -- an IEEE `TABLE I` read as no number, an
appendix `Table A:` read as none -- the five-minute healer injected another copy,
and a page collected the same tables dozens or hundreds of times over. The
injectors now key on the caption text and refuse once a caption repeats (the
`runaway` guard in extract_paper_tables), so a page no longer grows. But refusing
removes nothing: a page that ran away before those fixes keeps every copy, and
the injector then refuses to touch it at all.

This keeps the FIRST copy of each caption in page order and archives the rest. A
copy placed beside the text that cites it comes before a pile appended at the end
of the body. Identity is the caption key the runaway guard itself counts, so a
page this leaves behind is one the injector will work on again. (The file name
is no help: tables are uploaded as `table_<i>` numbered per run, and figures under
temporary names, so copies of one table rarely share a name.)

Not on the five-minute healer, deliberately: run there, it would remove the very
repeat the runaway guard exists to see, so an injector that has lost track of its
output again would add a copy, have it removed, and add it again on every cycle.
The healer prints `TABLES runaway` with this command instead. Run it by hand; it
only reports until given --apply:

  dedup_images.py --page <id> [--apply]

What it never touches: an image with no caption (that is how a person's own image
looks), and anything below the top level (injected images are top-level blocks; a
Q&A callout's contents are not this tool's business).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_paper_tables import _cap_key, _image_caption  # noqa: E402


def image_key(block: dict):
    """The caption key of a captioned image block, else None."""
    if block.get("type") != "image":
        return None
    return _cap_key(_image_caption(block)) or None


def duplicate_images(blocks: list) -> list:
    """Ids of every image that repeats an earlier one's caption, in page order.

    The first block carrying a key is kept; each later block with the same key is
    returned. Applying the result and running this again yields nothing.
    """
    seen, extra = set(), []
    for b in blocks:
        if b.get("archived") or b.get("in_trash"):
            continue
        key = image_key(b)
        if key is None:
            continue
        if key in seen:
            extra.append(b["id"])
        else:
            seen.add(key)
    return extra


def dedup_images(page_id: str, apply: bool = False) -> dict:
    import verify_sections as vs
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)
    extra = duplicate_images(blocks)
    extra_ids = set(extra)
    repeats = {}
    for b in blocks:
        if b["id"] in extra_ids:
            key = image_key(b)
            repeats[key] = repeats.get(key, 0) + 1
    rep = {"page": page_id, "images": sum(b["type"] == "image" for b in blocks),
           "would_archive": len(extra), "archived": [],
           "repeated": {k[:60]: n for k, n in repeats.items()}}
    if not apply:
        return rep
    for bid in extra:
        try:
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            rep["archived"].append(bid)  # kept so an archive can be undone
        except Exception as e:  # one stubborn block must not undo the rest
            rep.setdefault("archive_failed", []).append(f"{bid[-8:]}: {e}"[:120])
        time.sleep(0.34)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="archive the repeats (default: report only)")
    a = ap.parse_args()
    print(json.dumps(dedup_images(a.page, apply=a.apply),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
