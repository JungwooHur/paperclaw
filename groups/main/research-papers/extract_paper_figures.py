#!/usr/bin/env python3
r"""Extract an arxiv paper's figures and inject them into its Notion page.

Why this exists
---------------
Figure extraction/injection was the ONE part of the paper workflow with no
structural backstop — Phase 3 was pasted-prose the agent had to copy-run, and it
skipped it (the recurring "prose isn't load-bearing" pattern: most recently
processed papers ended up with 0 figures even though the source HTML has them).
Everything else (back-matter, source URLs, math, Q&A, citations) is enforced by a
script + healer + verify check; figures now are too.

Deterministic placement
------------------------
arxiv-native / ar5iv LaTeXML gives every figure `<figure id="S3.F2">` where the
`F<m>` is the figure NUMBER. The translated Notion body references each figure as
`그림 <m>` / `Figure <m>` / `Fig. <m>` (NotebookLM keeps figure refs), and section
headings keep their number (`4 GAM`, `부록 A`). So each figure is inserted right
after the paragraph that first mentions its number; fallback to the heading of
the section the figure sits in (the `S3` of `S3.F2`); fallback to the page end.
No NotebookLM round-trip needed.

Idempotent: if the page already has image blocks it is left alone (unless --force).
Figures are uploaded PRIVATELY into Notion via notion_upload (never a public host).

  extract_paper_figures.py --page <id> --arxiv <id> [--dry-run] [--force]
"""
import argparse
import html as _html
import os
import re
import sys
import urllib.request
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reference_section  # noqa: E402

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_FIG = re.compile(r'<figure[^>]*\bid="([^"]+)"[^>]*>(.*?)</figure>', re.DOTALL | re.I)
_IMG = re.compile(r'<img[^>]+\bsrc="([^"]+)"', re.I)
_CAP = re.compile(r'<figcaption[^>]*>(.*?)</figcaption>', re.DOTALL | re.I)
_TAG = re.compile(r'<[^>]+>')
_FNUM = re.compile(r'F(\d+)')

TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "quote",
              "bulleted_list_item", "numbered_list_item", "callout")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub("", s))).strip()


