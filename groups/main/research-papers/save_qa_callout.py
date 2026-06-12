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


def find_after_for_section(blocks: list[dict], section_query: str) -> str | None:
    """Return the ID of the LAST top-level block of the section whose heading
    contains `section_query`. Returns None if no heading matches.

    "Last block of the section" = the block immediately before the next heading
    at an equal or shallower level (or the end of the page).
    """
    q = section_query.strip().lower()
    start = None
    start_level = None
    for i, b in enumerate(blocks):
        lvl = HEADING_LEVEL.get(b["type"])
        if lvl and q in block_text(b).lower():
            start = i
            start_level = lvl
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
    """Prose sanitizer: collapse single \\n, strip $. NEVER apply to code blocks —
    newlines inside triple-backtick fences must be preserved verbatim."""
    MARK = "\x00PARA\x00"
    t = t.replace("\n\n", MARK).replace("\n", " ").replace(MARK, "\n\n")
    t = t.replace("$", "")
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


def _inline_rich_text(text: str) -> list[dict]:
    """Split inline text on **bold** markers, emitting rich_text spans with
    annotations. Non-bold spans are plain. Empty text yields an empty span."""
    out = []
    for part in re.split(r"(\*\*[^*\n]+?\*\*)", text):
        if not part:
            continue
        if len(part) >= 4 and part.startswith("**") and part.endswith("**"):
            body = part[2:-2]
            if body:
                out.append({"type": "text", "text": {"content": body[:2000]},
                            "annotations": {"bold": True}})
        else:
            out.append({"type": "text", "text": {"content": part[:2000]}})
    return out or [{"type": "text", "text": {"content": ""}}]


def _paragraph_blocks(text: str) -> list[dict]:
    return [{"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": _inline_rich_text(ch)}}
            for ch in chunks(text)]


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
        first = para.split("\n", 1)[0]
        m_head = re.match(r"^(#{1,6})\s+(.*)", first)
        if m_head:
            level = min(len(m_head.group(1)), 3)
            blocks.append(_heading_block(level, sanitize(m_head.group(2))))
            rest = para[len(first):].strip()
            if rest:
                blocks.extend(_paragraph_blocks(sanitize(rest)))
            continue
        if first.lstrip().startswith(("- ", "* ")):
            for line in para.split("\n"):
                m = re.match(r"^\s*[\-*]\s+(.*)", line)
                if m:
                    txt = sanitize(m.group(1))
                    if txt:
                        blocks.append({"object": "block", "type": "bulleted_list_item",
                                       "bulleted_list_item": {"rich_text": _inline_rich_text(txt[:2000])}})
            continue
        if re.match(r"^\s*\d+\.\s", first):
            for line in para.split("\n"):
                m = re.match(r"^\s*\d+\.\s+(.*)", line)
                if m:
                    txt = sanitize(m.group(1))
                    if txt:
                        blocks.append({"object": "block", "type": "numbered_list_item",
                                       "numbered_list_item": {"rich_text": _inline_rich_text(txt[:2000])}})
            continue
        if first.lstrip().startswith("> "):
            quote = "\n".join(re.sub(r"^\s*>\s?", "", ln) for ln in para.split("\n"))
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": _inline_rich_text(sanitize(quote)[:2000])}})
            continue
        blocks.extend(_paragraph_blocks(sanitize(para)))
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
        blocks.append(_code_block(m.group(2), m.group(1)))
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
    q = sanitize(question)[:2000]
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
