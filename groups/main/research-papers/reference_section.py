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

# An entry opens with the label the paper cites it by. Numeric styles write
# `[7]`; author-initials styles write `[ACDE12]` or `[VSP + 17]`. Both have to
# be recognised, because everything downstream depends on this boundary: a
# bibliography it cannot see is body, and back-matter stripping cuts from the
# References heading to the end of the page.
#
# A label either carries a number — a year or an index, possibly with a
# disambiguating letter after it (`[RRBS19a]`) — or is a short bare tag for a
# corporate author (`[Fou]`). Requiring one of those keeps an ordinary
# sentence that opens with a bracketed aside ("[see below] for the
# derivation") from being read as an entry, which is the false positive that
# would move the boundary into the body.
# A label is SHORT and identifies one work. Three styles are in use: a number
# (`[7]`), author initials with a year (`[ACDE12]`, `[DGV+18]`), and author names
# with a year (`[Agrawal et al. (2016)]`). The last one broke this: it carries
# dots, parentheses and ampersands, so the boundary could not see a list written
# in that style — and the healer, unable to recognise the list it had just
# written, appended a fresh copy every five minutes until one page held
# thirty-seven copies of its own bibliography and nothing else.
#
# So the label may hold anything but a bracket, and is pinned down by two rules
# instead: it is short, and it carries a digit — every style ends its label with
# a year or an index. A corporate tag with no digit (`[Fou]`) is allowed only
# when it is a single short word, which keeps a bracketed clause of prose out.
MAX_LABEL_CHARS = 40
_LABEL = r"\[([^\[\]\n]{1,%d})\]" % MAX_LABEL_CHARS
_ENTRY = re.compile(r"\s*" + _LABEL + r"\s")
_BARE_TAG = re.compile(r"^[^\W\d_]{1,6}$", re.UNICODE)
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
    """Does this block open with a reference label, in English?

    A translated bibliography is what the healers exist to remove; only the
    injected English one is protected, so Korean anywhere in the block rules it
    out.
    """
    text = block_text(block)
    found = _ENTRY.match(text)
    if not found or _KOREAN.search(text):
        return False
    label = found.group(1).strip()
    return any(c.isdigit() for c in label) or bool(_BARE_TAG.match(label))


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
        # Only up to the NEXT heading. Collecting every paragraph to the end of the
        # page made any heading that merely PRECEDES the bibliography match it: on
        # one page the last appendix heading was reported as the start of the
        # reference list, so "everything after it" swept in that appendix's body.
        run = []
        for x in blocks[i + 1:]:
            if str(x.get("type", "")).startswith("heading"):
                break
            if x.get("type") == "paragraph":
                run.append(x)
        if looks_like_list(run):
            return i
    return None


def body_blocks(blocks: list) -> list:
    """The page WITHOUT its injected reference list."""
    i = start_index(blocks)
    return blocks if i is None else blocks[:i]


def body_end_anchor(blocks: list):
    """Block id to append AFTER so new content lands at the end of the BODY.

    Appending to a page's children with no `after` puts the block at the very end
    — which, once the reference list is there, is *below the bibliography*. Four
    tables shipped that way on one page: the healer injected the references and
    the tables in the same cycle, and every table whose number the body never
    mentions fell past them. Returns None when the page has no reference list, so
    the caller keeps its old plain-append behaviour.
    """
    i = start_index(blocks)
    return blocks[i - 1]["id"] if i else None