def html_renders(arxiv_id: str):
    """Yield (html, source_url) for each HTML render of the paper that answers.

    arxiv-native first (latest version), ar5iv fallback (often stale v1 — see
    the ar5iv note in CLAUDE.md). Lazy, so a caller that stops at the first
    render it can use fetches no more than that.
    """
    for url in (f"https://arxiv.org/html/{arxiv_id}",
                f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read().decode("utf-8", "replace")
                final = r.geturl()
        except Exception:
            continue
        yield data, final


def fetch_html(arxiv_id: str):
    """Return (html, source_url) of the first render with a figure in it."""
    for data, final in html_renders(arxiv_id):
        if "<figure" in data:
            return data, final
    return None, None


_CAP_OWN = re.compile(r"^\s*(?:fig\.?|figure|그림)\s*[0-9A-Z]", re.I)


def _figure_caption(body: str) -> str:
    """The figure's OWN caption, not a sub-panel's.

    A composite figure nests one `<figcaption>` per panel — "(a) Example
    rollouts." — and the figure's real caption, the one that carries its NUMBER,
    comes after them. Taking the first match therefore captioned the whole figure
    with a panel label, and since the number lives in that caption, the figure lost
    its number: on one page three figures appeared with no "Fig. N" at all, and one
    of them was additionally mislabelled from its id because the caption fallback
    had nothing to read. Prefer the caption that opens with a figure label; fall
    back to the first, which is right for a simple figure.
    """
    caps = [_clean(c) for c in _CAP.findall(body)]
    for c in caps:
        if _CAP_OWN.match(c):
            return c
    return caps[0] if caps else ""


def parse_figures(html_text: str, source_url: str) -> list:
    """Ordered list of {id, num, img_url, caption[, section]}, one per figure
    image, deduped by image URL (composite figures expose one img per
    subfigure); `section` as `enclosing_section` gives it."""
    # Resolve relative img srcs against the page's <base href> if present. Some
    # arxiv HTML pages set `<base href="/html/<id>vN/">` and give srcs relative to
    # it (`images/x.jpg`); urljoin against the page URL alone would drop the version
    # dir and 404. Others have no <base> and srcs already include the version dir.
    bm = re.search(r'<base[^>]+href=["\']?([^"\'>\s]+)', html_text, re.I)
    base = urljoin(source_url, _html.unescape(bm.group(1))) if bm else source_url
    out, seen, no_image = [], set(), []
    sections = source_sections(html_text)
    for fid, body in _iter_figures(html_text):
        imgs = _IMG.findall(body)
        img = _IMG.search(body)
        if not img:
            # A <figure> with a caption but no <img>: the converter rendered it as
            # markup rather than an image (common on ar5iv when arxiv-native 404s).
            # Recording it matters — this is how a page silently lost its Figure 1
            # while an appendix figure sat in that slot, and nothing reported it.
            if ".T" in fid:
                continue        # a <figure class="ltx_table"> — extract_paper_tables owns it
            no_image.append(figure_label(fid, _figure_caption(body)) or fid)
            continue
        url = urljoin(base, _html.unescape(img.group(1)))
        if url in seen:
            continue
        seen.add(url)
        caption = _figure_caption(body)[:1900]
        fig = {"id": fid, "num": figure_label(fid, caption), "img_url": url,
               "caption": caption}
        section = enclosing_section(sections, html_text.find(f'id="{fid}"'))
        if section:
            fig["section"] = section
        out.append(fig)
    parse_figures.no_image = no_image     # read by inject_figures for its report
    return out


_FIG_OPEN = re.compile(r'<figure[^>]*\bid="([^"]+)"[^>]*>', re.I)
_SUBFIG = re.compile(r"\.sf\d+$", re.I)


def _iter_figures(html_text: str):
    """(id, body) per figure, with the body spanning NESTED figures too.

    The old `<figure[^>]*>(.*?)</figure>` stopped at the FIRST `</figure>`, so a
    composite figure — one that wraps its panels in their own `<figure>` elements —
    was truncated to its first panel. The panels were then ALSO yielded on their own,
    each carrying a sub-caption like "(a) Table Bussing" instead of a figure number,
    so one composite figure arrived as six unanchored images that piled up at the end
    of the page. extract_paper_tables already had to stop using this pattern for
    exactly the same reason.

    Sub-figures are skipped: a panel is part of its parent, never a figure of its own.
    """
    for m in _FIG_OPEN.finditer(html_text):
        fid = m.group(1)
        if _SUBFIG.search(fid):
            continue
        # walk forward keeping the figure nesting depth, so the body ends at the
        # </figure> that closes THIS element
        depth, i = 1, m.end()
        while depth and i < len(html_text):
            nxt_open = html_text.find("<figure", i)
            nxt_close = html_text.find("</figure", i)
            if nxt_close == -1:
                break
            if nxt_open != -1 and nxt_open < nxt_close:
                depth += 1
                i = nxt_open + 7
            else:
                depth -= 1
                i = nxt_close + 8
        yield fid, html_text[m.end():max(m.end(), i - 9)]


def _has_our_caption(block: dict) -> bool:
    """True if this injector wrote the image (it always captions "Figure N")."""
    cap = "".join(c.get("plain_text", "") for c in
                  ((block.get("image") or {}).get("caption") or []))
    # A "(a) Table Bussing" sub-caption is a figure PANEL this pipeline emitted
    # before composite figures were handled — ours, and replaceable. Without this
    # those leftovers read as manual inserts and survive every --force forever.
    return bool(re.match(r"\s*(?:figure|fig\.?|그림)\s*\S", cap, re.I)
                or re.match(r"\s*\([a-z]\)\s+\S", cap, re.I))


def figure_label(fid: str, caption: str = "") -> str | None:
    r"""The figure's printed label: "3" for a body figure, "F.2" for an appendix one.

    The id alone is not enough. LaTeXML numbers appendix figures inside their own
    appendix (`A6.F2` is Figure F.2), and the old parser read `F(\d+)` out of the id
    and threw the appendix away — so `A1.F1` and `S1.F1` both became 1. Placement
    then anchored on the first mention of "Figure 1", and the appendix figure landed
    in the body next to (or instead of) the real one. On one paper this displaced
    five figures and put an appendix figure where Figure 1 belongs.

    The caption states the label outright ("Figure F.2: ..."), so trust it first and
    fall back to deriving it from the id (`A<k>` is the k-th appendix, i.e. letter k).
    """
    m = re.match(r"\s*(?:Figure|Fig\.?|그림)\s*([A-Za-z]?\.?\s?\d+(?:\.\d+)?)\s*[:.]",
                 caption or "")
    if m:
        return re.sub(r"\s+", "", m.group(1)).strip(".") if "." in m.group(1) \
            else m.group(1).strip()
    m = re.match(r"(?:S(\d+)|A(\d+))\.F(\d+)", fid or "")
    if m:
        if m.group(2):                       # appendix: A1 -> "A", A6 -> "F"
            idx = int(m.group(2))
            if 1 <= idx <= 26:
                return f"{chr(ord('A') + idx - 1)}.{m.group(3)}"
        return m.group(3)
    m = _FNUM.search(fid or "")
    return m.group(1) if m else None


def _block_text(b: dict) -> str:
    t = b["type"]
    return "".join(x.get("plain_text", "")
                   for x in (b.get(t) or {}).get("rich_text", []))


# A block that OPENS with a float's own label is that float's caption. The
# separator is required here — on the page a caption is written `Figure 1: …`,
# while a body sentence runs straight on ("Figure 13에서 우리는 …") and is exactly
# the mention a figure should be anchored to.
_CAPTION_OPENER = re.compile(
    r"^\s*(그림|Figure|Fig\.?|표|Table)\s*0*(\d+)\s*[:.]", re.I)


def caption_number(text: str):
    """`(kind, number)` if this text opens as a float's caption, else None.

    Captions cross-reference each other — one figure's caption saying "compare
    with Figure 13" is a mention of 13, and it is the FIRST one on the page. The
    anchor rule then put Figure 13 directly under Figure 1, chapters from the
    text that discusses it. Knowing which block is whose caption is what lets
    the scan step over that.
    """
    found = _CAPTION_OPENER.match(text or "")
    if not found:
        return None
    word = found.group(1).lower()
    kind = "table" if word in ("표", "table") else "figure"
    return kind, int(found.group(2))


def _is_other_caption(text: str, kind: str, num) -> bool:
    """Is this block the caption of a DIFFERENT float than the one being placed?"""
    own = caption_number(text)
    if own is None:
        return False
    return own != (kind, num if isinstance(num, int) else _as_int(num))


def _as_int(num):
    try:
        return int(str(num))
    except (TypeError, ValueError):
        return None


def _series_key(num):
    """(series letter, index) for a figure number, so `A.1` and `1` stay apart."""
    found = re.match(r"([A-Za-z]*)\.?(\d+)", str(num or ""))
    return (found.group(1) or "", int(found.group(2))) if found else ("", 0)


def fill_anchor_gaps(nums: list, resolved: dict) -> dict:
    """Give every figure the body never cites a place beside its neighbours.

    A figure's anchor is the first mention of its number. One the text never
    cites — a teaser, or one whose citation the translation dropped — has none,
    and falling to the end of the page puts it in the appendix while the figures
    around it sit chapters earlier.

    It joins the nearest placed figure BEFORE it, and when there is none — which
    is exactly the case of a teaser numbered 1 — the nearest placed figure AFTER
    it instead. Sharing an anchor is enough to order them correctly, because
    figures are written out in numeric order within an anchor.

    Args:
        nums: Every figure number, in the order they should appear.
        resolved: {number: anchor block id or None}.

    Returns:
        A new mapping with the gaps filled where a neighbour exists. A figure
        with no placed neighbour at all keeps None — the page end stays the last
        resort, and inventing a position would be worse than admitting there is
        none.
    """
    ordered = sorted(nums, key=_series_key)
    filled = dict(resolved)
    for i, num in enumerate(ordered):
        if filled.get(num):
            continue
        series = _series_key(num)[0]
        before = [n for n in ordered[:i]
                  if filled.get(n) and _series_key(n)[0] == series]
        if before:
            filled[num] = filled[before[-1]]
            continue
        after = [n for n in ordered[i + 1:]
                 if resolved.get(n) and _series_key(n)[0] == series]
        if after:
            filled[num] = resolved[after[0]]
    return filled


def neighbour_spot(num, placed: dict):
    """Where a figure belongs among the ones already on the page, or None.

    When only the missing figures are being injected, the ones already placed are
    the only neighbours there are — looking at the batch alone leaves a single
    uncited figure with nobody to sit beside, and it falls to the end of the
    page, which is the appendix.

    Args:
        num: The figure number being placed.
        placed: {figure number: index of its image block on the page}.

    Returns:
        `(index, "after")` for the nearest lower-numbered figure, or
        `(index, "before")` for the nearest higher one when nothing is lower —
        which is the case of a teaser numbered 1. None when the number is
        already placed, or nothing is.
    """
    if num in placed or not placed:
        return None
    lower = [n for n in placed if n < num]
    if lower:
        return placed[max(lower)], "after"
    higher = [n for n in placed if n > num]
    return (placed[min(higher)], "before") if higher else None


# The words a body uses to name a float: (Latin, Hangul). Each may not continue a
# word of its own script, or "config 2" would mention Figure 2, "suitable 2" Table
# 2, and 목표 1 ("goal 1") Table 1. The plural opens a list ("Tables 2 and 3").
_FLOAT_WORDS = {"figure": (r"(?:Figures?|Figs?\.?)", r"그림"),
                "table": (r"(?:Tables?|Tabs?\.)", r"표")}

ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
                "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13,
                "xiv": 14, "xv": 15}

