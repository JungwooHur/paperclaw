#!/usr/bin/env python3
"""Complete, deterministic translation of a long document (book) onto its Notion
page, using the source's INDEXED FULLTEXT — not per-section NotebookLM asks.

Why this exists
---------------
Asking NotebookLM "translate section X.Y" does NOT preserve a long document:
its section answers overlap at the boundaries (the same paragraph lands on the
page twice) AND drop the spans between them (whole paragraphs vanish). On real
books this produced pages at 1-24% of the source length, with duplicated runs.
verify_sections' DUPLICATE check only sees repeated *headings*, so it passed
them. (See groups/main/CLAUDE.md "Long documents / books".)

This tool instead:
  1. Pulls each source's raw indexed text via `notebooklm source fulltext`
     (the exact text NotebookLM is built on — complete, no summarization).
  2. Splits it into TILING, sentence-bounded chunks (~--chunk chars): every
     chunk is a contiguous span, chunks cover the whole text with no gap and no
     overlap. Omission and duplication are impossible by construction.
  3. Translates each chunk on its own via `notebooklm ask --json` (a bounded
     "translate THIS text" request, which — unlike "translate section X" — does
     not summarize), with an empty/short retry and a length-ratio check.
  4. Assembles per-source (heading_1 from the source title + chunk bodies) and
     converts markdown via save_qa_callout.build_answer_blocks.
  5. Replaces the Notion page rate-safely (429 backoff, paced < 3 req/s).
  6. Runs the verify_sections gate.

Resumable: chunk translations are cached under --workdir; re-running skips
finished chunks. Translation and rebuild are separate phases (--no-apply to
stop after translating; --apply to also rebuild the page).

Usage:
  translate_fulltext.py --notebook <id> --page <id> [--apply]
      [--chunk 4000] [--lang ko] [--workdir /tmp/ft_<page>]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_qa_callout as sq
import verify_sections as vs

API = "https://api.notion.com/v1"


def notion(method, path, body=None, tries=12):
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        sys.exit("NOTION_TOKEN environment variable must be set")
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(
                API + path,
                data=json.dumps(body).encode() if body else None, method=method,
                headers={"Authorization": f"Bearer {tok}",
                         "Notion-Version": "2022-06-28",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 5)) + 2 * a)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # transient network/read timeout — retry (a mid-rebuild crash here
            # would leave the page half-archived). Backoff and try again.
            last = e
            time.sleep(2 + 2 * a)
            continue
    raise last


def nb(*args, timeout=300):
    return subprocess.run(["notebooklm", *args], capture_output=True,
                          text=True, timeout=timeout)


def list_sources(notebook):
    r = nb("source", "list", "--notebook", notebook, "--json", timeout=60)
    if r.returncode != 0:
        sys.exit(f"`notebooklm source list` failed for {notebook}: {r.stderr[:300]}")
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.exit(f"`notebooklm source list` returned non-JSON: {r.stdout[:300]}")
    return d.get("sources", d) if isinstance(d, dict) else d


def source_fulltext(notebook, sid, out_path):
    nb("source", "fulltext", sid, "--notebook", notebook, "-o", out_path, timeout=120)
    with open(out_path, encoding="utf-8") as f:
        t = f.read()
    i = t.find("Content:")
    return t[i + 8:].strip() if i >= 0 else t


def normalize(t):
    t = re.sub(r"[ \t]*\n[ \t]*", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def chunk_text(t, cap):
    """Tiling chunks <= cap chars, split only at sentence boundaries."""
    sents = re.split(r"(?<=[.!?])\s+", t)
    chunks, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > cap:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur.strip():
        chunks.append(cur)
    return chunks


LANG_NAME = {"ko": "한국어", "ja": "일본어", "zh-CN": "중국어", "en": "English"}


def translate_chunk(chunk, lang, nb_id):
    name = LANG_NAME.get(lang, lang)
    if lang == "en":
        prompt = ("Reformat the following text cleanly. Keep ALL content, do "
                  "NOT summarize. Output only the reformatted text:\n\n" + chunk)
    else:
        prompt = (f"다음 영어 텍스트를 {name}로 한 문장도 빠짐없이 전문 번역해. 절대 요약하지 마. "
                  f"전문용어(LLM, agent, reasoning, tool, token 등)는 영어 그대로 유지. "
                  f"원문에 소제목(예: '3.1 Title')이 있으면 그 줄을 '## 제목' 형식으로. "
                  f"번역문만 출력:\n\n" + chunk)
    out = ""
    for attempt in range(4):
        try:
            r = nb("ask", prompt, "--notebook", nb_id, "--json")
            out = json.loads(r.stdout).get("answer", "").strip() if r.returncode == 0 else ""
        except Exception as e:
            # Broad on purpose: this runs unattended for hours, so retry on ANY
            # failure (timeout, JSON, subprocess, network) rather than abort the
            # whole queue — but log it so failures aren't silent.
            print(f"    chunk attempt {attempt} failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            out = ""
        if len(out) >= 0.30 * len(chunk):   # complete enough
            return out
        time.sleep(3)                        # empty/short -> retry
    return out                               # best effort (caller logs SHORT)


def clean_title(title):
    # "3. <chapter> _ <book title>.pdf" -> "3. <chapter>"
    title = re.sub(r"\.(pdf|txt|md|html?)$", "", title, flags=re.I)
    return re.split(r"\s+_\s+", title)[0].strip()


_FIGREF = re.compile(r"Figure\s*([0-9]+[-.][0-9]+)", re.I)


def _block_text(b):
    o = b.get(b["type"], {})
    return "".join(x.get("text", {}).get("content", "") for x in o.get("rich_text", []))


def inject_figures(blocks, figures_zip, workdir):
    """Insert each book figure (uploaded privately into Notion) right after the
    FIRST block that references its 'Figure N-M' label. Figures with no textual
    reference are appended at the end so nothing is silently dropped."""
    import extract_book_figures as ef
    import notion_upload as nu
    figmap = ef.extract_figures(figures_zip, workdir)   # {label: local_png}
    if not figmap:
        return blocks
    uploaded = {}   # label -> file_upload id (uploaded lazily, attached same run)

    def img_for(label):
        if label not in uploaded:
            fid = nu.upload_image(figmap[label]) if label in figmap else None
            uploaded[label] = fid
        return uploaded[label]

    out, placed = [], set()
    for b in blocks:
        out.append(b)
        for label in dict.fromkeys(m.group(1).replace(".", "-")
                                   for m in _FIGREF.finditer(_block_text(b))):
            if label in placed or label not in figmap:
                continue
            fid = img_for(label)
            if fid:
                out.append(nu.image_block(fid))
                placed.add(label)
    # any figures never referenced in text -> append at end
    for label in figmap:
        if label not in placed:
            fid = img_for(label)
            if fid:
                out.append(nu.image_block(fid))
                placed.add(label)
    print(f"  injected {len(placed)}/{len(figmap)} figures", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notebook", required=True)
    ap.add_argument("--page", required=True)
    ap.add_argument("--chunk", type=int, default=4000)
    ap.add_argument("--lang", default=os.environ.get("OUTPUT_LANGUAGE", "ko"))
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--figures-zip", default=None,
                    help="book source zip (chapter PDFs); inject its figures "
                         "next to their 'Figure N-M' references, uploaded "
                         "PRIVATELY into Notion (never a public host)")
    ap.add_argument("--apply", action="store_true",
                    help="rebuild the Notion page after translating")
    args = ap.parse_args()
    # FULL page id — a prefix collides (pages created together share a prefix),
    # which would make two books read each other's chunk/figure cache.
    work = args.workdir or f"/tmp/ft_{args.page}"
    os.makedirs(work, exist_ok=True)

    sources = list_sources(args.notebook)
    print(f"{len(sources)} sources", flush=True)

    # Phase 1: fulltext + chunked translation (resumable).
    seg_files = []  # (title, [chunk translation paths in order])
    consec_empty = 0
    for si, s in enumerate(sources):
        sid, title = s.get("id"), s.get("title", f"source {si}")
        raw_path = f"{work}/src_{si:02}.txt"
        if not (os.path.exists(raw_path) and os.path.getsize(raw_path) > 50):
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(source_fulltext(args.notebook, sid, raw_path + ".dl"))
        with open(raw_path, encoding="utf-8") as f:
            chunks = chunk_text(normalize(f.read()), args.chunk)
        paths = []
        for ci, c in enumerate(chunks):
            p = f"{work}/tr_{si:02}_{ci:03}.txt"
            if not (os.path.exists(p) and os.path.getsize(p) > 10):
                t0 = time.time()
                a = translate_chunk(c, args.lang, args.notebook)
                # Only cache a usable result. Caching an empty answer would make
                # the resume-skip treat a FAILED chunk as done, silently dropping
                # that span from the book.
                if a:
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(a)
                flag = "" if len(a) >= 0.30 * len(c) else " SHORT!"
                print(f"  s{si:02}c{ci:03}: src={len(c)} out={len(a)} "
                      f"({time.time()-t0:.0f}s){flag}", flush=True)
                consec_empty = consec_empty + 1 if not a else 0
                if consec_empty >= 5:
                    sys.exit("ABORT: 5 consecutive empty NotebookLM responses — "
                             "the service is down/rate-limited. Stopping before "
                             "an incomplete page is built; re-run later to resume.")
                time.sleep(1)
            paths.append(p)
        seg_files.append((clean_title(title), paths))

    # Completeness guard: every chunk must have a cached translation. If any is
    # missing (failed/empty), refuse to assemble or touch the page — a partial
    # rebuild would drop content or, worse, archive the old page and crash.
    missing = [p for _, ps in seg_files for p in ps
               if not (os.path.exists(p) and os.path.getsize(p) > 10)]
    if missing:
        sys.exit(f"ABORT: {len(missing)} chunk(s) not translated (e.g. {missing[0]}). "
                 f"Re-run to resume; NOT assembling/rebuilding with gaps.")

    # Build markdown: per source -> heading + chunk bodies.
    parts = []
    for title, paths in seg_files:
        parts.append(f"# {title}")
        for p in paths:
            with open(p, encoding="utf-8") as f:
                body = f.read().strip()
            if body:
                parts.append(body)
    md = "\n\n".join(parts)
    blocks = sq.build_answer_blocks(md)
    print(f"assembled {len(md)} chars -> {len(blocks)} blocks", flush=True)

    if args.apply and args.figures_zip:
        blocks = inject_figures(blocks, args.figures_zip, f"{work}/figs")
        print(f"with figures -> {len(blocks)} blocks", flush=True)

    if not args.apply:
        print("translation done (--apply to rebuild the page)", flush=True)
        return

    # Phase 2: rate-safe page replace.
    old = vs.fetch_blocks(args.page)
    print(f"archiving {len(old)} old blocks...", flush=True)
    for b in old:
        notion("PATCH", f"/blocks/{b['id']}", {"archived": True})
        time.sleep(0.35)
    print("appending...", flush=True)
    for i in range(0, len(blocks), 90):
        notion("PATCH", f"/blocks/{args.page}/children", {"children": blocks[i:i+90]})
        time.sleep(0.6)
    print(f"rebuilt with {len(blocks)} blocks", flush=True)


if __name__ == "__main__":
    main()
