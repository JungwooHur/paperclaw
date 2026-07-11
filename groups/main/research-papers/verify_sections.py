#!/usr/bin/env python3
"""Audit a translated Notion paper page for structural completeness against the
source paper. Read-only by default; it never edits the page.

Root causes this guards against (all observed in real processing runs where the
section-by-section workflow silently degraded into something thinner):

  1. DUPLICATED sections. Phase-4 assembly is hand-rolled curl PATCHes; when the
     agent loses track of what it already uploaded it re-appends whole sections,
     so the page ends up with section 3 / 4 (etc.) twice. Step 2-C only checked
     `heading_count >= section_count`, which a duplicated page trivially passes.

  2. CONTENT-LOSS. A per-section NotebookLM call that timed out / got backgrounded
     leaves a section with a single stub sentence (tens of chars) instead of the
     full translation. Nothing downstream noticed an almost-empty section.

  3. SUMMARIZATION. When several subsections are collapsed into ONE NotebookLM
     call ("translate all of section 3 incl. 3.1-3.4"), NotebookLM condenses them
     to fit its output limit, so each subsection lands ~5-15x thinner than a
     section translated on its own. The page reads like a summary, not a
     translation. The only reliable signal is the translated length vs. the
     length of the SAME section in the source.

What this does:
  * Pull the page's top-level blocks, group body text under each heading, and key
    each heading by its leading section number ("1", "2.1", "III").
  * DUPLICATE check (no source needed): any section key present more than once.
    The extra copies' heading block-ids are printed so they can be deleted.
  * COMPLETENESS / SUMMARIZATION check (needs --source or --arxiv): locate each
    section in the source text, compute translated_chars / source_chars, and flag
    sections below --min-ratio (summarized) or below --min-chars absolute
    (content-loss / stub).
  * MISSING check (needs --sections manifest): a listed section with no heading.

Exit code: 0 = clean, 2 = findings (so the subagent gate / a healer can react).
This is a verification gate, not a fixer — re-translate the flagged sections
individually (one NotebookLM call each) and delete the duplicate block-ids.

Usage:
  verify_sections.py --page <page_id> [--source <pdf-path-or-url>]
                     [--arxiv <id>] [--sections <sections.txt>]
                     [--min-ratio 0.35] [--min-chars 400] [--json]

  --source : a local PDF path, an http(s) PDF url, or a landing-page url.
  --arxiv  : arxiv id; fetches arxiv-native HTML (ar5iv fallback) as the source.
  If neither --source nor --arxiv is given, only the DUPLICATE check runs.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from html import unescape
import urllib.request

import auto_save_qa as aq  # shared Notion helpers: api_get, _block_text, headers

HEADING_TYPES = ("heading_1", "heading_2", "heading_3")
# Body-bearing block types whose text counts toward a section's length.
BODY_TYPES = ("paragraph", "bulleted_list_item", "numbered_list_item",
              "quote", "callout", "toggle", "code")
# Leading section label. Handles the forms papers actually use:
#   roman "I" / "IV", roman+letter "III-A" / "IV-D" (IEEE subsections),
#   arabic "2" / "2.1" / "3.4.1", and appendix letters "A" / "B" / "C".
# The trailing lookahead keeps it from biting the first word of an unlabeled
# heading ("Score (...)" -> no key, not "S").
_KEY_RE = re.compile(
    r"^\s*("
    r"[IVXLC]+(?:-[A-Z])?"        # I, IV, III-A
    r"|\d+(?:\.\d+)*(?:-[A-Z])?"  # 2, 2.1, 3-A
    r"|[A-Z](?:[.-]\d+)+"         # appendix subsection A.1, B-2 (unique key each)
    r"|[A-Z](?=[.:)\-])"          # appendix letter A./B. — must be followed by
                                  #   punctuation, NOT a space, so an unlabeled
                                  #   heading like "A New Approach" isn't keyed
    r")(?=[\s.:)\-]|$)")
# Non-content artifacts that must never reach a Notion paper body:
#  - CLI furniture: the notebooklm CLI prints conversation status lines to
#    stdout interleaved with the answer; uploading raw stdout embeds them.
#  - RAW MARKDOWN: NotebookLM emits markdown (### headings, **bold**, -/*
#    bullets, --- rules). If the assembler builds paragraph blocks from the raw
#    text instead of converting it (save_qa_callout.build_answer_blocks), these
#    render as literal text. `⬇` is a listing glyph from source HTML.
_ARTIFACT_PATS = [
    ("cli-conversation",
     re.compile(r"(Continuing|Resumed|New) conversation[: ]|Conversation:\s*[0-9a-f-]{8,}", re.I)),
    ("cli-answer-label", re.compile(r"\bAnswer:\s", re.I)),
    # Paired **bold** only — a lone ** is Python kwargs unpacking
    # (e.g. tools[name](**arguments)), legitimate prose/code content. The inner
    # may contain a balanced "(...)" (so bilingual bold like **Introduction
    # (서론)** is still caught) but must NOT contain a ")" that precedes a "("
    # — that ")...(" shape is the signature of matching across two unpackings
    # (f(**a) and g(**b)). Not anchored on a trailing word boundary, so Korean
    # particles glued to bold (**중요**입니다) are still caught.
    ("markdown-bold",
     re.compile(r"\*\*(?=\S)(?![^*\n]*\)[^*\n]*\()[^*\n]{1,100}?(?<=\S)\*\*")),
    ("markdown-heading", re.compile(r"(?:^|\n)#{1,6}\s")),
    ("markdown-rule", re.compile(r"(?:^|\n)(?:---+|\*\*\*+|___+)\s*(?:\n|$)")),
    ("markdown-bullet", re.compile(r"(?:^|\n)\s*[\*\-]\s+\S")),
    ("listing-glyph", re.compile(r"⬇")),
]
# Where a source paper's body ends — don't let "References" inflate the last
# section's measured length.
_TAIL_RE = re.compile(r"\n\s*(references|bibliography|acknowledg(e)?ments)\b",
                      re.IGNORECASE)


def _echo_norm(s: str) -> str:
    """Normalize a heading/paragraph for echo comparison: drop a TRAILING
    '(translation)', the leading section label, and all non-alphanumerics.
    '1. Introduction (서론)' and '1 Introduction (서론)' both -> 'introduction'.

    The parenthetical is stripped only at the END — splitting at the first '('
    would reduce an equation like 'Score(T,G) = min...(1)' to just 'score' and
    falsely match a heading 'Score (점수)'."""
    s = re.sub(r"\s*\([^()]*\)[^0-9a-z가-힣]*$", "", s or "")
    s = _KEY_RE.sub("", s, count=1)
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def section_key(heading_text: str):
    """'2.1 The Robot-Native Regime (2.1 ...)' -> '2.1'. None if no label."""
    m = _KEY_RE.match(heading_text or "")
    return m.group(1) if m else None


def english_title(heading_text: str) -> str:
    """The source-language part of a bilingual heading, label stripped.

    '2.1 The Robot-Native Regime (2.1 한국어)' -> 'The Robot-Native Regime'
    """
    text = heading_text or ""
    # Drop the trailing "(translation)" half if present.
    text = re.split(r"\s*\(", text, 1)[0]
    # Drop the leading section label.
    text = _KEY_RE.sub("", text, count=1)
    return re.sub(r"\s+", " ", text).strip(" .:-")


def fetch_blocks(page_id: str) -> list:
    """All top-level children of the page (paginated)."""
    blocks, cursor = [], None
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        d = aq.api_get(path)
        blocks += d.get("results", [])
        if not d.get("has_more"):
            return blocks
        cursor = d["next_cursor"]


def page_title(page_id: str) -> str:
    p = aq.api_get(f"/pages/{page_id}")
    for v in p.get("properties", {}).values():
        if v.get("type") == "title":
            return "".join(t["plain_text"] for t in v["title"])
    return ""


def group_sections(blocks: list) -> list:
    """Ordered list of section dicts, body text aggregated under each heading.

    Each entry: {key, title, heading, level, chars, heading_id, occurrence}.
    `occurrence` counts repeats of the same key in page order (1-based).
    """
    sections, seen = [], {}
    cur = None
    for b in blocks:
        t = b["type"]
        if t in HEADING_TYPES:
            txt = aq._block_text(b)
            key = section_key(txt)
            seen[key] = seen.get(key, 0) + 1 if key else 0
            cur = {
                "key": key,
                "title": english_title(txt),
                "heading": txt,
                "level": int(t[-1]),
                "chars": 0,
                "heading_id": b["id"],
                "occurrence": seen.get(key, 0),
            }
            sections.append(cur)
        elif t in BODY_TYPES and cur is not None:
            cur["chars"] += len(aq._block_text(b))
    return sections


# ---- source loading -------------------------------------------------------

ARXIV_CANDIDATES = ("https://arxiv.org/html/{id}",
                    "https://ar5iv.labs.arxiv.org/html/{id}")


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    # Keep block boundaries as newlines so the References tail-cut (which
    # anchors on a heading at line start) still works after tag stripping.
    html = re.sub(r"(?i)</(p|h[1-6]|section|div|li|figure|figcaption)>|<br\s*/?>",
                  "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = unescape(html)
    html = re.sub(r"[ \t]+", " ", html)
    return re.sub(r"\n\s*\n+", "\n", html)


def source_text_from_arxiv(arxiv_id: str):
    for tmpl in ARXIV_CANDIDATES:
        url = tmpl.format(id=arxiv_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "ignore")
        except Exception:
            continue
        if len(html) > 50000 and "ltx_document" in html and "Fatal error" not in html:
            return _strip_html(html)
    return None


def source_text_from_pdf(path_or_url: str):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        sys.stderr.write("PyMuPDF not installed; cannot read PDF source.\n")
        return None
    local, is_temp = path_or_url, False
    if re.match(r"^https?://", path_or_url):
        # Unique temp path: parallel subagents share /tmp, so a fixed name
        # would let concurrent gates overwrite each other's source PDF.
        fd, local = tempfile.mkstemp(suffix=".pdf", prefix="verify_sections_")
        os.close(fd)
        is_temp = True
        try:
            req = urllib.request.Request(
                path_or_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": path_or_url})
            with urllib.request.urlopen(req, timeout=60) as r, open(local, "wb") as f:
                f.write(r.read())
        except Exception as e:
            sys.stderr.write(f"could not download source pdf: {e}\n")
            os.unlink(local)
            return None
    try:
        if not os.path.exists(local):
            sys.stderr.write(f"source not found: {local}\n")
            return None
        try:
            doc = fitz.open(local)
            # Keep newlines — the References tail-cut anchors on a heading line.
            text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
        except Exception as e:
            sys.stderr.write(f"could not open pdf: {e}\n")
            return None
    finally:
        if is_temp and os.path.exists(local):
            os.unlink(local)
    return re.sub(r"[ \t]+", " ", text)


def load_source_text(source: str | None, arxiv: str | None):
    if arxiv:
        return source_text_from_arxiv(arxiv)
    if not source:
        return None
    if re.search(r"\.pdf($|\?)", source, re.IGNORECASE) or os.path.exists(source):
        return source_text_from_pdf(source)
    if re.match(r"^https?://", source):  # landing page -> try as html
        try:
            req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "ignore")
            return _strip_html(html)
        except Exception:
            return None
    return None


def source_section_chars(full_text: str, ordered_sections: list) -> dict:
    """Map section key -> source char count.

    Each section heading is located in the source text by a 'number + title
    words' needle (e.g. '2.3' + 'Generating Physical Experience'), taking the
    LAST occurrence — body headings come after any abstract/ToC mention of the
    same words. All located headings are then sorted by position and each
    section's span runs to the next located heading (tail cut at References).

    A parent heading whose children are also located therefore measures only
    its own intro span (parent start -> first child start), matching how the
    Notion side aggregates body text under each heading. A heading that can't
    be located maps to None (checks are skipped for it)."""
    body = full_text
    m = _TAIL_RE.search(full_text)
    if m:
        body = full_text[: m.start()]
    low = body.lower()
    found = []
    for key, title in ordered_sections:
        words = [w for w in re.split(r"\W+", title) if len(w) > 2][:6]
        if not key or not words:
            continue
        needle = (re.escape(key.lower()) + r"\.?\s+"
                  + r"\W+".join(re.escape(w.lower()) for w in words))
        last = None
        for mm in re.finditer(needle, low):
            last = mm
        if last:
            found.append((key, last.start()))
    found.sort(key=lambda t: t[1])
    out = {key: None for key, _ in ordered_sections}
    for n, (key, idx) in enumerate(found):
        nxt = found[n + 1][1] if n + 1 < len(found) else len(body)
        out[key] = max(nxt - idx, 0)
    return out


def load_manifest(path: str | None) -> list:
    if not path or not os.path.exists(path):
        return []
    keys = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            k = section_key(line)
            if k:
                keys.append(k)
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit a Notion paper page for "
                                 "duplicate/short/summarized/missing sections.")
    ap.add_argument("--page", required=True)
    ap.add_argument("--source", help="local PDF path, PDF url, or landing-page url")
    ap.add_argument("--arxiv", help="arxiv id (fetches HTML as source)")
    ap.add_argument("--sections", help="path to the Step 2-A section manifest")
    ap.add_argument("--min-ratio", type=float, default=0.35,
                    help="flag sections below translated/source length ratio")
    ap.add_argument("--min-chars", type=int, default=400,
                    help="flag sections below this absolute body length")
    ap.add_argument("--min-source", type=int, default=800,
                    help="skip ratio/length checks when the source section "
                         "itself is shorter than this")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    blocks = fetch_blocks(args.page)
    sections = group_sections(blocks)
    if not sections:
        print("No headings on page — nothing translated yet, or wrong page id.")
        return 2

    findings = []

    # 1. DUPLICATE sections (occurrence > 1), keyed HIERARCHICALLY by the
    # enclosing-heading chain. A paper commonly reuses bare subsection letters
    # (II has A/B/C/D, III has A/B, IV has A/B/C/D); those are different
    # subsections under different parents, NOT duplicates. Scoping by the parent
    # chain ("II>A" vs "III>A") avoids that false positive, while two real
    # copies of the same section under the same parent still collide.
    dup_keys = {}
    stack = []  # (level, scope_token)
    for s in sections:
        lvl = s["level"]
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        if s["key"]:
            scope = f"{parent}>{s['key']}"
            s["_dupkey"] = scope
            dup_keys.setdefault(scope, []).append(s)
        else:
            # Unlabeled heading: scope children by its title, never dup-checked.
            scope = f"{parent}>~{_echo_norm(s['heading'])[:16]}"
        stack.append((lvl, scope))
    for scope, occ in dup_keys.items():
        key = occ[0]["key"]
        if len(occ) > 1:
            extra_ids = [o["heading_id"] for o in occ[1:]]
            findings.append({
                "type": "DUPLICATE", "section": key,
                "heading": occ[0]["heading"][:70],
                "copies": len(occ),
                "delete_heading_ids": extra_ids,
                "detail": f"section {key} appears {len(occ)}x; delete the "
                          f"duplicate heading(s) + their body blocks",
            })

    # 1b. ARTIFACT blocks (no source needed): CLI furniture / unconverted
    # markdown / listing glyphs that leaked into the body. The CLI "Answer:"
    # label is only counted when a conversation token co-occurs in the same
    # block, so a legitimate "Answer:" in prose isn't flagged.
    artifact_blocks = []
    for b in blocks:
        t = b["type"]
        if t not in ("paragraph",) + HEADING_TYPES + ("callout", "quote",
                                                       "bulleted_list_item",
                                                       "numbered_list_item"):
            continue
        text = aq._block_text(b)
        hit = []
        for name, pat in _ARTIFACT_PATS:
            if not pat.search(text):
                continue
            if name == "cli-answer-label" and not _ARTIFACT_PATS[0][1].search(text):
                continue
            hit.append(name)
        if hit:
            artifact_blocks.append((b["id"], hit, text[:60]))
    if artifact_blocks:
        kinds = sorted({k for _, hits, _ in artifact_blocks for k in hits})
        findings.append({
            "type": "ARTIFACT", "section": None,
            "block_count": len(artifact_blocks),
            "kinds": kinds,
            "block_ids": [bid for bid, _, _ in artifact_blocks][:50],
            "detail": f"{len(artifact_blocks)} block(s) contain non-content "
                      f"artifacts ({', '.join(kinds)}); cli-* = raw `notebooklm ask` "
                      f"stdout uploaded unsanitized, markdown-* = NotebookLM markdown "
                      f"not run through build_answer_blocks. Rebuild the body via the "
                      f"converter (see Phase 4 step 2)",
        })

    # 1b2. BARE_MATH (no source needed): un-delimited LaTeX left in a TEXT span
    # renders as raw source and needs manual Ctrl+Shift+E. NotebookLM emits math
    # undelimited ~half the time and build_answer_blocks only converts delimited
    # math. Scan TEXT spans ONLY — an equation span's plain_text IS its expression,
    # so an all-spans scan would false-flag correctly-rendered equations.
    _BARE_MATH = re.compile(r"\\[A-Za-z]{2,}|[_^]\{|\\[{}]")
    bare_ids = []
    for b in blocks:
        t = b["type"]
        if t not in ("paragraph",) + HEADING_TYPES + ("quote",
                                                      "bulleted_list_item",
                                                      "numbered_list_item"):
            continue
        payload = b.get(t, {})
        spans = payload.get("rich_text", []) if isinstance(payload, dict) else []
        text_only = "".join(s.get("plain_text", "") for s in spans
                            if s.get("type") != "equation")
        if _BARE_MATH.search(text_only):
            bare_ids.append(b["id"])
    if bare_ids:
        findings.append({
            "type": "BARE_MATH", "section": None,
            "block_count": len(bare_ids), "block_ids": bare_ids[:50],
            "detail": f"{len(bare_ids)} block(s) carry un-delimited LaTeX in text "
                      f"(renders as raw source, needs manual Ctrl+Shift+E). Run "
                      f"wrap_math.py --page <id>, or let heal_paper_pages sweep it",
        })

    # 1b3. FURNITURE (no source needed): leaked arxiv HTML page chrome (nav / TOC /
    # report-issue widget / license line / javascript: links) that whole-fulltext
    # translation of an arxiv source drags into the body. Reuses strip_furniture's
    # high-precision marker set.
    try:
        from strip_furniture import FURNITURE
        fur_ids = [b["id"] for b in blocks
                   if b["type"] in ("paragraph",) + HEADING_TYPES + ("quote",
                       "bulleted_list_item", "numbered_list_item", "callout")
                   and FURNITURE.search(aq._block_text(b))]
    except Exception:
        fur_ids = []
    if fur_ids:
        findings.append({
            "type": "FURNITURE", "section": None,
            "block_count": len(fur_ids), "block_ids": fur_ids[:50],
            "detail": f"{len(fur_ids)} block(s) contain leaked arxiv HTML page chrome "
                      f"(nav/TOC/report-issue/license). Run strip_furniture.py --page "
                      f"<id>, or let heal_paper_pages sweep it",
        })

    # 1b4. FIGURES_MISSING (no source needed): the body references figures
    # (그림 N / Figure N / Fig. N) but the page has zero image blocks — figure
    # extraction (Phase 3) was skipped.
    fig_ref = re.compile(r"(?:그림|Figure|Fig\.?)\s*\d+")
    has_ref = any(b["type"] in ("paragraph",) + HEADING_TYPES
                  and fig_ref.search(aq._block_text(b)) for b in blocks)
    if has_ref and not any(b["type"] == "image" for b in blocks):
        findings.append({
            "type": "FIGURES_MISSING", "section": None,
            "block_count": 0, "block_ids": [],
            "detail": "body references figures but the page has 0 image blocks — run "
                      "extract_paper_figures.py --page <id> --arxiv <id> (or let "
                      "heal_paper_pages inject them)",
        })

    # 1b5. TABLE_FLATTENED (no source needed): dense flattened-table text blocks
    # while the page has no table images — a paper translated from arxiv HTML
    # fulltext whose <table>s landed as unreadable runs of numbers.
    try:
        from extract_paper_tables import _is_pure_table
        flat_ids = [b["id"] for b in blocks
                    if b["type"] in ("paragraph",) + HEADING_TYPES
                    and _is_pure_table(aq._block_text(b))]
    except Exception:
        flat_ids = []
    has_table_img = any(
        b["type"] == "image" and any(
            (c.get("plain_text", "") or "").lower().startswith("table")
            for c in (b.get("image", {}).get("caption") or []))
        for b in blocks)
    if flat_ids and not has_table_img:
        findings.append({
            "type": "TABLE_FLATTENED", "section": None,
            "block_count": len(flat_ids), "block_ids": flat_ids[:50],
            "detail": f"{len(flat_ids)} block(s) look like flattened table data and the "
                      f"page has no table images — run extract_paper_tables.py --page "
                      f"<id> --arxiv <id> (or let heal_paper_pages inject them)",
        })

    # 1c. HEADING_ECHO (no source needed): a paragraph that merely restates its
    # section heading, so the title shows twice (a heading block + an echo
    # paragraph). NotebookLM emits the section title as the first body line; the
    # assembler created a heading block AND kept that line as a paragraph.
    echo_ids = []
    last_head = None
    for b in blocks:
        t = b["type"]
        if t in HEADING_TYPES:
            last_head = aq._block_text(b)
        elif t == "paragraph" and last_head:
            s = aq._block_text(b)
            n = _echo_norm(s)
            if n and len(s) < 120 and n == _echo_norm(last_head):
                echo_ids.append(b["id"])
            # The echo is always the FIRST paragraph of a section; stop after
            # it so a later short paragraph that happens to equal the title
            # (e.g. a body line "Conclusion") isn't falsely flagged.
            last_head = None
    if echo_ids:
        findings.append({
            "type": "HEADING_ECHO", "section": None,
            "block_count": len(echo_ids), "block_ids": echo_ids,
            "detail": f"{len(echo_ids)} paragraph(s) merely repeat their section "
                      f"heading (title shown twice) — archive the echo paragraph",
        })

    # 1d. PARA_DUP (no source needed): the same substantial paragraph appearing
    # more than once on the page. This is the symptom of section-translation
    # boundary overlap (adjacent NotebookLM section answers repeat each other)
    # that the heading-only DUPLICATE check can't see. Only count paragraphs
    # long enough to be real content (>= 80 chars normalized), so short stock
    # lines ("Summary", a shared formula) don't false-trigger.
    seen, dup_para_ids = set(), []
    for b in blocks:
        if b["type"] not in ("paragraph", "bulleted_list_item",
                              "numbered_list_item", "quote"):
            continue
        # lowercased so two copies differing only in embedded-English casing
        # (sentence-boundary capitalization) still match.
        norm = re.sub(r"\s+", "", aq._block_text(b).lower())
        if len(norm) < 80:
            continue
        if norm in seen:
            dup_para_ids.append(b["id"])
        else:
            seen.add(norm)
    if dup_para_ids:
        findings.append({
            "type": "PARA_DUP", "section": None,
            "block_count": len(dup_para_ids), "block_ids": dup_para_ids[:50],
            "detail": f"{len(dup_para_ids)} paragraph(s) duplicate an earlier "
                      f"paragraph verbatim — likely section-translation boundary "
                      f"overlap; archive the repeats",
        })

    # BACKMATTER: a References / Bibliography / Acknowledgements section translated
    # into the body. The translated page should be Abstract..Conclusion only; a
    # bibliography run through translation is mangled (author names pick up a Korean
    # "그리고", citation numbers renumber per chunk, entries fragment across blocks).
    _backmatter = re.compile(
        r"^\s*\d*\.?\s*(references|bibliography|참고\s*문헌|"
        r"acknowledge?ments?|disclosure of funding)\b", re.I)
    bm = next((i for i, b in enumerate(blocks)
               if b["type"].startswith("heading")
               and _backmatter.match(aq._block_text(b).strip())), None)
    if bm is not None:  # a back-matter heading at all — even a lone one — is wrong
        tail = [b["id"] for b in blocks[bm:]]
        findings.append({
            "type": "BACKMATTER", "section": None,
            "block_count": len(tail), "block_ids": tail[:50],
            "detail": f"a back-matter section ('{aq._block_text(blocks[bm]).strip()[:40]}') was "
                      f"translated into the body — References/Acknowledgements must NOT be "
                      f"translated. Remove it: strip_backmatter.py --page <id> --apply",
        })

    # 2 + 3. COMPLETENESS / SUMMARIZATION (needs source). Measure only the FIRST
    # occurrence of each key so duplicates don't mask a short copy.
    src_text = load_source_text(args.source, args.arxiv)
    first = {}
    for s in sections:
        if s["key"] and s["key"] not in first:
            first[s["key"]] = s
    if src_text:
        ordered = [(s["key"], s["title"]) for s in first.values() if s["title"]]
        src_chars = source_section_chars(src_text, ordered)
        keys = list(first.keys())
        for key, s in first.items():
            sc = src_chars.get(key)
            tc = s["chars"]
            # A parent section whose subsections are their own page sections
            # legitimately holds only an intro; ratio-checking it against the
            # whole section's source (which spans all subsections) is a false
            # positive. Children use either dotted (2.1) or IEEE hyphen (III-A)
            # labels, so check both.
            is_parent = any(k != key and (k.startswith(key + ".")
                                          or k.startswith(key + "-"))
                            for k in keys)
            if is_parent:
                continue
            # A located source span below the floor means the section is
            # intrinsically tiny — nothing to lose.
            if sc is not None and sc < args.min_source:
                continue
            if tc < args.min_chars:
                findings.append({
                    "type": "CONTENT_LOSS", "section": key,
                    "heading": s["heading"][:70], "chars": tc,
                    "source_chars": sc,
                    "detail": f"section {key} has only {tc} chars (< {args.min_chars}) "
                              f"vs ~{sc} source chars; likely a timed-out/dropped "
                              f"translation — re-translate it",
                })
            elif sc and (tc / sc) < args.min_ratio:
                findings.append({
                    "type": "SUMMARIZED", "section": key,
                    "heading": s["heading"][:70], "chars": tc,
                    "source_chars": sc, "ratio": round(tc / sc, 3),
                    "detail": f"section {key}: {tc} translated vs {sc} source chars "
                              f"(ratio {tc/sc:.2f} < {args.min_ratio}); looks summarized — "
                              f"re-translate this section in its OWN NotebookLM call",
                })
    elif args.source or args.arxiv:
        findings.append({"type": "WARN", "section": None,
                         "detail": "source given but could not be parsed; "
                                   "summarization/completeness checks skipped"})

    # 4. MISSING (needs manifest).
    manifest = load_manifest(args.sections)
    if manifest:
        present = {s["key"] for s in sections if s["key"]}
        for key in manifest:
            if key not in present:
                findings.append({
                    "type": "MISSING", "section": key,
                    "detail": f"section {key} is in the Step 2-A list but has no "
                              f"heading on the page — translate and append it",
                })

    title = page_title(args.page)
    if args.json:
        print(json.dumps({"page": args.page, "title": title,
                          "section_count": len(first),
                          "sections": [{"key": k, "chars": v["chars"]}
                                       for k, v in first.items()],
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(f"Page: {title[:70]}  ({len(first)} distinct sections)")
        if not src_text and (args.source or args.arxiv) is None:
            print("(no --source/--arxiv: only the duplicate check ran)")
        print("-" * 68)
        table_src = {}
        if src_text:
            table_src = source_section_chars(
                src_text, [(s["key"], s["title"]) for s in first.values()])
        for k, v in first.items():
            sc = ""
            if src_text:
                got = table_src.get(k)
                sc = f"  src~{got}" if got is not None else "  src~?"
            print(f"  {k:>6}  {v['chars']:>6} chars{sc}  {v['title'][:42]}")
        print("-" * 68)
        if not findings:
            print("OK — no duplicate, echo, para-dup, artifact, short, summarized, or missing sections.")
        else:
            for f in findings:
                print(f"[{f['type']}] {f['detail']}")
                if f.get("delete_heading_ids"):
                    print(f"         delete heading ids: {f['delete_heading_ids']}")
                if f.get("block_ids"):
                    print(f"         artifact block ids: {f['block_ids']}")

    return 0 if not findings else 2


if __name__ == "__main__":
    sys.exit(main())
