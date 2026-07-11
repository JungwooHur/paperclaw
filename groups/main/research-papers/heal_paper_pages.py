#!/usr/bin/env python3
r"""Auto-heal every paper page in $NOTION_RESEARCH_DB: remove translated back-matter
and leaked source URLs. Runs from paperclaw-qa-heal.service (systemd timer).

Why this exists
---------------
Paper processing is agent-driven and the agent does NOT reliably follow prose
rules in CLAUDE.md — it still translates the References/Acknowledgements section
and still sources off ar5iv (whose inline citation links leak `#bib.bib` URLs into
the body). Fixing this with prose or a one-off manual tool doesn't hold; the
cleanup has to be ENFORCED structurally. So this sweeps the same two tools used
for one-off remediation across the whole DB, on the healer's 5-minute cadence:

  * strip_backmatter — archive a translated References/Bibliography/참고문헌/
    Acknowledgements/Disclosure-of-Funding heading and everything after it.
  * clean_source_urls — strip leaked NotebookLM image URLs and ar5iv citation /
    figure-reference URLs from body text.
  * wrap_math — wrap bare LaTeX (`\mathbf{c}_{v}`, `L_{s}<m_{1}`) left in text
    spans in $...$ so it renders as Notion equations (NotebookLM emits math
    undelimited ~half the time; build_answer_blocks only converts delimited math).
  * strip_furniture — archive leaked arxiv HTML page chrome (nav/TOC/report-issue
    widget/license line/`javascript:` links) that whole-fulltext translation drags in.
  * heal_figures — inject the paper's arxiv figures if the page has none (resolves
    the arxiv id from the page's Paper URL). Figure extraction is otherwise
    agent-driven and routinely skipped, leaving papers with 0 figures.

Both are idempotent and no-ops on an already-clean page. Applies by default;
--dry-run reports without writing.

By default it only heals pages edited in the last --since-hours (so the 5-minute
timer stays cheap and just catches freshly-built papers); --all sweeps the whole
DB (use once to clean the backlog).

  heal_paper_pages.py [--dry-run] [--since-hours N | --all | --page <id>]
"""
import argparse
import datetime
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auto_fix_qa import query_paper_pages, api_post
from strip_backmatter import strip_backmatter
from clean_source_urls import clean_page
from wrap_math import wrap_math_page
from strip_furniture import strip_furniture
from extract_paper_figures import heal_figures


def _post_retry(path, body, tries=5):
    """api_post with retry — this runs unattended on a 5-minute timer, so a
    transient Notion 429/5xx during the DB query must not abort the whole run."""
    last = None
    for a in range(tries):
        try:
            return api_post(path, body)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and a < tries - 1:
                time.sleep(float(e.headers.get("Retry-After", 2)) if e.code == 429 else 2 + 2 * a)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if a < tries - 1:
                time.sleep(2 + 2 * a)
                continue
            raise
    raise last


def query_recent_pages(hours):
    db = os.environ.get("NOTION_RESEARCH_DB")
    if not db:
        sys.exit("NOTION_RESEARCH_DB not set")
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=hours)).replace(microsecond=0).isoformat()
    out, cur = [], None
    while True:
        body = {"page_size": 100,
                "filter": {"timestamp": "last_edited_time",
                           "last_edited_time": {"on_or_after": since}},
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]}
        if cur:
            body["start_cursor"] = cur
        d = _post_retry(f"/databases/{db}/query", body)
        out.extend(p["id"] for p in d["results"])
        if d.get("has_more"):
            cur = d["next_cursor"]
        else:
            return out


def heal(pages, apply):
    healed = 0
    for pid in pages:
        try:
            bm = strip_backmatter(pid, apply=apply)
            cu = clean_page(pid, apply=apply)
            wm = wrap_math_page(pid, apply=apply)
            fu = strip_furniture(pid, apply=apply)
            fg = heal_figures(pid, apply=apply)
        except Exception as e:
            print(f"  {pid}: error {type(e).__name__}: {e}", file=sys.stderr)
            continue
        n_bm = bm.get("archived") or bm.get("would_archive") or 0
        n_url = (cu.get("edited") or 0) + (cu.get("archived") or 0)
        n_math = wm.get("edited") or 0
        n_fur = fu.get("archived") or fu.get("would_archive") or 0
        n_fig = fg.get("placed") or 0
        if n_bm or n_url or n_math or n_fur or n_fig:
            healed += 1
            print(f"  {pid}: back-matter={n_bm} url-cleaned={n_url} "
                  f"math-wrapped={n_math} furniture={n_fur} figures={n_fig}")
    print(f"healed {healed}/{len(pages)} paper page(s)"
          f"{' (dry-run)' if not apply else ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--page", help="heal a single page id")
    ap.add_argument("--all", action="store_true", help="sweep the whole DB (slow)")
    ap.add_argument("--since-hours", type=float, default=3.0,
                    help="only pages edited in the last N hours (default 3)")
    a = ap.parse_args()
    if a.page:
        pages = [a.page]
    elif a.all:
        pages = query_paper_pages()
    else:
        pages = query_recent_pages(a.since_hours)
    heal(pages, apply=not a.dry_run)


if __name__ == "__main__":
    main()
