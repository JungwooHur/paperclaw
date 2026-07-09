#!/usr/bin/env python3
"""Auto-heal every paper page in $NOTION_RESEARCH_DB: remove translated back-matter
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auto_fix_qa import query_paper_pages, api_post
from strip_backmatter import strip_backmatter
from clean_source_urls import clean_page


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
        d = api_post(f"/databases/{db}/query", body)
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
        except Exception as e:
            print(f"  {pid}: error {type(e).__name__}: {e}", file=sys.stderr)
            continue
        n_bm = bm.get("archived") or bm.get("would_archive") or 0
        n_url = (cu.get("edited") or 0) + (cu.get("archived") or 0)
        if n_bm or n_url:
            healed += 1
            print(f"  {pid}: back-matter blocks={n_bm} url-cleaned blocks={n_url}")
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
