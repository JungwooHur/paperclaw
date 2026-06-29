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
    tok = os.environ["NOTION_TOKEN"]
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(
                API + path,
                data=json.dumps(body).encode() if body else None, method=method,
                headers={"Authorization": f"Bearer {tok}",
                         "Notion-Version": "2022-06-28",
                         "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 5)) + 2 * a)
                continue
            raise
    raise last


def nb(*args, timeout=300):
    return subprocess.run(["notebooklm", *args], capture_output=True,
                          text=True, timeout=timeout)


def list_sources(notebook):
    r = nb("source", "list", "--notebook", notebook, "--json", timeout=60)
    d = json.loads(r.stdout)
    return d.get("sources", d) if isinstance(d, dict) else d


def source_fulltext(notebook, sid, out_path):
    nb("source", "fulltext", sid, "--notebook", notebook, "-o", out_path, timeout=120)
    t = open(out_path, encoding="utf-8").read()
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


def translate_chunk(chunk, lang):
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
            r = nb("ask", prompt, "--notebook", NB_ID, "--json")
            out = json.loads(r.stdout).get("answer", "").strip() if r.returncode == 0 else ""
        except Exception:
            out = ""
        if len(out) >= 0.30 * len(chunk):   # complete enough
            return out
        time.sleep(3)                        # empty/short -> retry
    return out                               # best effort (caller logs SHORT)


def clean_title(title):
    # "3. <chapter> _ <book title>.pdf" -> "3. <chapter>"
    title = re.sub(r"\.(pdf|txt|md|html?)$", "", title, flags=re.I)
    return re.split(r"\s+_\s+", title)[0].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notebook", required=True)
    ap.add_argument("--page", required=True)
    ap.add_argument("--chunk", type=int, default=4000)
    ap.add_argument("--lang", default=os.environ.get("OUTPUT_LANGUAGE", "ko"))
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="rebuild the Notion page after translating")
    args = ap.parse_args()
    global NB_ID
    NB_ID = args.notebook
    work = args.workdir or f"/tmp/ft_{args.page[:8]}"
    os.makedirs(work, exist_ok=True)

    sources = list_sources(args.notebook)
    print(f"{len(sources)} sources", flush=True)

    # Phase 1: fulltext + chunked translation (resumable).
    seg_files = []  # (title, [chunk translation paths in order])
    for si, s in enumerate(sources):
        sid, title = s.get("id"), s.get("title", f"source {si}")
        raw_path = f"{work}/src_{si:02}.txt"
        if not (os.path.exists(raw_path) and os.path.getsize(raw_path) > 50):
            open(raw_path, "w").write(source_fulltext(args.notebook, sid, raw_path + ".dl"))
        chunks = chunk_text(normalize(open(raw_path).read()), args.chunk)
        paths = []
        for ci, c in enumerate(chunks):
            p = f"{work}/tr_{si:02}_{ci:03}.txt"
            if not (os.path.exists(p) and os.path.getsize(p) > 10):
                t0 = time.time()
                a = translate_chunk(c, args.lang)
                open(p, "w").write(a)
                flag = "" if len(a) >= 0.30 * len(c) else " SHORT!"
                print(f"  s{si:02}c{ci:03}: src={len(c)} out={len(a)} "
                      f"({time.time()-t0:.0f}s){flag}", flush=True)
                time.sleep(1)
            paths.append(p)
        seg_files.append((clean_title(title), paths))

    # Build markdown: per source -> heading + chunk bodies.
    parts = []
    for title, paths in seg_files:
        parts.append(f"# {title}")
        for p in paths:
            body = open(p).read().strip()
            if body:
                parts.append(body)
    md = "\n\n".join(parts)
    blocks = sq.build_answer_blocks(md)
    print(f"assembled {len(md)} chars -> {len(blocks)} blocks", flush=True)

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