# One printed label: `A.1` / `S2`, `10` / `2.1`, or a roman numeral that no Latin
# letter continues (`Table in` is not Table I). It must end there: a longer number
# (`10`, `1.2`) is a different label, and a Hangul counter makes it a count —
# 표 2개 is "two tables". 번 is not one: 그림 2번 is "figure number 2".
_LABEL = (r"(?P<label>[A-Z]\.?[0-9]+(?:\.[0-9]+)*|[0-9]+(?:\.[0-9]+)*"
          r"|[ivx]+(?![A-Za-z0-9]))(?![0-9]|\.[0-9]|[개장차배명회줄행열층단쪽권편종])")
# What joins the labels of one citation: a list, or a range (`2-4`, `2~4`, `2 to 4`).
_JOIN = (r"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+|\s*(?:&|및|[와과])\s*"
         r"|(?P<range>\s*[-–—~]\s*|\s+to\s+))")
_CITATION = {kind: re.compile(
    rf"(?<!Data )(?P<word>(?<![A-Za-z]){latin}|(?<![가-힣]){hangul})[\s~]*{_LABEL}",
    re.I) for kind, (latin, hangul) in _FLOAT_WORDS.items()}
_NEXT_LABEL = re.compile(_JOIN + _LABEL, re.I)
_MAX_RANGE = 30
_PLURAL = re.compile(r"(?:figures|figs|tables|tabs)\b", re.I)
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)*")
# What a Korean sentence glues onto a float label: a particle, or 번 ("number").
# Anything else glued on makes the number a quantity: 3절 (section 3), 3초,
# 3가지, 3년.
_PARTICLE = re.compile(r"에|의|은|는|이(?![상하내])|가(?!지)|을|를|과|와|도|까지|부터"
                       r"|으?로|처럼|보다|번|참조|및")


def _a_quantity_follows(text: str, at: int, plural: bool, comma: bool = False) -> bool:
    """Does what follows the number ending at `at` make it a quantity, not a label?

    Only a list's later numbers are asked: after "Table 2, " the next number is
    as often a count or a measure as another table. `3D`, `3rd`, `5-shot`,
    `30%`, `3×3`, `3/4`, a Hangul word that is no particle (`3절`, `3초`), and,
    after a SINGULAR word, an English word (`Figure 2 and 3 epochs`, `Fig. 3,
    4 m/s`): a list of figures says "Figures".

    Two particles are also units. `번` is "number" in `3번에서` and a count in
    `3번째` (third), `3번씩` and `3번 반복` (three times). `도` is "also" in
    `그림 2와 3도`, and degrees after a bare comma (`comma`): `(그림 2, 30도 회전)`.
    """
    if _NEXT_LABEL.match(text, at):
        return False            # the list goes on
    rest = text[at:]
    if re.match(r"[A-Za-z%°]|\s*[-–—]\s*[A-Za-z가-힣]|\s*[×xX*/]\s*[0-9]", rest):
        return True
    if rest.startswith("번"):
        after = rest[1:]
        if re.match(r"[가-힣]", after):
            return not _PARTICLE.match(after)
        return bool(re.match(r"\s+[가-힣]", after)
                    and not re.match(r"\s+(?:참조|참고)", after))
    if rest.startswith("도") and comma:
        return True
    if re.match(r"[가-힣]", rest):
        return not _PARTICLE.match(rest)
    return bool(not plural and re.match(r"\s+[A-Za-z]", rest))


def _label_key(label) -> str:
    """`2` for "2" / "02" / 2, `a.1` for "A.1": how two labels compare."""
    text = str(label).strip().lower()
    return str(int(text)) if text.isdigit() else text


def _is_roman(label: str) -> bool:
    return bool(re.fullmatch(r"[ivx]+", label, re.I))


def cited_labels(kind: str, text: str) -> set:
    """Every `kind` label `text` cites, as `_label_key`s, lists and ranges expanded.

    "Tables 2 and 3에" cites 2 and 3, "표 2–4" cites 2, 3 and 4. Reading only the
    label after the word missed the rest of the list, and the relocation then
    moved a table off the very sentence citing it. A later number of a list
    counts only when nothing makes it a quantity (`_a_quantity_follows`):
    "Table 2와 3절에서" cites Table 2 and section 3, and reading it as Table 3
    moved that table off the sentence that really cites it.
    """
    out = set()
    for found in _CITATION[kind].finditer(text or ""):
        labels, at = [found.group("label")], found.end()
        plural = bool(_PLURAL.match(found.group("word")))
        while True:
            more = _NEXT_LABEL.match(text, at)
            if not more:
                break
            low, high = labels[-1], more.group("label")
            if _is_roman(high) != _is_roman(labels[0]):
                break   # "Table 2 and I think": a pronoun, not Table I
            comma = text[at:more.start("label")].strip() == ","
            if (_NUMBER.fullmatch(high)
                    and _a_quantity_follows(text, more.end(), plural, comma)):
                break   # "Table 2와 3절", "Figure 2, 32 GPUs"
            if more.group("range"):
                # Only an ascending run of numbers is a range; `2-1` is a label
                # of its own (chapter 2, table 1), not a citation of table 1.
                if not (low.isdigit() and high.isdigit()
                        and 0 < int(high) - int(low) <= _MAX_RANGE):
                    break
                labels.extend(str(n) for n in range(int(low) + 1, int(high)))
            labels.append(high)
            at = more.end()
        out.update(_label_key(label) for label in labels)
    return out


