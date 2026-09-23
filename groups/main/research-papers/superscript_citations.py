#!/usr/bin/env python3
"""Brackets a citation that arrived glued to the word before it.

Nature and journals like it set citations as superscripts. A translation made
from the rendered paper flattens them into the text, so the body reads
`Method11,12` and `…구성한다20`: the number looks like part of the word, and
nothing links it, because only a bracketed marker is recognised as a citation.

The repair INSERTS and never replaces. Every character that was there before is
still there, in the same order — which is what makes it safe to run on a page
somebody is reading and editing, and what the tests pin down.

Refusing is the default. A number is bracketed only when it is glued to a word,
carries no decimal or further digits, and names an entry the page's bibliography
actually has. Everything that merely looks numeric — a year, a board size, an
identifier ending in digits — is left exactly as it is.

Imports nothing but `re`.
"""
import re

# The number, or comma/dash group, immediately after a word character or a
# closing parenthesis — the body writes both `구성한다20` and `(ABC)11,12`.
# A DIGIT before it is excluded, so `1994` inside `(1994)` and the tail of
# `19x19` are never the start of a match.
#
# What may follow is a LATIN letter and nothing else: `conv2d` is an
# identifier, not a citation. A Korean particle glues straight onto the
# number — `method8는`, `항목6에서` — and excluding word characters in
# general would make every one of those invisible, which is the same trap
# this project has hit with `\b` three times.
_GLUED = re.compile(
    r"(?:(?<=[^\W\d_])|(?<=\)))(\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*)"
    r"(?![\d.,]*\d)(?![A-Za-z])")

# `19x19`, `3x3`: a digit and a single letter right before the match means the
# number belongs to a measurement, not to the bibliography.
_MEASUREMENT = re.compile(r"\d\s*[a-zA-Z×]$")


def convert(text: str, max_ref: int) -> str:
    """Bracket every glued citation in `text`, leaving everything else alone.

    Args:
        text: A block's text.
        max_ref: The highest number the page's bibliography defines. A number
          above it cannot be a citation on this page.

    Returns:
        The text with ` [` and `]` inserted around each citation. Nothing is
        removed and nothing is rewritten, so stripping those two tokens returns
        exactly what was passed in.
    """
    def replace(found):
        group = found.group(1)
        if _MEASUREMENT.search(text[:found.start()][-4:]):
            return group
        numbers = [int(n) for n in re.findall(r"\d+", group)]
        if not numbers or not all(1 <= n <= max_ref for n in numbers):
            return group
        return " [%s]" % group

    return _GLUED.sub(replace, text or "")


def heal_page(page_id: str, max_ref: int, apply: bool = False) -> dict:
    """Bracket every glued citation on a page, one block at a time.

    Writes a block only when its text actually changes, and refuses the write if
    anything but brackets and spaces differs — this edits a page somebody reads,
    so the invariant is checked against the real text rather than assumed from
    the tests.
    """
    import re
    import time

    import reference_section
    import verify_sections as vs
    from translate_fulltext import notion

    bare = lambda text: re.sub(r"[\[\]\s]", "", text)
    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "blocks": 0, "citations": 0}
    for block in reference_section.body_blocks(blocks):
        kind = block["type"]
        spans = (block.get(kind) or {}).get("rich_text")
        if not spans:
            continue
        out, hits = [], 0
        for span in spans:
            if span.get("type") != "text":
                out.append(span)
                continue
            content = (span.get("text") or {}).get("content", "")
            fixed = convert(content, max_ref)
            if fixed != content:
                hits += fixed.count(" [") - content.count(" [")
                span = dict(span,
                            text=dict(span.get("text") or {}, content=fixed),
                            plain_text=fixed)
            out.append(span)
        if not hits:
            continue
        before = "".join((s.get("text") or {}).get("content", "") for s in spans)
        after = "".join((s.get("text") or {}).get("content", "") for s in out)
        if bare(before) != bare(after):
            raise ValueError(f"text changed in {block['id']}; refusing to write")
        rep["blocks"] += 1
        rep["citations"] += hits
        if apply:
            notion("PATCH", f"/blocks/{block['id']}", {kind: {"rich_text": out}})
            time.sleep(0.25)
    return rep


def main() -> int:
    """Run by hand: this edits body text, so it is never put on a timer."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--max-ref", type=int, required=True,
                        help="highest number the page's bibliography defines")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(heal_page(args.page, args.max_ref, apply=args.apply),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
