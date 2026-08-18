#!/usr/bin/env python3
"""Scan a PR body for content that must not appear on a public repo.

Why this exists separately from `check-sensitive.sh`: a git hook can only see
commits, and a PR body is written straight to GitHub. Every pattern the hook
enforces is therefore unenforced there — and the leak that actually keeps
happening is not a secret but PAPER CONTENT: section titles, figure-citation
facts, the shape of one specific paper's results.

So this checks two things:
  1. the same text patterns `check-sensitive.sh` blocks (secrets, emails, arxiv
     ids, UUIDs, assistant session URLs), by delegating to it;
  2. verbatim SECTION TITLES from the Notion paper pages — the signal that a PR is
     describing a specific paper rather than the code. Titles are fetched live, so
     the check keeps working as papers are added.

  check-pr-body.py --file body.md        # before creating a PR
  check-pr-body.py --pr 51               # audit one that already exists
  check-pr-body.py --sweep [--limit 60]  # audit every recent PR
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_TITLE = 14          # shorter headings ("Method", "Results") are ordinary English


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def known_section_titles(page_limit=40):
    """Section titles from recently-edited paper pages, as the leak oracle."""
    tok, db = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_RESEARCH_DB")
    if not (tok and db):
        return []
    sys.path.insert(0, os.path.join(REPO_ROOT, "groups", "main", "research-papers"))
    try:
        import verify_sections as vs
        from save_qa_callout import block_text, HEADING_LEVEL
    except Exception:
        return []
    h = {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28",
         "Content-Type": "application/json"}
    body = json.dumps({"page_size": page_limit,
                       "sorts": [{"timestamp": "last_edited_time",
                                  "direction": "descending"}]}).encode()
    req = urllib.request.Request(f"https://api.notion.com/v1/databases/{db}/query",
                                 data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pages = json.load(r)["results"]
    except Exception:
        return []
    titles = set()
    for p in pages:
        try:
            blocks = vs.fetch_blocks(p["id"].replace("-", ""))
        except Exception:
            continue
        for b in blocks:
            if not HEADING_LEVEL.get(b["type"]):
                continue
            t = vs.english_title(block_text(b)).strip()
            if len(t) >= MIN_TITLE:
                titles.add(t)
    return sorted(titles)


def scan(text, titles):
    problems = []
    proc = subprocess.run(["bash", os.path.join(REPO_ROOT, "scripts", "check-sensitive.sh"),
                           "--msg", "/dev/stdin"], input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        problems.append(("patterns", (proc.stderr or proc.stdout).strip()[:600]))
    nb = _norm(text)
    # Compare on the NORMALISED form's length, not the raw title's: _norm drops
    # Korean, so "1단계: 단기 재주석 모델" collapses to "1" and matched any body
    # containing a digit. Only a title with enough latin content is a usable signal.
    hits = [t for t in titles if len(_norm(t)) >= 12 and _norm(t) in nb]
    if hits:
        problems.append(("paper content",
                         "verbatim section titles from a paper page: "
                         + "; ".join(h[:52] for h in hits[:6])))
    return problems


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--pr")
    g.add_argument("--sweep", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()

    titles = known_section_titles()
    if not titles:
        print("warning: no section titles available (NOTION_TOKEN unset?) — "
              "paper-content check skipped", file=sys.stderr)

    def report(label, text):
        problems = scan(text, titles)
        for kind, detail in problems:
            print(f"✗ {label}: {kind}\n    {detail}", file=sys.stderr)
        return not problems

    if a.file:
        ok = report(a.file, open(a.file).read())
    elif a.pr:
        body = json.loads(subprocess.run(["gh", "pr", "view", a.pr, "--json", "body"],
                                         capture_output=True, text=True).stdout)["body"]
        ok = report(f"PR #{a.pr}", body)
    else:
        prs = json.loads(subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", str(a.limit),
             "--json", "number,body"], capture_output=True, text=True).stdout)
        ok = True
        for p in prs:
            ok &= report(f"PR #{p['number']}", p.get("body") or "")
        print(f"swept {len(prs)} PR(s)")
    if ok:
        print("PR body is clean")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
