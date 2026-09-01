#!/usr/bin/env python3
"""Removes the markdown bullet a list item kept from its source.

Notion draws the bullet for a list block itself, so a `•` still sitting in the
text renders a second one beside it. The assembler leaves one behind whenever it
converts a markdown list without stripping the marker, and the result reads as a
doubled or mismatched bullet on every line of the list.

Only list blocks are touched. The same characters at the start of a paragraph
are ordinary punctuation — a dash opening a sentence, a bullet the author typed
— and rewriting those would change what the page says.

Like `reference_section`, this module imports nothing but `re`, so a healer can
never fail to load because of it.
"""
import re

# A leftover marker: a bullet glyph, or a dash used as one. A dash followed by a
# digit is arithmetic ("- 3 dB 감소"), and stripping it would silently change the
# number's sign, so the dash forms require a non-digit after the space.
_MARKER = re.compile(r'^\s*(?:[•‣·*]\s+|[-–—]\s+(?=[^\s\d]))')

_LIST_TYPES = ('bulleted_list_item', 'numbered_list_item')


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


def has_marker(block: dict) -> bool:
    """Does this list item still carry the marker its source wrote?"""
    if block.get('type') not in _LIST_TYPES:
        return False
    return bool(_MARKER.match(text_of(block)))


def strip_marker(block: dict):
    """The block with its leading marker removed, or None if it has none.

    Args:
        block: A Notion block, as the API returns it.

    Returns:
        A new block payload whose first span has lost the marker, keeping every
        annotation, link and equation after it. None when there is nothing to
        remove, so a caller can skip the write entirely.
    """
    if not has_marker(block):
        return None
    kind = block['type']
    spans = list((block.get(kind) or {}).get('rich_text') or [])
    if not spans or spans[0].get('type') != 'text':
        return None
    head = _span_text(spans[0])
    cut = _MARKER.match(head)
    if cut is None:                # the marker straddles spans; leave it whole
        return None
    body = head[cut.end():]
    spans[0] = dict(spans[0],
                    text=dict(spans[0].get('text') or {}, content=body),
                    plain_text=body)
    if not body:
        spans.pop(0)
    return {'object': 'block', 'type': kind, kind: {'rich_text': spans}}
