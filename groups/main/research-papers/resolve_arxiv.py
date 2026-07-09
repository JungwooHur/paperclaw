#!/usr/bin/env python3
"""Resolve a paper request (URL / arxiv id / title) to the EXACT arxiv id and the
paper's canonical title — so the pipeline NEVER guesses an arxiv id from a title.

Why this exists
---------------
Given only a title, an LLM confabulates a plausible-but-wrong arxiv id. A
single-digit-off id fetches a DIFFERENT paper, which then gets fully translated
and saved under the requested title — a silent wrong-paper bug (see
groups/main/CLAUDE.md). This queries the authoritative
arxiv API instead, and REFUSES to guess: on an ambiguous / low-confidence match it
prints ASK_USER and exits 2, so the agent asks the user rather than proceeding.

  resolve_arxiv.py "<request: an arxiv URL, an arxiv id, or a paper title>"
    -> stdout JSON {"arxiv_id","title","url","source"}   exit 0  (confident)
    -> "ASK_USER ..." + candidate list                   exit 2  (ambiguous)

The workflow MUST use only the arxiv_id/url this returns, and echo `title` back to
the user. Never construct an arxiv id from memory.
"""
import sys
import re
import json
import time
import html
import difflib
import urllib.request
import urllib.parse

ARXIV_ID = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")
API = "http://export.arxiv.org/api/query?"


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "paperclaw-resolve/1.0"})
    last = None
    for attempt in range(3):  # arxiv API 503s / times out under load — retry
        try:
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1.5 ** attempt)
    raise last


def _entries(xml):
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        idm = re.search(r"<id>(.*?)</id>", e)
        tim = re.search(r"<title>(.*?)</title>", e, re.S)
        if not idm or not tim:
            continue
        aid = re.sub(r"v\d+$", "", idm.group(1).split("/abs/")[-1].strip())
        ti = html.unescape(re.sub(r"\s+", " ", tim.group(1)).strip())
        out.append((aid, ti))
    return out


def _by_id(aid):
    e = _entries(_fetch(API + urllib.parse.urlencode({"id_list": aid})))
    return e[0] if e else None


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def _sim(a, b):
    return difflib.SequenceMatcher(None, " ".join(_norm(a)), " ".join(_norm(b))).ratio()


def resolve(request):
    req = request.strip()
    # 1) explicit arxiv id / URL in the request -> authoritative, verify via API.
    m = ARXIV_ID.search(req)
    if m or "arxiv.org" in req.lower():
        if m:
            hit = _by_id(m.group(1))
            if hit:
                return {"arxiv_id": hit[0], "title": hit[1],
                        "url": f"https://arxiv.org/abs/{hit[0]}", "source": "user-id"}
        # An explicit arxiv reference was given but couldn't be verified — ASK the
        # user rather than title-searching the id/URL string (which returns garbage).
        return {"ask_user": True, "query": req, "candidates": [],
                "note": "explicit arxiv id/URL present but not found on arxiv"}
    # 2) title search. Strip trailing instruction words (KO/EN), any URL tail, and
    # double quotes (they would break the ti:"..." query).
    title = re.split(r"https?://", req)[0].strip() or req
    title = re.sub(r"\s*(정리\S*|요약\S*|번역\S*|올려\S*|해줘|해라|부탁\S*|please|summari\w*|translate)\s*$",
                   "", title, flags=re.I).replace('"', " ").strip()
    cands = _entries(_fetch(API + urllib.parse.urlencode(
        {"search_query": f'ti:"{title}"', "max_results": 5})))
    if not cands:  # broaden to an all-fields keyword search
        words = [w for w in _norm(title) if len(w) > 2] or _norm(title)
        kw = " ".join(words)[:200]
        cands = _entries(_fetch(API + urllib.parse.urlencode(
            {"search_query": f"all:{kw}", "max_results": 5})))
    scored = sorted(((_sim(title, ti), aid, ti) for aid, ti in cands), reverse=True)
    # Confident only if top match is strong AND clearly ahead of the runner-up.
    if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        s, aid, ti = scored[0]
        return {"arxiv_id": aid, "title": ti,
                "url": f"https://arxiv.org/abs/{aid}", "source": f"title-match:{s:.2f}"}
    return {"ask_user": True, "query": title,
            "candidates": [{"arxiv_id": aid, "title": ti, "score": round(s, 2)}
                           for s, aid, ti in scored[:5]]}


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        sys.exit('usage: resolve_arxiv.py "<arxiv url | arxiv id | paper title>"')
    try:
        r = resolve(" ".join(sys.argv[1:]))
    except Exception as e:  # network/API failure -> clean message, not a traceback
        sys.exit(f"ERROR: arxiv resolution failed: {e}")
    if r.get("ask_user"):
        print("ASK_USER: could not confidently resolve to a single arxiv paper — "
              "ask the user for the arxiv URL/id instead of guessing.")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(2)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
