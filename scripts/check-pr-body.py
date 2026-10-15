#!/usr/bin/env python3
"""Scan a PR title and body for content that must not appear on a public repo.

Why this exists separately from `check-sensitive.sh`: a git hook can only see
commits, and a PR body is written straight to GitHub. Every pattern the hook
enforces is therefore unenforced there — and the leak that actually keeps
happening is not a secret but PAPER CONTENT: section titles, figure-citation
facts, the shape of one specific paper's results.

So this checks three things:
  1. the same text patterns `check-sensitive.sh` blocks (secrets, emails, arxiv
     ids, UUIDs, assistant session URLs), by delegating to it;
  2. the owner's real private values (`private_values.py`), which no pattern
     has to recognise;
  3. verbatim SECTION TITLES from the Notion paper pages — the signal that a PR is
     describing a specific paper rather than the code. Titles are fetched live, so
     the check keeps working as papers are added. Only distinctive ones count:
     three words or more, and not a heading every paper has ("Results and
     Discussion"), matched as whole words.

The Notion credentials come from the environment or, failing that, from the
main checkout's .env, set for this process only: the check-sensitive subprocess
gets an environment without them. A report names the kind of finding and where
it is, never the text it found.

  check-pr-body.py --file body.md [--title T]   # before creating a PR
  check-pr-body.py --pr 51                      # audit one that already exists
  check-pr-body.py --sweep [--limit 60]         # audit every recent PR

`guard-gh-publish.py` runs the same scan on every gh command that would publish,
so a hand-run is for audits. The fetch takes ~20 s, too long to pay on every
gh call, so the guard reads the titles from a private cache
(${XDG_CACHE_HOME:-~/.cache}/paperclaw/section-titles.json, mode 0600) and
refreshes it in the background once it is stale.

PAPERCLAW_ALLOW_SENSITIVE=1 skips the pattern scan of a hand-run. Inherited by
the guard it does nothing: there, only the prefix on the gh command counts.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import private_values  # noqa: E402  (a sibling script, not a package)

MIN_TITLE = 14          # shorter headings ("Method", "Results") are ordinary English
# A title is a usable signal only when it is distinctive. Two-word headings
# ("Model architecture", "Problem Formulation") occur in plain prose about
# code and blocked a code-only PR body, with no way past it.
MIN_TITLE_WORDS = 3
MIN_TITLE_CHARS = 20    # letters and digits only: Korean drops out entirely
GENERIC_TITLES = frozenset({
    "results and discussion", "discussion and results",
    "conclusion and future work", "conclusions and future work",
    "limitations and future work", "discussion and conclusion",
    "discussion and conclusions", "summary and conclusions",
    "background and related work", "related work and background",
    "experimental setup and results", "implementation details",
    "experiments and results", "proof of the main theorem",
})
NOTION_KEYS = ("NOTION_TOKEN", "NOTION_RESEARCH_DB")
TITLE_CACHE_FRESH = 600       # seconds: used as is
TITLE_CACHE_MAX_AGE = 86400   # older than fresh: used while a refresh runs
_REFRESH_LOCK_SECONDS = 180


def _words(text):
    """Lowercase latin words and numbers, single-spaced."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _usable(title):
    words = _words(title)
    return (len(words.split()) >= MIN_TITLE_WORDS
            and len(words.replace(" ", "")) >= MIN_TITLE_CHARS
            and words not in GENERIC_TITLES)


def could_hold_a_title(text):
    """Whether `text` is long enough to contain any usable title."""
    return len(_words(text).replace(" ", "")) >= MIN_TITLE_CHARS


def load_notion_env():
    """Fill missing Notion credentials from the main checkout's .env.

    The service never loads .env into its environment (src/env.ts), so a shell
    usually lacks them, and the paper-content check used to be skipped without
    anyone noticing. They go into this process's environment only.
    """
    missing = [k for k in NOTION_KEYS if not os.environ.get(k)]
    if not missing:
        return
    env = private_values.read_env(private_values.env_file())
    for key in missing:
        if env.get(key):
            os.environ[key] = env[key]


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


def title_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "paperclaw", "section-titles.json")


def _cache_key():
    db = os.environ.get("NOTION_RESEARCH_DB")
    return hashlib.sha256(db.encode()).hexdigest() if db else None


def _read_title_cache(key):
    """(age in seconds, titles) from the cache, or None."""
    try:
        with open(title_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    titles, at = data.get("titles"), data.get("at")
    if not isinstance(titles, list) or not isinstance(at, (int, float)):
        return None
    return time.time() - at, [t for t in titles if isinstance(t, str)]


def write_title_cache(titles):
    """Stores titles for the guard: owner-only, replaced atomically."""
    key = _cache_key()
    if not key or not titles:
        return
    path = title_cache_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"key": key, "at": time.time(), "titles": titles}, f)
    os.replace(tmp, path)


