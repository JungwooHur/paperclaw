#!/usr/bin/env python3
"""Turns markdown emphasis left as characters into an actual italic span.

The assembler sometimes writes `*중요*` as text rather than applying it, so the
asterisks appear on the page. Converting them looks trivial and is not: `*` is
also a maths character, and an unbalanced pair means the block was mangled
rather than emphasised. A wrong conversion rewrites a formula or eats a
sentence, while leaving the asterisks alone merely looks untidy — so every
ambiguous case is refused.

Conversion happens only when all of these hold:

  * exactly one balanced single-asterisk pair in the block,
  * both markers inside the SAME text span, and
  * nothing between them that belongs to maths.

Imports nothing but `re`, like `reference_section`, so a healer can never fail
to load because of it.
"""
import re

# Characters that mean the pair is inside a formula rather than around a phrase.
_MATHS = set('\\${}^_')

# A single-asterisk pair. `**` is bold and is written by a different path, so it
# is excluded on both sides rather than half-matched.
_PAIR = re.compile(r'(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)')

_EMPHASISABLE = ('paragraph', 'quote', 'bulleted_list_item',
                 'numbered_list_item', 'heading_1', 'heading_2', 'heading_3')


def text_of(block: dict) -> str:
    """Plain text of a block, whatever rich_text container it uses."""
    payload = block.get(block.get('type')) or {}
    if not isinstance(payload, dict):
        return ''
    return ''.join(_span_text(s) for s in payload.get('rich_text', []))


def _span_text(span: dict) -> str:
    if span.get('type') == 'equation':
        return span.get('plain_text') or (
            span.get('equation') or {}).get('expression', '')
    return span.get('plain_text', '') or (
        span.get('text') or {}).get('content', '')


def convert(block: dict):
    """The block with its leftover emphasis applied, or None to leave it alone.

    Args:
        block: A Notion block, as the API returns it.

    Returns:
        A new block payload whose asterisks have become an italic span, or None
        when the block carries no emphasis or carries one this cannot convert
        safely. None is a complete answer: the audit keeps reporting it.
    """
    kind = block.get('type', '')
    if kind not in _EMPHASISABLE:
        return None
    whole = text_of(block)
    if whole.count('*') != 2:      # zero, unbalanced, bold, or more than one pair
        return None
    spans = list((block.get(kind) or {}).get('rich_text') or [])
    for i, span in enumerate(spans):
        if span.get('type') != 'text':
            continue
        body = _span_text(span)
        found = _PAIR.search(body)
        if found is None:
            continue
        if set(found.group(1)) & _MATHS:
            return None
        parts = [body[:found.start()], found.group(1), body[found.end():]]
        made = []
        for at, piece in enumerate(parts):
            if not piece:
                continue
            new = dict(span,
                       text=dict(span.get('text') or {}, content=piece),
                       plain_text=piece)
            new['annotations'] = dict(span.get('annotations') or {},
                                      italic=(at == 1))
            made.append(new)
        rebuilt = spans[:i] + made + spans[i + 1:]
        return {'object': 'block', 'type': kind, kind: {'rich_text': rebuilt}}
    return None                    # the pair straddles spans; not ours to join