def mentions(kind: str, num, text: str, roman: str = "") -> bool:
    r"""The one rule for "this text mentions figure/table `num`".

    Figures and tables each had their own copy and they drifted: the figure copy
    learned that `\b` does not hold before a Korean particle, the table copy never
    did, so `Table 2에` was invisible and the table fell to a fallback.

    The label must match as a WHOLE label — `Figure\s*2` must not match
    "Figure F.2" at the 2, which put appendix figures beside the body figure
    sharing their digit. And the boundary rejects only a LONGER NUMBER (`10`,
    `1.2`) or a counter (`표 2개`), never a following word: a particle glues
    straight onto the number (`Fig. 10에서`), 에 is a word character, and refusing
    it sent Fig 3, 10 and 15 to the appendix while the text citing them sat
    chapters earlier. A roman numeral (`TABLE II`) also refuses a following
    Latin letter, or the roman 1 would read "Table in the appendix" as Table I.
    A list or range cites every label in it (`cited_labels`).

    Case-insensitive, since a translated body writes `fig. 5` as often as
    `Fig. 5`. "Extended Data Fig. 3" names a different figure than "Fig. 3".

    Args:
        kind: "figure" or "table".
        num: The printed label: 3, or "F.2" for an appendix figure.
        text: The text to look in.
        roman: The label as a roman numeral too, lowercase ("ii"), or "".
    """
    cited = cited_labels(kind, text)
    return _label_key(num) in cited or bool(roman and roman.lower() in cited)


def first_mention(kind: str, num, blocks: list, roman: str = ""):
    """Id of the first text block that mentions this float, or None.

    Another float's caption is skipped: one caption saying "compare with Figure 13"
    is the first mention of 13 on the page, and anchoring there put Figure 13
    directly under Figure 1 (see `caption_number`).
    """
    for b in blocks:
        if b["type"] not in TEXT_TYPES:
            continue
        text = _block_text(b)
        if _is_other_caption(text, kind, num):
            continue
        if mentions(kind, num, text, roman):
            return b["id"]
    return None


def appendix_anchor(letter: str, blocks: list):
    """The heading of appendix `letter` (`Appendix A …`, `부록 A …`), or None."""
    app = re.compile(rf"(?:appendix|부록)\s*{re.escape(letter)}(?![A-Za-z0-9])", re.I)
    for b in blocks:
        if b["type"].startswith("heading") and app.search(_block_text(b)):
            return b["id"]
    return None


def element_span(html_text: str, at: int):
    """`(inner_start, inner_end, end)` of the element whose start tag holds `at`.

    Walks the nesting of that element's own tag, so a table figure that wraps its
    panels in figures of their own ends at ITS closing tag, not the first one.
    An element that is never closed runs to the end of the text.
    """
    start = html_text.rfind("<", 0, at + 1)
    tag = re.match(r"<([A-Za-z][\w-]*)", html_text[start:]) if start >= 0 else None
    open_end = html_text.find(">", at)
    if not tag or open_end < 0:
        return None
    opener = re.compile(rf"<{tag.group(1)}\b", re.I)
    closer = re.compile(rf"</{tag.group(1)}\s*>", re.I)
    depth, i = 1, open_end + 1
    while True:
        close = closer.search(html_text, i)
        if not close:
            return open_end + 1, len(html_text), len(html_text)
        nested = opener.search(html_text, i, close.start())
        if nested:
            depth, i = depth + 1, nested.end()
            continue
        depth -= 1
        if not depth:
            return open_end + 1, close.start(), close.end()
        i = close.end()


_SECTION_OPEN = re.compile(r"<section\b[^>]*>", re.I)
# `S3`, `A1`, `S2a` (a part that restarts the numbering), `Sx1` (unnumbered) —
# not a paragraph some renders also mark `ltx_section` (`subsubsubsectionx12`).
_TOP_SECTION_ID = re.compile(r"[SA]x?[0-9]+[a-z]?(?:\\par)?")
_TAG_SPAN = re.compile(r'<span\b[^>]*\bclass="ltx_tag\b[^"]*"[^>]*>', re.I)
_TITLE_OPEN = re.compile(r"<h[1-6]\b[^>]*>", re.I)
_UNTAGGED_NUMBER = re.compile(r"\s*([0-9]+|[IVX]+)\.?\s+(?=\S)")


def _section_label(tag: str, appendix: bool):
    """3 for a printed `3`, `3.` or `III`; "B" for `Appendix B` or a lone `B`."""
    tag = re.sub(r"\\par\b", "", tag).strip()
    letter = re.fullmatch(r"(?:appendix\s+)?([A-Z])\.?", tag, re.I)
    if letter and (appendix or tag.lower().startswith("appendix")):
        return letter.group(1).upper()
    number = re.fullmatch(r"([0-9]+|[IVX]+)\.?", tag)
    if number:
        token = number.group(1)
        return int(token) if token.isdigit() else ROMAN_VALUES.get(token.lower())
    return letter.group(1).upper() if letter else None


def source_sections(html_text: str) -> list:
    """Every top-level section of a LaTeXML source, in document order.

    Each is `{"id", "label", "title", "start", "end"}`: `label` is what the
    section prints — 3 for `3` or `III`, "B" for `Appendix B` or a restarted
    part's `B` — or None, and `title` its heading without that label.
    """
    out = []
    for found in _SECTION_OPEN.finditer(html_text or ""):
        tag = found.group(0)
        sid = re.search(r'\bid="([^"]+)"', tag)
        cls = re.search(r'\bclass="([^"]*)"', tag)
        cls = cls.group(1) if cls else ""
        if not sid or not _TOP_SECTION_ID.fullmatch(sid.group(1)) or not re.search(
                r"\bltx_(?:section|appendix)\b", cls):
            continue
        span = element_span(html_text, found.start())
        if not span:
            continue
        inner = html_text[span[0]:span[1]]
        head = _TITLE_OPEN.search(inner)
        title, label = "", None
        if head:
            head_span = element_span(inner, head.start())
            title_html = inner[head_span[0]:head_span[1]]
            tag_open = _TAG_SPAN.search(title_html)
            if tag_open:
                tag_span = element_span(title_html, tag_open.start())
                label = _section_label(_clean(title_html[tag_span[0]:tag_span[1]]),
                                       "ltx_appendix" in cls)
                title_html = title_html[:tag_open.start()] + title_html[tag_span[2]:]
            title = _clean(title_html)
            untagged = None if tag_open else _UNTAGGED_NUMBER.match(title)
            if untagged:        # a render that prints the number in the title
                label = _section_label(untagged.group(1), False)
                title = title[untagged.end():]
        out.append({"id": re.sub(r"\\par$", "", sid.group(1)), "label": label,
                    "title": title, "start": found.start(), "end": span[2]})
    return out


