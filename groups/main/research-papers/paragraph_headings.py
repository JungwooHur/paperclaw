#!/usr/bin/env python3
"""Promotes a section title the assembler emitted as an ordinary paragraph.

Every structural check on a paper page reads headings. The audit groups sections
by them, the duplicate healer keys on them, the citation alignment splits on
them. A page whose titles came out as paragraphs therefore has no sections at
all, and the audit reports it as "nothing translated yet" — so every other check
is skipped and the page becomes invisible. One page sat that way holding forty
thousand characters of translation and a duplicated half nobody could see.

Recognition is deliberately narrow, because promoting a sentence would cut the
section it sits in. A title carries a section number, opens its words in the
Latin script the source uses, and is short. A sentence that merely begins with a
number ("3.5 배의 속도 향상을 …") fails on the words that follow it.

Imports nothing but `re`, like `reference_section`, so a healer can never fail to
load because of it.
"""
import re

# A title is short. Measured against real pages, the longest runs to about a
# hundred characters once the translated form in parentheses is included.
MAX_TITLE_CHARS = 120

# `3.2.2 Multi-Head Attention …` — a section number, then a word that starts in
# the Latin script. The source's own titles are English, and the translation
# appends its Korean form in parentheses, so the first word after the number is
# the reliable part.
_TITLE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+([A-Za-z][A-Za-z0-9'\-]*)")

_HEADINGS = ("heading_1", "heading_2", "heading_3")


def text_of(block: dict) -> str:
    """Plain text of a block, whatever rich_text container it uses."""
    payload = block.get(block.get('type')) or {}
    if not isinstance(payload, dict):
        return ''
    return ''.join(
        span.get('plain_text', '') or span.get('text', {}).get('content', '')
        for span in payload.get('rich_text', []))


def heading_level(block: dict):
    """The heading level this paragraph should have, or None to leave it alone.

    Args:
        block: A Notion block, as the API returns it.

    Returns:
        1 for a top-level section and 3 for anything deeper — the levels the
        assembler uses when it gets this right — or None when the block is not a
        paragraph carrying a section title.
    """
    if block.get('type') != 'paragraph':
        return None
    text = text_of(block).strip()
    if not text or len(text) > MAX_TITLE_CHARS:
        return None
    found = _TITLE.match(text)
    if not found:
        return None
    return 1 if '.' not in found.group(1) else 3


# Three figures with no text at all is the signature of a page whose row was
# seen and whose translation never ran; fewer than that is just a fresh page.
MIN_ORPHAN_FIGURES = 3


# A title the journal set in bold and the translation flattened into the text:
# `Domain knowledge (도메인 지식) 이 시스템은 …`. What makes it recognisable is
# the translation's own convention — every heading on these pages is written
# `English Title (한글 제목)` — plus the space after the bracket. A term gloss in
# running prose has a Korean particle glued straight onto it ("(약어)는"), and a
# title does not.
MAX_RUN_IN_TITLE = 60

_RUN_IN = re.compile(
    r"^([A-Z][A-Za-z0-9 ,:'\-\u2013]{2,%d}?)\s*\(([^)]*[가-힣][^)]*)\)\s+(?=\S)"
    % MAX_RUN_IN_TITLE)


def split_run_in(block: dict):
    """A paragraph opening with a run-in title, as `(heading, paragraph)`.

    Returns None when the paragraph does not open with one — which includes
    every term gloss, because splitting one would cut a sentence in half.
    """
    if block.get('type') != 'paragraph':
        return None
    spans = (block.get('paragraph') or {}).get('rich_text') or []
    text = text_of(block)
    found = _RUN_IN.match(text.strip())
    if not found:
        return None
    # The offset is measured on the stripped text, so leading space is counted.
    lead = len(text) - len(text.lstrip())
    cut = lead + found.end()
    title_len = lead + len(found.group(0).rstrip())
    head = _slice_spans(spans, 0, title_len)
    body = _slice_spans(spans, cut, None)
    # A title is plain words. Anything else reaching into it — an equation, a
    # mention — means the phrase is part of a sentence rather than a heading.
    if any(span.get('type') != 'text' for span in head):
        return None
    if not head or not body:
        return None
    return ({'object': 'block', 'type': 'heading_3',
             'heading_3': {'rich_text': head}},
            {'object': 'block', 'type': 'paragraph',
             'paragraph': {'rich_text': body}})


def _slice_spans(spans: list, start: int, end):
    """The rich_text covering `[start, end)` characters, annotations kept."""
    out, seen = [], 0
    for span in spans:
        body = (span.get('text') or {}).get('content', '') or span.get(
            'plain_text', '')
        first, last = seen, seen + len(body)
        seen = last
        lo = max(first, start)
        hi = last if end is None else min(last, end)
        if lo >= hi:
            continue
        if span.get('type') != 'text':
            # An equation carries an expression, not characters, so it cannot be
            # cut — writing text into one is rejected by Notion outright. It
            # passes through whole. A cut that would land inside one always puts
            # it in the title, which the plain-text rule below then refuses.
            out.append(span)
            continue
        piece = body[lo - first:hi - first]
        out.append(dict(span,
                        text=dict(span.get('text') or {}, content=piece),
                        plain_text=piece))
    return out


def no_heading_finding(blocks: list):
    """What a page with no headings actually is: `(finding kind, block ids)`.

    Three states share the same appearance and need different answers. A page
    waiting for its translation is NORMAL and is reported quietly. A page with
    figures and no text at all was skipped by a dedup check and needs
    re-processing. And a page whose titles came out as paragraphs is fully
    translated — reporting THAT as untranslated is what let one sit for months
    with a duplicated half nobody could see, because every structural check
    reads headings and it had none.
    """
    demoted = [b.get('id') for b in blocks if heading_level(b) is not None]
    if demoted:
        return 'HEADINGS_AS_PARAGRAPHS', demoted
    figures = sum(1 for b in blocks if b.get('type') == 'image')
    if figures >= MIN_ORPHAN_FIGURES:
        return 'SKIPPED_TRANSLATION', []
    return 'NOT_TRANSLATED', []


def promote(block: dict):
    """The block as a heading, or None when it is not a section title.

    The spans are carried over untouched: this changes what the block IS, never
    what it says.
    """
    level = heading_level(block)
    if level is None:
        return None
    kind = 'heading_%d' % level
    spans = (block.get('paragraph') or {}).get('rich_text') or []
    return {'object': 'block', 'type': kind, kind: {'rich_text': spans}}


def heal_run_in(page_id: str, apply: bool = False) -> dict:
    """Split every run-in title on a page. Run by hand; see `main` below."""
    import time

    import reference_section
    import verify_sections as vs
    from translate_fulltext import notion

    blocks = vs.fetch_blocks(page_id)
    rep = {"page": page_id, "split": 0}
    for block in reference_section.body_blocks(blocks):
        pair = split_run_in(block)
        if pair is None:
            continue
        head, body = pair
        if text_of(head) + " " + text_of(body) != text_of(block).strip():
            raise ValueError(f"text would change in {block['id']}; refusing")
        rep["split"] += 1
        if apply:
            notion("PATCH", f"/blocks/{page_id}/children",
                   {"children": [head, body], "after": block["id"]})
            notion("PATCH", f"/blocks/{block['id']}", {"archived": True})
            time.sleep(0.45)
    return rep


def main() -> int:
    """Run by hand: journals differ, so a person checks the result."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(heal_run_in(args.page, apply=args.apply),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
