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
