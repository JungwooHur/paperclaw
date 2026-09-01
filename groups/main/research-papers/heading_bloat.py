#!/usr/bin/env python3
"""Splits a heading that swallowed the paragraph belonging under it.

The assembler sometimes emits a run-in subsection title together with its whole
body as ONE heading block. The visible symptom is a wall of large bold text, but
the expensive part is invisible: with the body inside the heading, that section's
heading text no longer matches the same section's other copy, so
`heal_verify.dedupe_duplicates` — which groups sections by heading — cannot see
that the section was uploaded twice. A page can therefore carry a duplicated run
of sections forever while every healer reports it clean.

Splitting is deliberately narrow. It only acts on a run-in title, recognised by a
colon near the start, because that is the shape the assembler produces from
LaTeX's own run-in headings. Anything else is left whole and left reported: a
wrong cut would rewrite the reader's text, and this module has no way to tell a
title from a sentence that merely happens to be long.

This module imports nothing but the standard library, like `reference_section`,
so a healer can never fail to load because of it.
"""

# Must match the threshold `verify_sections` uses to report HEADING_BLOAT; a
# heading this code declines to split still has to be reported by the audit.
MAX_HEADING_CHARS = 160

# How far into the text a run-in title's colon may sit. A colon past this is
# punctuation inside a sentence, not the end of a title.
MAX_TITLE_CHARS = 120

_SEPARATOR = ': '


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


def title_length(text: str) -> int | None:
    """Characters of the run-in title, colon included, or None if there is none.

    Returns:
        The length of the leading title, or None when the text carries no colon
        close enough to its start to be one.
    """
    at = text.find(_SEPARATOR)
    if at < 0 or at >= MAX_TITLE_CHARS:
        return None
    return at + 1


def is_bloated(block: dict) -> bool:
    """Is this block a heading carrying more than a heading's worth of text?

    The audit reports what this returns and the repair below acts on it, so the
    two can never drift apart into flagging one set and fixing another.
    """
    kind = block.get('type', '')
    return kind.startswith('heading') and len(text_of(block)) > MAX_HEADING_CHARS


def split_spans(spans: list, offset: int):
    """Cut a rich_text list at a character offset, keeping every annotation.

    Args:
        spans: The block's rich_text.
        offset: How many characters belong to the first part.

    Returns:
        A `(head, tail)` pair of span lists, or None when the cut would fall
        inside a span that has no characters to cut at — an equation carries a
        LaTeX expression, not prose, and splitting one would have to invent it.
    """
    head, tail, seen = [], [], 0
    for span in spans:
        body = _span_text(span)
        start, end = seen, seen + len(body)
        seen = end
        if end <= offset:
            head.append(span)
            continue
        if start >= offset:
            tail.append(span)
            continue
        if span.get('type') != 'text':
            return None
        cut = offset - start
        head.append(_retext(span, body[:cut]))
        tail.append(_retext(span, body[cut:]))
    return head, tail


def _retext(span: dict, content: str) -> dict:
    """A copy of a text span carrying different characters."""
    new = dict(span)
    new['text'] = dict(span.get('text') or {}, content=content)
    new['plain_text'] = content
    return new


def split_block(block: dict):
    """A bloated heading as its title plus the paragraph it swallowed.

    Args:
        block: A Notion block, as the API returns it.

    Returns:
        A `(heading, paragraph)` pair of new block payloads, or None when the
        block is not a bloated heading or cannot be split safely. None is a
        complete answer: the audit keeps reporting the block, and nothing of the
        reader's text has been guessed at.
    """
    if not is_bloated(block):
        return None
    kind = block['type']
    length = title_length(text_of(block))
    if length is None:
        return None
    spans = (block.get(kind) or {}).get('rich_text') or []
    cut = split_spans(spans, length)
    if cut is None:
        return None
    head, tail = cut
    tail = _lstrip_spans(tail)
    if not head or not tail:
        return None
    return ({'object': 'block', 'type': kind, kind: {'rich_text': head}},
            {'object': 'block', 'type': 'paragraph',
             'paragraph': {'rich_text': tail}})


def _lstrip_spans(spans: list) -> list:
    """Drop the space that separated the title from its body."""
    out = list(spans)
    while out:
        body = _span_text(out[0])
        if out[0].get('type') != 'text' or body.strip():
            break
        out.pop(0)
    if out and out[0].get('type') == 'text':
        body = _span_text(out[0])
        out[0] = _retext(out[0], body.lstrip())
    return out
