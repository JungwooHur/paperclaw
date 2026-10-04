#!/usr/bin/env python3
"""A Notion page as plain text, for answering questions about it.

Made for the owner's lecture pages: a video at the top and the narration script
and storyboard beneath. The script IS the source, so an answer reads it whole —
no upload to NotebookLM, nothing to go stale when the script is edited, and no
daily ask quota spent. Earlier Q&A callouts are left out: they are answers, not
the lecture.

    page_text.py --page <id>            # print the page
    page_text.py --page <id> --out f    # write it to a file
"""
import argparse
import sys

# Lists and toggles nest; deeper than this is never script content.
MAX_DEPTH = 4


def _text(rich):
    return "".join(r.get("plain_text", "") for r in rich or ())


def _is_qa_callout(block, children):
    """A Q&A callout: its first child is a toggle labelled `Q: …`."""
    if block.get("type") != "callout" or not block.get("has_children"):
        return False
    kids = children(block["id"])
    return bool(kids) and kids[0].get("type") == "toggle" and _text(
        kids[0]["toggle"].get("rich_text")).lstrip().startswith("Q:")


def _lines(block, children, depth):
    kind = block.get("type")
    payload = block.get(kind) or {}
    pad = "  " * depth
    if kind and kind.startswith("heading_"):
        yield "#" * int(kind[-1]) + " " + _text(payload.get("rich_text"))
    elif kind == "bulleted_list_item":
        yield pad + "- " + _text(payload.get("rich_text"))
    elif kind == "numbered_list_item":
        yield pad + "1. " + _text(payload.get("rich_text"))
    elif kind in ("video", "embed", "bookmark", "image", "file", "pdf"):
        url = payload.get("url") or (payload.get("external") or {}).get("url") \
            or (payload.get("file") or {}).get("url") or ""
        caption = _text(payload.get("caption"))
        yield f"[{kind}] {url} {caption}".rstrip()
    elif kind == "equation":
        yield "$$" + payload.get("expression", "") + "$$"
    elif kind == "code":
        yield "```" + payload.get("language", "") + "\n" + _text(
            payload.get("rich_text")) + "\n```"
    elif kind == "table":
        for row in children(block["id"]):
            cells = (row.get("table_row") or {}).get("cells") or []
            yield pad + " | ".join(_text(c) for c in cells)
        return
    elif "rich_text" in payload:
        text = _text(payload.get("rich_text"))
        if text:
            yield pad + text
    if block.get("has_children") and depth < MAX_DEPTH:
        for child in children(block["id"]):
            yield from _lines(child, children, depth + 1)


def page_text(blocks, children) -> str:
    """The page's text, top to bottom, with earlier Q&A answers left out.

    Args:
        blocks: The page's top-level blocks.
        children: block id -> that block's children (injectable for tests).
    """
    out = []
    for block in blocks:
        if _is_qa_callout(block, children):
            continue
        out.extend(_lines(block, children, 0))
    return "\n".join(out) + "\n"


def _children(block_id):
    import verify_sections as vs
    return vs.fetch_blocks(block_id)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--page", required=True)
    ap.add_argument("--out", help="write here instead of stdout")
    a = ap.parse_args()
    import verify_sections as vs
    text = page_text(vs.fetch_blocks(a.page), _children)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {len(text)} chars to {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