def enclosing_section(sections: list, at: int):
    """`{"id", "label", "title"}` of the top-level section holding position `at`."""
    inside = [s for s in sections if s["start"] <= at < s["end"]]
    if not inside:
        return None
    s = inside[-1]
    return {"id": s["id"], "label": s["label"], "title": s["title"]}


# A top-level section label: `2`, `2.`, `II.`, followed by the title. Not
# `2.1`, `6-DoF` (a title that opens with a number), `3)` (an IEEE item), `III-A`.
_TOP_LEVEL = re.compile(r"\s*([0-9]+|[IVX]+)[.:]?(?=\s|$)")
_SUBSECTION = re.compile(r"\s*(?:([0-9]+)\.[0-9]|([IVX]+)-[A-Z](?![A-Za-z]))")


def _top_level_number(heading_text: str):
    """2 for "2 Method" / "2. Method" / "II. METHOD"; None for "2.1 …",
    "6-DoF …", "3) …" or no label."""
    found = _TOP_LEVEL.match(heading_text or "")
    if not found:
        return None
    token = found.group(1)
    return int(token) if token.isdigit() else ROMAN_VALUES.get(token.lower())


def _subsection_of(heading_text: str):
    """6 for "6.1 …" or "6.1.6 …", 4 for "IV-A …"; None otherwise."""
    found = _SUBSECTION.match(heading_text or "")
    if not found:
        return None
    if found.group(1):
        return int(found.group(1))
    return ROMAN_VALUES.get(found.group(2).lower())


def _title_key(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (title or "").lower())


def _heading_title_keys(heading_text: str) -> set:
    """'3. Causal Dataset (인과 데이터셋)' and 'Appendix B: Causal Dataset' ->
    {'causaldataset', ...}: the title (with or without what follows its
    bracket, as in '3 Title (번역) — 발췌'), and what a trailing bracket holds,
    since a page may write '3. 인과 데이터셋 (Causal Dataset)' instead."""
    import verify_sections as vs
    text = vs._APPENDIX_PREFIX.sub("", heading_text or "")
    keys = {vs._echo_norm(text), _title_key(vs.english_title(text))}
    bracket = re.search(r"\(([^()]*)\)\W*$", text)
    if bracket:
        keys.add(_title_key(bracket.group(1)))
    return {k for k in keys if len(k) >= 4}


def _level(block: dict) -> int:
    return int(block["type"][-1]) if block["type"][-1].isdigit() else 9


def _shallowest(found: list):
    """The first of the shallowest headings in `found`, or None."""
    return min(found, key=_level)["id"] if found else None


_APPENDIX_HEADING = re.compile(r"\s*(?:appendix|appendices|부록|[A-HJ-UWYZ][.:](?=\s))",
                               re.I)


def _heading_titled(title: str, blocks: list, appendix: bool = False):
    """The section heading carrying the source section's `title`, or None.

    A section heading of the same kind: one labelled with a top-level number
    for a numbered section (any number — a page may number its own), an
    appendix heading for an appendix. A title alone is too common to go on:
    the body's run-in `Implementation Details` is not appendix B's. And not one
    of two at the same depth: a page that holds a section twice says nothing
    about which copy is the float's.
    """
    key = _title_key(title)
    if len(key) < 4:
        return None
    found = []
    for b in blocks:
        text = _block_text(b) if b["type"].startswith("heading") else ""
        kind = (_APPENDIX_HEADING.match(text) if appendix
                else _top_level_number(text) is not None)
        if kind and key in _heading_title_keys(text):
            found.append(b)
    top = [b for b in found if _level(b) == min(map(_level, found))] if found else []
    return top[0]["id"] if len(top) == 1 else None


def _section_number(section: dict):
    """The number a source section prints, else the one its id gives a render
    that prints none (`S3` is 3); None for an appendix or a restarted part."""
    label = section.get("label")
    if label is not None:
        return label if isinstance(label, int) else None
    found = re.fullmatch(r"S([0-9]+)", section.get("id") or "")
    return int(found.group(1)) if found else None


_SHORTENED_TITLE = 16


def _same_title(a: str, b: str) -> bool:
    """Are title keys `a` and `b` one title, one perhaps cut short?

    The shorter must keep `_SHORTENED_TITLE` characters, so a generic word
    (`introduction`, `experiments`) is never the start of a longer title.
    """
    short, long_ = sorted((a, b), key=len)
    return a == b or (len(short) >= _SHORTENED_TITLE and long_.startswith(short))


def _numbering_mirrors(blocks: list, sections: list) -> bool:
    """Does the page number its sections as the source does?

    Only on evidence: numbered headings of two sections carrying the titles the
    source prints under the same numbers, and none carrying one it prints under
    another. A page that summarises a paper numbers its own sections — its `3.`
    may be the source's 4 — and a heading in Korean alone says nothing either
    way. One heading is not enough: a summary opens with `1. Introduction` as
    the paper does, and then numbers the rest itself. A heading that shortens a
    long title (`4. Specializing and Adapting X` for the source's `… X for New
    Embodiments`) carries it as well (`_same_title`).
    """
    numbers = {}
    for s in sections:
        n, key = _section_number(s), _title_key(s.get("title"))
        if n is not None and len(key) >= 4:
            numbers[key] = n
    agree = set()
    for b in blocks:
        if not b["type"].startswith("heading"):
            continue
        text = _block_text(b)
        n = _top_level_number(text)
        keys = _heading_title_keys(text)
        found = {num for key, num in numbers.items()
                 if any(_same_title(key, k) for k in keys)}
        if n is None or not found:
            continue
        if n not in found:
            return False
        agree.add(n)
    return len(agree) >= 2


