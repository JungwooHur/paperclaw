#!/usr/bin/env python3
"""Save a paper Q&A as a top-level 💡 callout on a Notion page.

ALWAYS use this script for paper Q&A. Hand-rolled curl PATCHes have repeatedly
landed callouts inside random unrelated sections (the API silently nests them
when the URL parent isn't the page itself). This script enforces the safe path:

  1) PATCH URL is always /blocks/PAGE_ID/children (page as parent).
  2) `after` is resolved to a top-level block of the named section.
  3) After PATCH, top-level children are re-fetched to verify the new callout
     is a direct child of the page. If not, it is deleted and an error raised.

It ALSO refuses to write to the wrong paper. `--expect-title` is required: the
script fetches the target page's Title (and Paper URL) and aborts BEFORE writing
unless the expected substring is present. This is the structural guard against
the recurring bug where an agent reuses a stale page ID from an earlier task and
silently files a Q&A under an unrelated paper. Prose rules alone never stopped
it; this makes the wrong page a hard failure.

Usage:
  python3 save_qa_callout.py \
      --page <page_id> \
      --expect-title "Distinctive-Fragment" # distinctive title fragment or arxiv id
      --question "Q: ..." \
      --answer-file /tmp/answer.md \
      --section "4.3"           # heading-text fragment; omit to append at end

The answer file is split into paragraphs on blank lines and chunked to <=2000
chars per paragraph rich_text block.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request, urllib.error

from wrap_math import wrap_math_text  # Prevent: auto-wrap bare LaTeX -> equations

API = "https://api.notion.com/v1"


def headers() -> dict:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        sys.exit("NOTION_TOKEN not set")
    return {
        "Authorization": f"Bearer {tok}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def api_get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers=headers())
    return json.loads(urllib.request.urlopen(req).read())


def api_patch(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path, method="PATCH",
        data=json.dumps(body).encode(), headers=headers(),
    )
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"PATCH {path} failed {e.code}: {e.read().decode(errors='ignore')[:500]}")


def api_delete(block_id: str) -> None:
    req = urllib.request.Request(
        f"{API}/blocks/{block_id}", method="DELETE", headers=headers()
    )
    urllib.request.urlopen(req).read()


def fetch_page_identity(page_id: str) -> tuple[str, str]:
    """Return (title, urls) for a page. `title` is the title-type property's
    plain text; `urls` concatenates any url-type property values (e.g. the
    arxiv 'Paper URL'). Used to verify --page is the paper we think it is."""
    d = api_get(f"/pages/{page_id}")
    props = d.get("properties", {})
    title, urls = "", []
    for v in props.values():
        if v.get("type") == "title":
            title = "".join(r["plain_text"] for r in v.get("title", []))
        elif v.get("type") == "url" and v.get("url"):
            urls.append(v["url"])
    return title, " ".join(urls)


def fetch_top_children(page_id: str) -> list[dict]:
    cur, out = None, []
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cur:
            path += f"&start_cursor={cur}"
        d = api_get(path)
        out.extend(d["results"])
        if d.get("has_more"):
            cur = d["next_cursor"]
        else:
            break
    return out


def block_text(b: dict) -> str:
    t = b["type"]
    rts = b.get(t, {}).get("rich_text", [])
    return "".join(r["plain_text"] for r in rts)


HEADING_LEVEL = {"heading_1": 1, "heading_2": 2, "heading_3": 3}


_QPREFIX = re.compile(r"^\s*(?:Q\s*[:.]\s*)+", re.I)


def strip_q_prefix(question: str) -> str:
    """Collapse repeated "Q:" markers.

    The caller passes `--question "Q: ..."` and the renderer adds its own marker, so
    saved callouts read "Q: Q: ...". Cosmetic on the page, but it also skews the
    dedup comparison that keeps a question from being filed twice.
    """
    return _QPREFIX.sub("", question or "").strip()


def find_after_for_section(blocks: list[dict], section_query: str) -> str | None:
    """Return the ID of the LAST top-level block of the section whose heading
    contains `section_query`. Returns None if no heading matches.

    "Last block of the section" = the block immediately before the next heading
    at an equal or shallower level (or the end of the page).
    """
    q = section_query.strip().lower().rstrip(".")
    start = None
    start_level = None
    # A section LABEL must match as a label, not as a substring. Plain `q in text`
    # makes "4" match the heading "3.4. …" and "4.1" match "3.4.1. …" — both appear
    # earlier in the page, so the callout lands in a different section entirely.
    # Labels are anchored to the start of the heading and must end on a boundary,
    # so "3.2" no longer matches "3.2.2". A non-label query (e.g. "Method") keeps
    # the old substring behaviour, which is what makes it useful.
    label = re.fullmatch(r"[0-9]+(?:\.[0-9]+)*|[ivxlc]+(?:-[a-z])?|[a-z](?:\.[0-9]+)*",
                         q) is not None
    # The label ends here only if no further number follows it: a trailing "." is
    # part of the label ("4."), but ".2" means this heading is a deeper section.
    needle = (re.compile(r"^\s*" + re.escape(q) + r"(?![0-9])(?!\.[0-9])")
              if label else None)
    for i, b in enumerate(blocks):
        lvl = HEADING_LEVEL.get(b["type"])
        if not lvl:
            continue
        text = block_text(b).lower()
        if needle.search(text) if needle else (q in text):
            start, start_level = i, lvl
            break
    if start is None and label:
        # The page may not label its headings at all (a translated title only).
        # Fall back to the substring match rather than refusing to place.
        for i, b in enumerate(blocks):
            lvl = HEADING_LEVEL.get(b["type"])
            if lvl and q in block_text(b).lower():
                start, start_level = i, lvl
                break
    if start is None:
        return None
    end = len(blocks)
    for j in range(start + 1, len(blocks)):
        lvl = HEADING_LEVEL.get(blocks[j]["type"])
        if lvl and lvl <= start_level:
            end = j
            break
    last = blocks[end - 1]
    return last["id"]


def chunks(text: str, limit: int = 1900) -> list[str]:
    out = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def sanitize(t: str) -> str:
    """Prose sanitizer: collapse single \\n. NEVER apply to code blocks — newlines
    inside triple-backtick fences must be preserved verbatim.

    Does NOT strip '$': LaTeX math delimiters must survive to _inline_rich_text,
    which turns $...$ / $$...$$ into Notion equation objects and only then drops
    any leftover stray '$' from real prose. (The old strip deleted every '$',
    leaving bare LaTeX as plain text so every formula needed a manual Ctrl+Shift+E.)"""
    MARK = "\x00PARA\x00"
    t = t.replace("\n\n", MARK).replace("\n", " ").replace(MARK, "\n\n")
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


NOTION_CODE_LANGS = {
    "python", "bash", "shell", "javascript", "typescript", "json", "yaml",
    "markdown", "plain text", "sql", "go", "rust", "c", "c++", "java",
    "cuda", "diff", "html", "css", "lua", "ruby", "scala",
}
CODE_LANG_ALIASES = {
    "sh": "bash", "js": "javascript", "ts": "typescript", "yml": "yaml",
    "md": "markdown", "text": "plain text", "plaintext": "plain text",
    "py": "python", "cpp": "c++", "rb": "ruby",
}


def _normalize_lang(lang: str) -> str:
    lang = (lang or "").strip().lower()
    lang = CODE_LANG_ALIASES.get(lang, lang)
    return lang if lang in NOTION_CODE_LANGS else "plain text"


# LaTeX math -> Notion inline equation objects. NotebookLM emits math wrapped in
# $...$ / $$...$$ (also \(...\) / \[...\]). The '$' inline forms require a
# non-space char right inside each delimiter, so prose money ("$5 and $10") is not
# mistaken for math. Order matters: $$..$$ / \[..\] are tried before $..$.
#
# The backslash delimiters accept ONE OR TWO backslashes, and that is load-bearing.
# The Step 2-B prompt shows the delimiters escaped for the shell (`\\(…\\)`), and
# NotebookLM copies that escaping into its answer, so real answers arrive with
# `\\(m = n\\)`. Matching only `\(` made the regex start at the SECOND backslash:
# the first was left behind in the prose and the closing one was swallowed into the
# expression, producing text `…\` + equation `m = n\`. A lone trailing backslash is
# invalid KaTeX, so EVERY equation on such a page rendered as a red error — the
# defect heal_equations exists to repair after the fact. This is where it was born.
_MATH = re.compile(
    r"\$\$(?P<disp>.+?)\$\$"                          # $$ ... $$
    r"|\\{1,2}\[(?P<dispb>.+?)\\{1,2}\]"              # \[ ... \]  /  \\[ ... \\]
    r"|\$(?![\s$])(?P<inl>[^$\n]+?)(?<![\s$])\$"      # $ ... $
    r"|\\{1,2}\((?P<inlb>.+?)\\{1,2}\)",              # \( ... \)  /  \\( ... \\)
    re.DOTALL)


def _math_and_text(text: str, bold: bool) -> list[dict]:
    """Split one (optionally bold) run into inline equation objects + text, dropping
    stray unbalanced '$'. The bold annotation carries onto equations too, so a bold
    run that contains math stays fully bold."""
    ann = {"annotations": {"bold": True}} if bold else {}
    out: list[dict] = []
    pos = 0
    for m in _MATH.finditer(text):
        pre = text[pos:m.start()].replace("$", "")
        if pre:
            out.append({"type": "text", "text": {"content": pre[:2000]}, **ann})
        expr = next(g for g in m.groups() if g is not None).strip()
        if expr:
            out.append({"type": "equation", "equation": {"expression": expr[:1000]}, **ann})
        pos = m.end()
    tail = text[pos:].replace("$", "")
    if tail:
        out.append({"type": "text", "text": {"content": tail[:2000]}, **ann})
    return out


def _inline_rich_text(text: str) -> list[dict]:
    """Rich_text spans for inline text: **bold** -> annotations, $...$ / \\(...\\)
    LaTeX -> Notion inline equation objects, everything else plain. Bold is parsed
    first so a bold run containing math stays fully bold. Empty text -> empty span."""
    text = wrap_math_text(text)   # bare LaTeX -> $...$ so it becomes equations
    out: list[dict] = []
    for seg in re.split(r"(\*\*[^*\n]+?\*\*)", text):
        if not seg:
            continue
        bold = len(seg) >= 4 and seg.startswith("**") and seg.endswith("**")
        out.extend(_math_and_text(seg[2:-2] if bold else seg, bold))
    return out or [{"type": "text", "text": {"content": ""}}]


def _paragraph_blocks(text: str) -> list[dict]:
    return [{"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": _inline_rich_text(ch)}}
            for ch in chunks(text)]


# Same one-or-two-backslash rule as _MATH — see the note there.
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\{1,2}\[(.+?)\\{1,2}\]", re.DOTALL)


def _prose_paragraphs(text: str) -> list[dict]:
    """Sanitize prose into paragraph blocks, but pull any DISPLAY equation
    ($$...$$ or \\[...\\]) out into its own Notion equation block — even mid-
    paragraph, where sanitize has by then folded its standalone line into the
    surrounding prose. Inline $...$ / \\(...\\) stay in their paragraph (handled
    by _inline_rich_text)."""
    text = sanitize(text)
    out: list[dict] = []
    pos = 0
    for m in _DISPLAY_MATH.finditer(text):
        pre = text[pos:m.start()].strip()
        if pre:
            out.extend(_paragraph_blocks(pre))
        expr = (m.group(1) or m.group(2) or "").strip()
        if expr:
            out.append({"object": "block", "type": "equation",
                        "equation": {"expression": expr[:1000]}})
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        out.extend(_paragraph_blocks(tail))
    return out


def _heading_block(level: int, text: str) -> dict:
    level = min(max(level, 1), 3)
    key = f"heading_{level}"
    return {"object": "block", "type": key,
            key: {"rich_text": _inline_rich_text(text[:2000])}}


def _code_block(code: str, lang: str) -> dict:
    """Code block with newlines preserved. Splits long code into ≤2000-char
    rich_text spans (a single code block can hold multiple spans)."""
    code = code.rstrip("\n")
    spans = []
    remaining = code
    while remaining:
        spans.append({"type": "text", "text": {"content": remaining[:2000]}})
        remaining = remaining[2000:]
    if not spans:
        spans = [{"type": "text", "text": {"content": ""}}]
    return {"object": "block", "type": "code",
            "code": {"rich_text": spans, "language": _normalize_lang(lang)}}


def _is_md_table(para: str) -> bool:
    lines = [ln for ln in para.split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    if not all(ln.lstrip().startswith("|") for ln in lines):
        return False
    return bool(re.match(r"^\s*\|?\s*:?-+", lines[1]))


def _group_list_items(para: str, marker_re: str) -> list[str]:
    """Split a list paragraph into item texts, merging continuation lines.

    A list item's wrapped/indented continuation line does NOT start with the
    marker; it belongs to the item above. The previous version dropped such
    lines, losing content (e.g. '* CoT:\\n    explanation...' kept only 'CoT:').
    """
    items: list[str] = []
    for line in para.split("\n"):
        m = re.match(marker_re, line)
        if m:
            # Append even when empty (e.g. "* " with text on the next line), so
            # continuation lines merge into THIS item, not the previous one.
            items.append(sanitize(m.group(1)))
        elif line.strip() and items:
            items[-1] = (items[-1] + " " + sanitize(line)).strip()
    return [it for it in items if it]


_MATH_LANGS = {"math", "latex", "tex", "equation"}
_KOREAN_RE = re.compile(r"[가-힣]")
# Real code, not a formula. Braces are deliberately NOT a code signal — `e^{i\theta}`
# is ordinary LaTeX, and treating `{}` as code kept genuine formulas out.
_CODE_HINT = re.compile(r"\b(def|class|import|from|return|for|while|if|else|print|"
                        r"lambda|self|None|True|False|const|let|var|function)\b"
                        r"|;|//|#\s|\bpip\b")
# A STRONG signal — one of these must be present. Two weak ones were removed after a
# whole-DB dry run turned them into false positives: a lone `·` matched a weather
# forecast used as a bullet separator, and a bare `[_^][A-Za-z0-9]` matched the `_r`
# in `car_rental`, i.e. every snake_case identifier. A subscript therefore has to
# follow a SINGLE-letter variable (`x_m`, `q_t`) sitting on a word boundary.
_MATH_HINT = re.compile(r"\\[a-zA-Z]+|[_^]\{|\b[A-Za-z]\d*[_^]"
                        r"|=|≈|≤|≥|≠|≡|→|⊗|⊤|∑|∏|∫|∇|∂|√|∈|∉|⊂|⊆|∪|∩|±|∞"
                        r"|[α-ωΑ-Ω]")
# Data/prose that a math signal alone would wave through: a python dict repr carries
# `=`, and a weather forecast used `·` as a bullet separator. `%` is a LaTeX comment
# char (never valid in an expression) and a period followed by a lowercase word is a
# sentence. A quote only counts when it OPENS a string — `'ident'` — because a lone
# or trailing `'` is prime notation (`reward'`, `o'_{t+k}`), which is real maths.
_NOT_MATH = re.compile(r"\"|(?<![A-Za-z0-9_])'[^']*'|(?<!\\)%|\.\s+[a-z]")


def is_formula_fence(lang: str, text: str) -> bool:
    r"""True if a fenced block is really a FORMULA and should become a Notion
    equation block instead of a code block.

    Why this exists: body math arrives as `$…$` and becomes equation objects, but an
    ANSWER often writes its math inside a ``` fence, which the converter faithfully
    turns into monospace code — so the same formula renders as maths in the body and
    as plain text in a Q&A. Answers should read like the body.

    Deliberately conservative, because the failure modes are worse than a missed
    conversion. Measured against 26 real fenced blocks from the DB, it converts 8
    single-line formulas and keeps the rest as code:
      * Korean inside  -> keep. Converting would put prose INSIDE an equation, the
        exact corruption heal_mangled_math exists to undo.
      * multi-line     -> keep. These are ASCII-art matrices and flow diagrams whose
        meaning is the alignment; KaTeX would destroy it.
      * code / a declared language other than math -> keep.
      * quotes, a `%`, or a sentence (`. ` + lowercase) -> keep. A whole-DB dry run
        found these hiding behind a real math signal: a python dict repr carries `=`,
        and a weather forecast used `·` as a bullet separator.
    """
    lang = (lang or "").strip().lower()
    body = (text or "").strip()
    if not body:
        return False
    if lang in _MATH_LANGS:
        return True                       # declared intent — trust it
    if lang not in ("", "plain text", "plaintext", "text"):
        return False
    if _KOREAN_RE.search(body) or "\n" in body or re.search(r"\s{2,}", body):
        return False
    if _CODE_HINT.search(body) or _NOT_MATH.search(body):
        return False
    if not _MATH_HINT.search(body):
        return False
    if body.count("{") != body.count("}") or body.rstrip().endswith("\\"):
        return False                      # would be invalid KaTeX
    return len(body) <= 200


def _prose_blocks(prose_md: str) -> list[dict]:
    """Convert prose markdown (no fenced code blocks) into Notion blocks."""
    blocks: list[dict] = []
    for para in re.split(r"\n\n+", prose_md.strip()):
        para = para.strip()
        if not para:
            continue
        if _is_md_table(para):
            # Render markdown tables as a plain-text code block so column
            # alignment is preserved without building Notion table schema.
            blocks.append(_code_block(para, "markdown"))
            continue
        if re.fullmatch(r"(?:---+|\*\*\*+|___+)", para):
            # Markdown horizontal rule -> Notion divider (else it renders as a
            # literal "---" paragraph).
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue
        first = para.split("\n", 1)[0]
        m_head = re.match(r"^(#{1,6})\s+(.*)", first)
        if m_head:
            level = min(len(m_head.group(1)), 3)
            blocks.append(_heading_block(level, sanitize(m_head.group(2))))
            rest = para[len(first):].strip()
            if rest:
                blocks.extend(_prose_paragraphs(rest))
            continue
        if first.lstrip().startswith(("- ", "* ")):
            for txt in _group_list_items(para, r"^\s*[\-*]\s+(.*)"):
                blocks.append({"object": "block", "type": "bulleted_list_item",
                               "bulleted_list_item": {"rich_text": _inline_rich_text(txt[:2000])}})
            continue
        if re.match(r"^\s*\d+\.\s", first):
            for txt in _group_list_items(para, r"^\s*\d+\.\s+(.*)"):
                blocks.append({"object": "block", "type": "numbered_list_item",
                               "numbered_list_item": {"rich_text": _inline_rich_text(txt[:2000])}})
            continue
        if first.lstrip().startswith("> "):
            quote = "\n".join(re.sub(r"^\s*>\s?", "", ln) for ln in para.split("\n"))
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": _inline_rich_text(sanitize(quote)[:2000])}})
            continue
        blocks.extend(_prose_paragraphs(para))
    return blocks


CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


def build_answer_blocks(answer_md: str) -> list[dict]:
    """Convert answer markdown into Notion child blocks.

    Handles:
      ```lang\\n…``` fenced code blocks  -> code blocks (newlines preserved)
      # / ## / ### / #### headings       -> heading_1 / heading_2 / heading_3 (clamped)
      - or *  list items                 -> bulleted_list_item
      N. list items                      -> numbered_list_item
      > quote                            -> quote
      | md | table |                     -> code block (markdown) to preserve alignment
      **bold** inline                    -> rich_text with annotations.bold
      otherwise                          -> paragraph
    Newlines inside code fences are preserved verbatim; outside, single
    newlines inside a paragraph collapse to spaces per `sanitize()`.
    """
    blocks: list[dict] = []
    pos = 0
    for m in CODE_FENCE_RE.finditer(answer_md):
        prose = answer_md[pos:m.start()]
        if prose.strip():
            blocks.extend(_prose_blocks(prose))
        lang, body = m.group(1), m.group(2)
        if is_formula_fence(lang, body):
            blocks.append({"object": "block", "type": "equation",
                           "equation": {"expression": body.strip()[:1000]}})
        else:
            blocks.append(_code_block(body, lang))
        pos = m.end()
    tail = answer_md[pos:]
    if tail.strip():
        blocks.extend(_prose_blocks(tail))
    return blocks


def build_callout(question: str, answer_md: str) -> dict:
    """Build the standard Paper-DB Q&A block.

    Layout (matches the toggle-style reference pages):

        callout (icon: 💡, gray_background, empty rich_text)
        └── toggle (rich_text: "Q: ...question...")
            └── answer blocks (heading_3 / paragraph / lists)

    The toggle keeps the answer collapsed by default so the page stays scannable.
    """
    # Exactly one "Q: " marker, whatever the caller passed. Callers disagree —
    # some prefix it, some do not, and saved callouts read "Q: Q: …".
    q = "Q: " + strip_q_prefix(sanitize(question))[:1990]
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "💡"},
            "color": "gray_background",
            "rich_text": [],
            "children": [{
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": q}}],
                    "children": build_answer_blocks(answer_md),
                },
            }],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True, help="Notion page ID")
    ap.add_argument("--expect-title", required=True,
                    help="Distinctive substring of the paper's title (or its arxiv id). "
                         "The script aborts if it is not found in the target page's "
                         "Title/Paper URL — guards against writing to the wrong paper.")
    ap.add_argument("--question", required=True, help="Question text (single line)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--answer", help="Answer markdown")
    g.add_argument("--answer-file", help="Path to answer markdown file")
    ap.add_argument("--section", default=None,
                    help="Heading-text fragment for placement (e.g. '4.3'). Omit to append at end.")
    args = ap.parse_args()

    answer_md = args.answer if args.answer else open(args.answer_file, encoding="utf-8").read()

    # Page-identity guard: refuse to write before confirming --page is the
    # paper the caller claims. Catches stale/reused page IDs.
    page_title, page_urls = fetch_page_identity(args.page)
    needle = args.expect_title.strip().lower()
    if needle not in f"{page_title} {page_urls}".lower():
        sys.exit(
            f"FAIL: --expect-title {args.expect_title!r} not found on target page.\n"
            f"  page {args.page}\n"
            f"  actual title: {page_title!r}\n"
            f"  This is almost certainly the WRONG paper. Re-resolve the page ID from "
            f"the paper title (query the Paper DB) before saving. Nothing was written."
        )
    print(f"page identity OK: {page_title[:80]!r}", file=sys.stderr)

    top = fetch_top_children(args.page)
    after_id = None
    if args.section:
        after_id = find_after_for_section(top, args.section)
        if after_id is None:
            sys.exit(f"section not found: '{args.section}' (no top-level heading matched)")

    body: dict = {"children": [build_callout(args.question, answer_md)]}
    if after_id:
        body["after"] = after_id

    res = api_patch(f"/blocks/{args.page}/children", body)
    new_id = res["results"][0]["id"]
    print(f"inserted callout id={new_id}", file=sys.stderr)

    # MANDATORY verification: new block must be a direct child of the page.
    time.sleep(0.4)
    top_after = fetch_top_children(args.page)
    if new_id not in {b["id"] for b in top_after}:
        try:
            api_delete(new_id)
            print(f"ROLLED BACK nested insert {new_id}", file=sys.stderr)
        except Exception:
            pass
        sys.exit("FAIL: callout landed nested instead of top-level — verify your --page is the actual page ID")
    print(f"OK: top-level callout {new_id} placed after {after_id or '(end of page)'}")


if __name__ == "__main__":
    main()