def _refresh_in_background():
    """Starts a detached refresh, unless one started moments ago."""
    lock = title_cache_path() + ".refresh"
    try:
        if time.time() - os.path.getmtime(lock) < _REFRESH_LOCK_SECONDS:
            return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(lock), mode=0o700, exist_ok=True)
        with open(lock, "w", encoding="utf-8"):
            pass
        # No inherited pipe: the hook's caller waits until its pipes close.
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh-titles"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def section_titles(fetch=None, refresh=None):
    """The oracle's titles for the guard, from the cache when it can be.

    Fresh: as cached. Stale but under a day old: as cached, while `refresh`
    (a detached `--refresh-titles` run by default) renews it. Otherwise
    `fetch` (a live `known_section_titles`) answers and is cached.
    """
    key = _cache_key()
    if not key or not os.environ.get("NOTION_TOKEN"):
        return []
    cached = _read_title_cache(key)
    if cached and 0 <= cached[0] < TITLE_CACHE_FRESH:
        return cached[1]
    if cached and 0 <= cached[0] < TITLE_CACHE_MAX_AGE:
        (refresh or _refresh_in_background)()
        return cached[1]
    titles = (fetch or known_section_titles)()
    write_title_cache(titles)
    return titles


def _pattern_env():
    """The environment for check-sensitive: no Notion credentials, redacted,
    and no inherited override: the caller decides that through `patterns`."""
    drop = (*NOTION_KEYS, "PAPERCLAW_ALLOW_SENSITIVE")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PAPERCLAW_REDACT"] = "1"
    return env


def scan(text, titles, needles, label="text", patterns=True):
    """Every reason `text` must not be published.

    Args:
        text: What would be published.
        titles: Section titles from `known_section_titles()`.
        needles: Private values from `private_values.load_needles()`.
        label: Names the text in the pattern scan's report.
        patterns: False skips the pattern scan, the part that can misfire.

    Returns:
        (kind, detail) pairs. A detail says where, never what.
    """
    problems = []
    if patterns:
        proc = subprocess.run(
            ["bash", os.path.join(SCRIPTS, "check-sensitive.sh"),
             "--text", label],
            input=text, capture_output=True, text=True, env=_pattern_env())
        if proc.returncode != 0:
            found = [ln.strip() for ln in proc.stderr.splitlines()
                     if ln.startswith("✗")]
            problems.append(("patterns", "; ".join(found) or
                             f"check-sensitive exited {proc.returncode}"))
    labels = private_values.find(text, needles)
    if labels:
        problems.append(("private value", ", ".join(labels)))
    # Whole words of the latin content: _words drops Korean, so a title like
    # "1단계: 단기 재주석 모델" collapses to "1", which _usable rejects.
    padded = f" {_words(text)} "
    hits = sorted({w for w in map(_words, filter(_usable, titles))
                   if f" {w} " in padded})
    if hits:
        lines = [str(i) for i, ln in enumerate(text.splitlines(), 1)
                 if any(f" {h} " in f" {_words(ln)} " for h in hits)]
        where = f" (line {', '.join(lines[:10])})" if lines else ""
        problems.append(("paper content",
                         f"{len(hits)} verbatim section title(s) from a paper "
                         f"page{where}"))
    return problems


def _gh_json(*args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gh {args[0]} {args[1]} failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--file")
    g.add_argument("--pr")
    g.add_argument("--sweep", action="store_true")
    g.add_argument("--refresh-titles", action="store_true",
                   help="renew the guard's title cache, then exit")
    ap.add_argument("--title", help="a PR title, alone or with --file")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    if a.refresh_titles:
        load_notion_env()
        write_title_cache(known_section_titles())
        return 0
    if not (a.file or a.pr or a.sweep or a.title is not None):
        ap.error("one of --file, --title, --pr or --sweep is required")
    if a.title is not None and (a.pr or a.sweep):
        ap.error("--pr and --sweep read the title from GitHub")

    load_notion_env()
    titles = known_section_titles()
    write_title_cache(titles)
    if not titles:
        print("warning: no section titles available (NOTION_TOKEN unset?) — "
              "paper-content check skipped", file=sys.stderr)
    needles = private_values.load_needles()
    if not needles:
        print("warning: no private values loaded — known-value check skipped",
              file=sys.stderr)

    patterns = os.environ.get("PAPERCLAW_ALLOW_SENSITIVE") != "1"

    def report(label, text):
        problems = scan(text, titles, needles, label, patterns=patterns)
        for kind, detail in problems:
            print(f"✗ {label}: {kind} — {detail}", file=sys.stderr)
        return not problems

    ok = True
    if a.title is not None:
        ok &= report("title", a.title)
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            ok &= report(a.file, f.read())
    elif a.pr:
        pr = _gh_json("pr", "view", a.pr, "--json", "title,body")
        ok &= report(f"PR #{a.pr} title", pr.get("title") or "")
        ok &= report(f"PR #{a.pr} body", pr.get("body") or "")
    elif a.sweep:
        prs = _gh_json("pr", "list", "--state", "all", "--limit", str(a.limit),
                       "--json", "number,title,body")
        for p in prs:
            ok &= report(f"PR #{p['number']} title", p.get("title") or "")
            ok &= report(f"PR #{p['number']} body", p.get("body") or "")
        print(f"swept {len(prs)} PR(s)")
    if ok:
        print("PR text is clean")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