def _numbered_heading(n: int, blocks: list):
    """The heading of section `n`: the shallowest heading labelled `n`, else the
    first of its subsections (a page may open the section at `6.1`)."""
    headings = [b for b in blocks if b["type"].startswith("heading")]
    found = _shallowest([b for b in headings if _top_level_number(_block_text(b)) == n])
    if found:
        return found
    return next((b["id"] for b in headings if _subsection_of(_block_text(b)) == n), None)


_OWN_SECTION = re.compile(r"(S|A)(\d+)\.")


def _label_from_id(found):
    """The section label a float id names: 3 for `S3.`, "A" for `A1.`."""
    n = int(found.group(2))
    if found.group(1) == "S":
        return n
    return chr(ord("A") + n - 1) if 1 <= n <= 26 else None


def section_anchor(fid: str, blocks: list, section=None, sections=None):
    """The heading of the section a float sits in, or None.

    The section is the one the source prints the float in (`section`, from
    `enclosing_section`), else the one its LaTeXML id names (`S3.T5` is in
    section 3, `A1.T6` in appendix A). The fallback used to be the heading whose
    number equals the FLOAT's number, which has nothing to do with where the
    float is: Table 6 went under `6 Conclusion` whatever section printed it.

    The heading is the one carrying the section's title, else the shallowest
    one labelled with its number — `6-DoF …`, `3) …` and a local `4.` inside
    another section are not section headings. A number is trusted only while the
    page numbers its sections as the source does (`_numbering_mirrors`, given
    `sections`), and an id names no section when the source puts the float in
    another one: a supplementary part restarts the section ids (`S2a`) while
    its floats keep `S2.T9`.

    Returns None when the page has no such heading; the caller's own last
    resort decides, which beats a confident wrong place.
    """
    own = _OWN_SECTION.match(fid or "")
    if section is not None:
        found = _heading_titled(section.get("title"), blocks,
                                isinstance(section.get("label"), str))
        if found:
            return found
        label = section.get("label")
        if label is None and own and section.get("id") == own.group(0)[:-1]:
            label = _label_from_id(own)
    else:
        label = _label_from_id(own) if own else None
    if label is None:
        return None
    if isinstance(label, str):
        return appendix_anchor(label, blocks)
    if sections and not _numbering_mirrors(blocks, sections):
        return None
    return _numbered_heading(label, blocks)


def _anchor_for(num, blocks: list, fid: str = "", section=None, sections=None):
    """Block id to insert a figure `num` after: first body mention of the figure
    number, else the heading of the section it sits in (`section_anchor`), else
    None (page end)."""
    if num is None:
        return None
    found = first_mention("figure", num, blocks)
    if found:
        return found
    letter = re.match(r"([A-Za-z])\.", str(num))
    if letter:
        # An appendix figure belongs in its appendix, never in the body.
        return appendix_anchor(letter.group(1), blocks)
    return section_anchor(fid, blocks, section, sections)


def in_section(blocks: list, head: int, i: int) -> bool:
    """Does block `i` sit in the section that the heading at `head` opens?"""
    if i <= head:
        return False
    level = _level(blocks[head])
    return not any(b.get("type", "").startswith("heading") and _level(b) <= level
                   for b in blocks[head + 1:i])


def figure_anchors(figs: list, blocks: list, sections=None) -> dict:
    """{figure number: block to insert it after, or None} for `parse_figures`.

    The first block citing the figure (`_anchor_for`). One no text cites goes
    under the heading of its own section, after the figures of that section
    the text cites before it: right under the heading it sat ABOVE a
    lower-numbered figure cited further down, out of reading order. Then beside
    its neighbours (`fill_anchor_gaps`); the page end is the last resort.
    """
    nums = [f["num"] for f in figs]
    found = {f["num"]: _anchor_for(f["num"], blocks, f["id"], f.get("section"),
                                   sections) for f in figs}
    at = {b["id"]: i for i, b in enumerate(blocks)}
    for num in sorted((n for n in nums if n is not None), key=_series_key):
        head = found.get(num)
        if (not head or not blocks[at[head]]["type"].startswith("heading")
                or first_mention("figure", num, blocks)):
            continue
        series = _series_key(num)
        lower = [n for n in nums if n is not None and found.get(n)
                 and _series_key(n)[0] == series[0] and _series_key(n) < series
                 and in_section(blocks, at[head], at[found[n]])]
        if lower:
            found[num] = found[max(lower, key=_series_key)]
    return fill_anchor_gaps(nums, found)


def _image_block(fid: str, caption: str) -> dict:
    img = {"type": "file_upload", "file_upload": {"id": fid}}
    if caption:
        img["caption"] = [{"type": "text", "text": {"content": caption[:2000]}}]
    return {"object": "block", "type": "image", "image": img}



# A page with no body cannot be placed into. Anchoring means "after the paragraph
# that first mentions this number", so on a page whose text has not been uploaded
# yet EVERY anchor misses and every float falls to the page-end fallback — which,
# on a page that is still empty, is the TOP. One paper shipped with all eleven of
# its figures stacked above the first heading, in a pile, because the agent
# injected them in the same minute it created the page and appended the
# translation underneath afterwards. Nothing could repair it later either: the
# next healer cycle sees images present and skips.
#
# Refusing is safe and self-healing — the healer retries every five minutes, and
# the moment the text lands the anchors work.
MIN_BODY_CHARS_TO_ANCHOR = 1000


def _has_body_to_anchor(blocks) -> bool:
    # The injected reference list is NOT body: it is English apparatus appended at
    # the tail, it anchors nothing, and counting it made a never-translated page
    # carrying 77 reference entries read as 43k characters of body.
    chars = 0
    for b in reference_section.body_blocks(blocks):
        if b.get("type") == "image":
            continue
        payload = b.get(b.get("type")) or {}
        if isinstance(payload, dict):
            chars += sum(len(s.get("plain_text", "")) for s in payload.get("rich_text", []))
    return chars >= MIN_BODY_CHARS_TO_ANCHOR


