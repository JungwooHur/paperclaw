#!/usr/bin/env python3
"""Reads a bibliography from a Nature article page.

A Nature paper has no arXiv source, so the citation pipeline — which parses
LaTeXML HTML — has nothing to work with, and such a page ends up with no
reference list at all while its body is full of `[N]` markers pointing nowhere.

Nature publishes the bibliography even where the full text is paywalled, which
is enough to inject the list and link those markers.

It is NOT enough for alignment. The article page carries no in-body citation
anchors, so there is nothing to check the page's numbering against. This source
is therefore used only where the numbering is the paper's own — the normal case
for a translation made from the paper itself — and a sample is worth checking by
hand before trusting a new one.

Imports nothing but the standard library.
"""
import html as _html
import re

import link_references as lr

# Nature numbers each entry in the list item and puts the text in a paragraph of
# its own: <li … data-counter="11"><p … id="ref-CR11">Kocsis, L. &amp; …</p></li>
_ITEM = re.compile(
    r'data-counter="(\d+)"\s*>\s*<p[^>]*>(.*?)</p>', re.S | re.I)

_ARTICLE = re.compile(r'^https?://(?:www\.)?nature\.com/articles/[\w.-]+', re.I)


def article_url(url: str):
    """The Nature article URL in `url`, or None if it is not one."""
    found = _ARTICLE.match((url or '').strip())
    return found.group(0) if found else None


def _plain(markup: str) -> str:
    """The text of an entry: tags dropped, entities decoded, spacing tidied."""
    return ' '.join(_html.unescape(re.sub(r'<[^>]+>', ' ', markup)).split())


def parse_bibliography(page_html: str) -> list:
    """[{num, label, text}] for every entry, in the order the paper numbers them.

    The shape matches `link_references.parse_bibliography` so the same injector
    and linker can be used unchanged.
    """
    out = []
    for number, markup in _ITEM.findall(page_html or ''):
        text = _plain(markup)
        if text:
            out.append({'num': int(number), 'id_num': int(number),
                        'label': number, 'text': text})
    out.sort(key=lambda entry: entry['num'])
    return out


def identity_mapping(text: str, entries: list) -> list:
    """The entry numbers a block's citation markers name, in reading order.

    There is no source body to align against here, so the mapping is the
    identity one: `[7]` means entry 7. A number no entry claims is dropped —
    linking it would point at nothing, and leaving it as plain text says so.
    """
    known = {entry["num"] for entry in entries}
    out = []
    for found in lr._CITE.finditer(text or ""):
        for num in lr.expand(found.group(1)):
            if isinstance(num, int) and num in known:
                out.append(num)
    return out


def link_page(page_id: str, url: str, apply: bool = False) -> dict:
    """Inject the article's bibliography and link the body's `[N]` markers.

    Refuses to append a second list, through the same heading-only guard the
    arXiv path uses — nothing here parses entries to decide that, so no future
    label style can make it run away.
    """
    import time
    import urllib.request

    import verify_sections as vs
    from translate_fulltext import notion

    article = article_url(url)
    rep = {"page": page_id, "source": article, "entries": 0,
           "refs": "kept", "slots_linked": 0}
    if not article:
        rep["error"] = "not a Nature article URL"
        return rep
    request = urllib.request.Request(
        article, headers={"User-Agent": "Mozilla/5.0"})
    entries = parse_bibliography(
        urllib.request.urlopen(request, timeout=90).read().decode("utf-8",
                                                                  "replace"))
    rep["entries"] = len(entries)
    if not entries:
        rep["error"] = "no bibliography on the article page"
        return rep

    blocks = vs.fetch_blocks(page_id)
    if not lr.may_inject_references(blocks):
        rep["refs"] = "already present"
        return rep
    ref_ids = lr.inject_references(page_id, entries, apply)
    rep["refs"] = f"{'injected' if apply else 'would inject'} {len(entries)}"
    if not apply or not ref_ids:
        return rep

    texts = {entry["num"]: entry["text"] for entry in entries}
    for block in lr.body_blocks_of(vs.fetch_blocks(page_id)):
        kind = block["type"]
        spans = (block.get(kind) or {}).get("rich_text")
        if not spans:
            continue
        mapping = identity_mapping(lr.block_text(block), entries)
        if not mapping:
            continue
        rich = lr._rewrite_block(block, list(mapping), page_id, ref_ids, texts)
        lr.check_prose_only(spans, rich, block["id"])
        notion("PATCH", f"/blocks/{block['id']}", {kind: {"rich_text": rich}})
        rep["slots_linked"] += len(mapping)
        time.sleep(0.2)
    return rep
