#!/usr/bin/env python3
"""Stores how far a reader has taken a paper, on the paper's own page.

The record sits after the injected reference list. That placement is the whole
reason it is cheap: `reference_section.body_blocks` already reports everything
past the bibliography as apparatus rather than body, so the structural audit,
the maths healer and back-matter stripping do not scan it, and the figure and
table injectors anchor before it.

Two things live there. What a person reads is one collapsible section per layer
they passed, holding their own restatement, a diagram and the evidence it rests
on. What the next session resumes from is a JSON block carrying the shape of the
descent: the tree, which branches are still open, which were closed and how, and
which node is current.

Structure is owned by that JSON. Text is owned by the page — a restatement
edited in Notion wins, because the record exists to hold the reader's sentences
and overwriting them would defeat it.

  descent.py --page <id> --state          # print the stored state as JSON
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reference_section  # noqa: E402  (path set above)

# Bumped when the stored shape changes in a way a reader cannot ignore.
STATE_VERSION = 1

HEADING = 'First principles'

# The state block is its own marker: a JSON code block carrying these keys is
# ours and nothing else on a paper page looks like it. Keying off the heading
# text instead would break the moment someone renames it, and people rename
# headings on pages they curate.
_STATE_KEYS = frozenset(('version', 'nodes'))


def _text_of(block: dict) -> str:
    """Plain text of a block, whatever rich_text container it uses."""
    payload = block.get(block.get('type')) or {}
    if not isinstance(payload, dict):
        return ''
    return ''.join(
        span.get('plain_text', '') or span.get('text', {}).get('content', '')
        for span in payload.get('rich_text', []))


def parse_state(blocks: list) -> dict | None:
    """The descent state stored on a page, or None if it carries no record.

    Args:
        blocks: Top-level blocks of the page, as the Notion API returns them.

    Returns:
        The stored state, or None when no state block is present or the one
        found does not parse. A page with no record is the normal case, so an
        unreadable block is reported the same way rather than raised: reading
        depth must never stop a caller that only wanted to answer a question.
    """
    for block in blocks:
        if block.get('type') != 'code':
            continue
        try:
            value = json.loads(_text_of(block))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and _STATE_KEYS <= value.keys():
            return value
    return None


def record_start(blocks: list) -> int | None:
    """Index of the first block of an existing record, or None.

    A rewrite has to replace what it wrote last time. A writer that cannot
    recognise its own output appends a second copy instead — which is how one
    page here accumulated the same tables every five minutes for days.

    The state block is the anchor because it is unmistakable; the record then
    begins at the heading that introduces it, or at the state block itself when
    the heading has been renamed away.

    Args:
        blocks: Top-level blocks of the page.

    Returns:
        The index the record starts at, or None when the page has no record.
    """
    anchor = None
    for i, block in enumerate(blocks):
        if block.get('type') != 'code':
            continue
        try:
            value = json.loads(_text_of(block))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and _STATE_KEYS <= value.keys():
            anchor = i
            break
    if anchor is None:
        return None
    for i in range(anchor, -1, -1):
        if _text_of(blocks[i]).strip() == HEADING:
            return i
    return anchor


def _paragraph(text: str) -> dict:
    return {
        'object': 'block',
        'type': 'paragraph',
        'paragraph': {
            'rich_text': [{'type': 'text', 'text': {'content': text[:2000]}}]
        },
    }


def _heading(text: str) -> dict:
    return {
        'object': 'block',
        'type': 'heading_1',
        'heading_1': {
            'rich_text': [{'type': 'text', 'text': {'content': text[:2000]}}]
        },
    }


def _state_block(state: dict) -> dict:
    return {
        'object': 'block',
        'type': 'code',
        'code': {
            'language': 'json',
            'rich_text': [{
                'type': 'text',
                'text': {'content': json.dumps(state, ensure_ascii=False,
                                               indent=1)},
            }],
        },
    }


def _code(text: str, language: str) -> dict:
    return {
        'object': 'block',
        'type': 'code',
        'code': {
            'language': language,
            'rich_text': [{'type': 'text', 'text': {'content': text[:2000]}}],
        },
    }


def _toggle(label: str, children: list) -> dict:
    return {
        'object': 'block',
        'type': 'toggle',
        'toggle': {
            'rich_text': [{'type': 'text', 'text': {'content': label[:2000]}}],
            'children': children,
        },
    }


def _bullet(text: str) -> dict:
    return {
        'object': 'block',
        'type': 'bulleted_list_item',
        'bulleted_list_item': {
            'rich_text': [{'type': 'text', 'text': {'content': text[:2000]}}]
        },
    }


def _evidence_line(node: dict) -> str:
    """One line naming both sides a layer rests on.

    Both, always: the page block is what the reader can click, and the source
    reference is what makes the layer checkable against the paper rather than
    against its translation.
    """
    evidence = node.get('evidence') or {}
    parts = []
    if evidence.get('page'):
        parts.append('페이지: %s' % evidence['page'])
    if evidence.get('source'):
        parts.append('원문: %s' % evidence['source'])
    return '근거 — ' + ' · '.join(parts) if parts else '근거 — 기록 없음'


def _layer_section(node: dict) -> dict:
    """A passed layer, collapsed under its own one-sentence thesis."""
    children = [_paragraph(node.get('restatement', ''))]
    if node.get('mermaid'):
        children.append(_code(node['mermaid'], 'mermaid'))
    children.append(_paragraph(_evidence_line(node)))
    return _toggle(node.get('thesis', ''), children)


def _frontier_line(node: dict) -> str:
    axis = node.get('axis')
    label = node.get('label', '')
    return '%s — %s' % (axis, label) if axis else label


def render_blocks(state: dict) -> list:
    """The blocks that carry this state on a page.

    Passed layers become collapsible sections in the order they were passed;
    branches still open are listed but given no section, because there is
    nothing of the reader's to show yet.

    Args:
        state: A descent state.

    Returns:
        Block payloads ready to append, opening with the record's heading and
        closing with the state itself.
    """
    blocks = [_heading(HEADING)]
    nodes = state.get('nodes', [])
    blocks += [_layer_section(n) for n in nodes if n.get('status') == 'passed']
    open_branches = [n for n in nodes if n.get('status') == 'frontier']
    if open_branches:
        blocks.append(_paragraph('아직 안 판 가지'))
        blocks += [_bullet(_frontier_line(n)) for n in open_branches]
    blocks.append(_state_block(state))
    return blocks


# A page whose translation has not arrived cannot be descended: the layers
# would rest on nothing. Measured against real pages, a translated body runs to
# tens of thousands of characters, so this floor only catches the empty case.
MIN_BODY_CHARS = 1000

_BATCH = 90


def write(page_id: str, state: dict, expect_title: str,
          apply: bool = False) -> dict:
    """Replace the record on a page with the one this state describes.

    Args:
        page_id: The paper page to write to.
        state: The descent state to store.
        expect_title: A distinctive fragment of the target paper's title. The
          write is refused unless the page carries it, which is the same guard
          the Q&A writer uses against a stale page id.
        apply: Write when true; otherwise report what would happen.

    Returns:
        A report carrying what was replaced and written, or the reason the
        write was refused.
    """
    import time

    import translate_fulltext
    import verify_sections

    report = {'page': page_id, 'archived': 0, 'written': 0}
    page = translate_fulltext.notion('GET', '/pages/%s' % page_id)
    title = ''
    for prop in (page.get('properties') or {}).values():
        if prop.get('type') == 'title':
            title = ''.join(t['plain_text'] for t in prop['title'])
    url = ''
    for prop in (page.get('properties') or {}).values():
        if prop.get('type') == 'url' and prop.get('url'):
            url = prop['url']
    if expect_title.lower() not in (title + ' ' + url).lower():
        report['refused'] = 'page title does not carry %r' % expect_title
        return report

    blocks = verify_sections.fetch_blocks(page_id)
    body = reference_section.body_blocks(blocks)
    chars = sum(len(_text_of(b)) for b in body if b.get('type') != 'image')
    if chars < MIN_BODY_CHARS:
        report['refused'] = 'page carries no translation (%d chars)' % chars
        return report

    start = record_start(blocks)
    doomed = blocks[start:] if start is not None else []
    new_blocks = render_blocks(state)
    report['archived'] = len(doomed)
    report['written'] = len(new_blocks)
    if not apply:
        return report

    for block in doomed:
        translate_fulltext.notion('PATCH', '/blocks/%s' % block['id'],
                                  {'archived': True})
        time.sleep(0.2)
    for i in range(0, len(new_blocks), _BATCH):
        translate_fulltext.notion('PATCH', '/blocks/%s/children' % page_id,
                                  {'children': new_blocks[i:i + _BATCH]})
        time.sleep(0.4)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--page', required=True)
    parser.add_argument('--state', action='store_true',
                        help='print the stored state and exit')
    parser.add_argument('--write', metavar='FILE',
                        help='replace the record with the state in FILE')
    parser.add_argument('--expect-title',
                        help='distinctive fragment of the paper title')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    import verify_sections  # Imported here: it reaches the network on import.

    if args.state:
        state = parse_state(verify_sections.fetch_blocks(args.page))
        print(json.dumps(state, ensure_ascii=False, indent=1)
              if state else 'null')
        return 0
    if args.write:
        if not args.expect_title:
            parser.error('--write requires --expect-title')
        with open(args.write, encoding='utf-8') as handle:
            state = json.load(handle)
        report = write(args.page, state, args.expect_title,
                       apply=not args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if report.get('refused') else 0
    parser.error('nothing to do: pass --state or --write')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