def inject_figures(page_id: str, arxiv_id: str, apply: bool = False,
                   force: bool = False) -> dict:
    import time
    import verify_sections as vs
    from translate_fulltext import notion
    from notion_upload import upload_image

    blocks = vs.fetch_blocks(page_id)
    # Count only NON-table images: table images (caption "Table N", injected by
    # extract_paper_tables) must not make the figure healer think figures exist.
    def _is_fig_img(b):
        if b["type"] != "image":
            return False
        cap = "".join(c.get("plain_text", "") for c in
                      ((b.get("image") or {}).get("caption") or [])).strip().lower()
        return not cap.startswith("table")
    # Look inside nested blocks too: an agent-injected figure is often PATCHed as a
    # child of its caption paragraph, and a top-level-only scan both miscounts the
    # page as empty and leaves those images behind on --force (same defect already
    # fixed in extract_pdf_media).
    from extract_pdf_media import _all_image_blocks
    page_imgs = [b for b in _all_image_blocks(blocks) if _is_fig_img(b)]
    have_imgs = len(page_imgs)
    rep = {"page": page_id, "existing_images": have_imgs, "replaced": 0,
           "found": 0, "placed": 0, "skipped_existing": False}
    if not _has_body_to_anchor(blocks):
        rep["deferred"] = "no body text to anchor against"
        return rep
    if have_imgs and not force:
        rep["skipped_existing"] = True          # idempotent: don't duplicate
        return rep

    html_text, src = fetch_html(arxiv_id)
    if not html_text:
        rep["error"] = "no HTML source with figures"
        return rep
    figs = parse_figures(html_text, src)
    rep["found"] = len(figs)
    rep["source"] = src
    missing = getattr(parse_figures, "no_image", [])
    if missing:
        # Loud, because the page will look complete: it still gets every other
        # figure, and FIGURES_MISSING only fires when a page has NO images at all.
        rep["no_image"] = missing

    # --force means REPLACE, not "add a second set". Without this it only bypassed
    # the skip, so forcing a page that already had figures left it with BOTH copies
    # — the doubling bug extract_pdf_media already fixed. Old images go only after
    # the source parsed successfully, so a failed fetch can't strip a good page.
    if force and page_imgs and figs:
        # Replace only images this pipeline is responsible for. Ours always carry a
        # "Figure N" caption; an agent-injected one carries none but sits NESTED
        # under its caption paragraph. A TOP-LEVEL image with no caption is neither
        # — it is one a human placed by hand (e.g. supplying a figure the source
        # renders as markup and we cannot extract), and re-healing must not delete
        # someone's manual repair.
        top_ids = {b["id"] for b in blocks if b["type"] == "image"}
        replaceable = [b for b in page_imgs
                       if _has_our_caption(b) or b["id"] not in top_ids]
        rep["replaced"] = len(replaceable)
        rep["kept_manual"] = len(page_imgs) - len(replaceable)
        if apply:
            for b in replaceable:
                notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
                time.sleep(0.2)
            blocks = vs.fetch_blocks(page_id)

    # Resolve anchors first, then fill the gaps IN NUMERIC ORDER. A figure the body
    # never cites (or whose citation the translation dropped) used to fall straight
    # to "__end__" — which is the last section, so it surfaced in the appendix while
    # the figures around it sat chapters earlier. It now goes to its own section,
    # after the figures that section cites before it, else beside the nearest
    # placed neighbour (`figure_anchors`); the page end remains the last resort.
    _sort_key = lambda f: _series_key(f["num"])
    by_num = figure_anchors(figs, blocks, source_sections(html_text))
    resolved = {id(f): by_num.get(f["num"]) for f in figs}
    ordered_figs = sorted(figs, key=_sort_key)

    groups, order = {}, []
    for f in ordered_figs:
        anchor = resolved[id(f)]
        key = anchor or "__end__"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    for key in order:
        children = []
        for f in groups[key]:
            if apply:
                fid = upload_image_from_url(f["img_url"])
                if not fid:
                    continue
                children.append(_image_block(fid, f["caption"]))
                time.sleep(0.2)
            else:
                children.append({"_fig": f["num"], "_after": key})
        if not children:
            continue
        rep["placed"] += len(children)
        if apply:
            body = {"children": children}
            end_of_body = reference_section.body_end_anchor(blocks)
            if key != "__end__":
                body["after"] = key
            elif end_of_body:
                body["after"] = end_of_body
            notion("PATCH", f"/blocks/{page_id}/children", body)
            time.sleep(0.34)

    # AFTER the HTML pass and after --force has archived the old images: running it
    # earlier meant the "is this number already present?" check saw images that
    # were about to be deleted, so the fallback skipped every one of them.
    rep["pdf_fallback"] = 0
    numeric_missing = {int(x) for x in missing if str(x).isdigit()}
    if numeric_missing and apply:
        # The HTML has these figures but not as images — LaTeXML rendered them as
        # vector markup, so there is no <img> to upload and the page silently ends
        # up short. Nothing covered this: heal_pdf_media deliberately stands down
        # whenever HTML exists, so "HTML present but this figure missing" belonged
        # to no one. The PDF always has every figure; render just the missing
        # numbers so the ones HTML did supply are not re-done.
        try:
            import extract_pdf_media as pm
            sub = pm.inject(page_id, arxiv_id, apply=True, force=False,
                            kinds=("figure",), keep_text=True,
                            only=numeric_missing)
            rep["pdf_fallback"] = sub.get("placed") or 0
        except Exception as e:
            rep["pdf_fallback_error"] = f"{type(e).__name__}: {e}"[:160]

    return rep


_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|html|pdf)/(\d{4}\.\d{4,5})", re.I)

# Backfilling a Paper URL points every downstream healer at that paper, so the
# title has to match almost exactly — the arxiv API's own confidence gate is not
# enough on its own (it only compares candidates against each other).
_TITLE_MATCH_MIN = 0.9


def arxiv_id_from_page(page_id: str):
    """Return the arxiv id from the page's 'Paper URL' property, or None."""
    from translate_fulltext import notion
    pg = notion("GET", f"/pages/{page_id}")
    for prop in (pg.get("properties") or {}).values():
        if prop.get("type") == "url" and prop.get("url"):
            m = _ARXIV_RE.search(prop["url"])
            if m:
                return m.group(1)
    return None


def _page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title") or [])
    return ""


