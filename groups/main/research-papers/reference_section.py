#!/usr/bin/env python3
r"""Where the injected English reference list begins — the one thing every healer
must agree on.

`link_references.py` appends the source's own bibliography, verbatim in English,
at the end of the page, and the body's citation links point INTO it. To every
other healer that section looks like exactly what they exist to destroy or
rewrite:

  * `strip_backmatter` sees a `References` heading at the tail and archives it
    together with everything after it.
  * `verify_sections` reports BACKMATTER ("a back-matter section was translated
    into the body — remove it") and, because entries carry real LaTeX
    (`\pi_{0.6} model card`), BARE_MATH.
  * `wrap_math` then acts on that BARE_MATH and rewrites the English entries.

Each of those is right about a TRANSLATED bibliography and wrong about this one,
so the boundary is defined once, here, and imported everywhere. This module
deliberately has no dependencies beyond `re`: it is imported by healers that must
never fail to load because something further down the chain is broken.

Identification is by BODY, not by title. A page can legitimately carry a heading
called "References"; what it cannot carry by accident is a run of paragraphs that
all open with `[N] ` and contain no Korean. A translated bibliography — the thing
the other healers target — is full of Korean.
"""
import re

_ENTRY = re.compile(r"\s*\[\d+\]\s")
_KOREAN = re.compile(r"[가-힣]")
MIN_ENTRIES = 3
ENTRY_SHARE = 0.8


def block_text(block: dict) -> str:
    """Plain text of any Notion block (present on every rich_text span type)."""
    payload = block.get(block.get("type"), {})
    if not isinstance(payload, dict):
        return ""
    return "".join(s.get("plain_text", "") for s in payload.get("rich_text", []))


def is_entry(block: dict) -> bool:
    text = block_text(block)
    return bool(_ENTRY.match(text)) and not _KOREAN.search(text)


def looks_like_list(paragraphs: list) -> bool:
    if len(paragraphs) < MIN_ENTRIES:
        return False
    return sum(1 for p in paragraphs if is_entry(p)) >= ENTRY_SHARE * len(paragraphs)


def start_index(blocks: list):
    """Index of the heading that opens the injected list, or None.

    Returns the HEADING's index, so `blocks[:start]` is the paper and
    `blocks[start:]` is the reference apparatus.
    """
    for i, b in enumerate(blocks):
        if not str(b.get("type", "")).startswith("heading"):
            continue
        paras = [x for x in blocks[i + 1:] if x.get("type") == "paragraph"]
        if looks_like_list(paras):
            return i
    return None


def body_blocks(blocks: list) -> list:
    """The page WITHOUT its injected reference list."""
    i = start_index(blocks)
    return blocks if i is None else blocks[:i]