def ensure_arxiv_id(page_id: str, apply: bool = False):
    """The page's arxiv id, resolving it from the TITLE when Paper URL is empty.

    Why: every visual/citation healer is keyed on the arxiv id parsed out of
    `Paper URL`. A paper added without one therefore makes `heal_figures`,
    `heal_tables` and `verify_citations` return `placed: 0` FOREVER — a silent
    no-op that looks identical to "already clean", so the page can never be
    repaired no matter how many times the healer runs. Observed on a real page
    whose figures and tables were both broken and un-healable.

    The resolve is deliberately strict — a wrong id would translate/illustrate a
    DIFFERENT paper, the failure this repo already has scar tissue for. It goes
    through the authoritative arxiv API (which refuses on ambiguity) and then
    additionally demands a near-exact title match, so it only ever fills in an id
    that is already implied by the title on the page.
    """
    aid = arxiv_id_from_page(page_id)
    if aid:
        return aid
    import resolve_arxiv
    from translate_fulltext import notion

    pg = notion("GET", f"/pages/{page_id}")
    # Only an EMPTY Paper URL is filled in. A page carrying a publisher URL is a
    # journal paper; resolving its title would overwrite that URL with the
    # preprint's and illustrate the page from a different version of itself.
    if any(p.get("type") == "url" and p.get("url")
           for p in (pg.get("properties") or {}).values()):
        return None
    title = _page_title(pg).strip()
    if len(title) < 12:
        return None
    try:
        hit = resolve_arxiv.resolve(title)
    except Exception:
        return None
    if hit.get("ask_user") or not hit.get("arxiv_id"):
        return None
    if resolve_arxiv._sim(title, hit.get("title") or "") < _TITLE_MATCH_MIN:
        return None
    url_prop = next((name for name, p in (pg.get("properties") or {}).items()
                     if p.get("type") == "url"), None)
    if apply and url_prop:
        notion("PATCH", f"/pages/{page_id}",
               {"properties": {url_prop: {"url": hit["url"]}}})
    return hit["arxiv_id"]



def recaption_page(page_id: str, arxiv_id: str = None, apply: bool = False) -> dict:
    """Fix images captioned with a sub-panel label, WITHOUT moving anything.

    The composite-figure bug above shipped before it was found, so pages already
    carry panel captions like "(a) Example rollouts." — and their figure number is
    simply absent. Re-running the injector would repair them but also re-place
    every figure, which is destructive on a page somebody has arranged by hand.
    This edits captions only: it finds the source figure whose markup CONTAINS the
    panel caption now on the page, and writes that figure's own caption instead.
    """
    import time
    import verify_sections as vs
    from translate_fulltext import notion

    rep = {"page": page_id, "recaptioned": 0, "unmatched": []}
    arxiv_id = arxiv_id or arxiv_id_from_page(page_id)
    if not arxiv_id:
        rep["error"] = "no arxiv id"
        return rep
    html_text, src = fetch_html(arxiv_id)
    if not html_text:
        rep["error"] = "no HTML source"
        return rep
    bodies = {fid: body for fid, body in _iter_figures(html_text)}
    good = {f["id"]: f["caption"] for f in parse_figures(html_text, src)}
    for b in vs.fetch_blocks(page_id):
        if b["type"] != "image":
            continue
        cap = "".join(c.get("plain_text", "") for c in
                      ((b.get("image") or {}).get("caption") or []))
        if not re.match(r"\s*\([a-z]\)\s", cap):
            continue                     # already carries a real caption
        probe = _clean(cap)[:40]
        owner = next((fid for fid, body in bodies.items()
                      if probe and probe in _clean(body)), None)
        if not owner or not good.get(owner):
            rep["unmatched"].append(cap[:40])
            continue
        rep["recaptioned"] += 1
        if apply:
            notion("PATCH", f"/blocks/{b['id']}",
                   {"image": {"caption": [{"type": "text",
                                           "text": {"content": good[owner][:1900]}}]}})
            time.sleep(0.3)
    return rep

def heal_figures(page_id: str, apply: bool = False) -> dict:
    r"""Healer entry: inject figures when the page has none — or when the ones it
    has clearly did not come from this injector — and its Paper URL resolves to an
    arxiv id. Idempotent no-op otherwise.

    Why the second case exists: "the page has images" is NOT the same as "the page's
    figures are done". An agent that injects figures by hand uploads them with NO
    caption and drops them wherever it happens to be — typically bunched at the end
    of a section instead of after each figure's first mention. The healer then saw
    images, reported `skipped_existing`, and could never repair the placement, so
    the page stayed wrong forever (observed: 7 uncaptioned images clustered at
    section ends while the source HTML had all 7 with proper captions).

    Every image THIS code writes carries a `Figure N:` caption, so an image set with
    no such caption is a reliable signal that the figures are not ours and should be
    re-done. Deliberately narrow: if even one properly-captioned figure is present
    the page is left alone, so a half-healed page is never churned.
    """
    aid = ensure_arxiv_id(page_id, apply=apply)
    if not aid:
        return {"page": page_id, "arxiv": None, "placed": 0}
    import verify_sections as vs
    from extract_pdf_media import _all_image_blocks

    imgs = _all_image_blocks(vs.fetch_blocks(page_id))
    caps = ["".join(c.get("plain_text", "") for c in
                    ((b.get("image") or {}).get("caption") or [])).strip().lower()
            for b in imgs]
    # `Fig. 4:` is our own caption too — it is copied verbatim from the source, and
    # IEEE-style papers write the short form. Testing only for "figure"/"그림" made
    # every such page look FOREIGN, so the healer force-replaced its own correct
    # figures on every cycle, forever. Match the short form as well.
    ours = re.compile(r"^\s*(?:fig\.?|figure|그림)\s*[0-9]", re.I)
    foreign = bool(caps) and not any(ours.match(c) for c in caps)
    return inject_figures(page_id, aid, apply=apply, force=foreign)


def upload_image_from_url(url: str):
    """Download an image to a temp file and upload it privately into Notion."""
    from notion_upload import upload_image
    import tempfile
    ext = os.path.splitext(url.split("?")[0])[1] or ".png"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception:
        return None
    if len(data) < 100:
        return None
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        return upload_image(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--arxiv", required=True, help="arxiv id (NNNN.NNNNN)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recaption", action="store_true",
                    help="fix sub-panel captions only; never moves a block")
    ap.add_argument("--force", action="store_true",
                    help="inject even if the page already has image blocks")
    a = ap.parse_args()
    rep = (recaption_page(a.page, a.arxiv, apply=not a.dry_run) if a.recaption
           else inject_figures(a.page, a.arxiv, apply=not a.dry_run, force=a.force))
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
