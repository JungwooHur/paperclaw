#!/usr/bin/env python3
"""
Daily research paper collection script.
Searches Semantic Scholar for new papers by followed researchers.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from difflib import SequenceMatcher

CONFIG_PATH = "/workspace/group/research-papers/config.json"
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB = os.environ.get("NOTION_RESEARCH_DB")

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def search_s2_author(name):
    """Search Semantic Scholar for author by name."""
    query = urllib.parse.quote(name)
    url = f"https://api.semanticscholar.org/graph/v1/author/search?query={query}&fields=authorId,name,affiliations&limit=5"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())

    if not data.get('data'):
        return None

    # Find best match by name similarity
    best_match = None
    best_score = 0

    for author in data['data']:
        score = similarity(name, author.get('name', ''))
        if score > best_score:
            best_score = score
            best_match = author

    # Require at least 0.7 similarity
    if best_score >= 0.7:
        return best_match['authorId']

    return None

def get_s2_author_papers(author_id, limit=50):
    """Get papers for a Semantic Scholar author."""
    url = f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers?fields=title,year,externalIds,abstract,authors,venue,publicationDate,publicationVenue,citationCount&limit={limit}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())

    return data.get('data', [])

def search_arxiv(query, days_back=7):
    """Search arXiv for papers matching query within last N days."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://export.arxiv.org/api/query?search_query={encoded_query}&sortBy=submittedDate&sortOrder=descending&start=0&max_results=20"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}

    papers = []
    # Use timezone-aware datetime for comparison
    from datetime import timezone
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)

    for entry in root.findall('atom:entry', ns):
        published = entry.find('atom:published', ns).text
        pub_date = datetime.fromisoformat(published.replace('Z', '+00:00'))

        if pub_date < cutoff_date:
            continue

        # Extract arXiv ID from the id field
        arxiv_id = entry.find('atom:id', ns).text.split('/abs/')[-1]

        title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
        abstract = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')

        authors = []
        for author in entry.findall('atom:author', ns):
            name = author.find('atom:name', ns).text
            authors.append(name)

        papers.append({
            'title': title,
            'abstract': abstract,
            'authors': authors,
            'year': pub_date.year,
            'publicationDate': pub_date.isoformat(),
            'externalIds': {'ArXiv': arxiv_id},
            'venue': 'arXiv',
            'source': 'arxiv'
        })

    return papers

def extract_arxiv_id(url):
    """Extract bare arxiv ID (e.g. '2401.12345') from any arxiv/ar5iv URL."""
    import re
    if not url:
        return None
    # Match patterns like abs/2401.12345, abs/2401.12345v2, html/2401.12345v1
    m = re.search(r'(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?', url)
    if m:
        return m.group(1)
    # Fallback: bare ID at end of string
    m = re.search(r'(\d{4}\.\d{4,5})(?:v\d+)?$', url)
    return m.group(1) if m else None


def _normalize_title(title: str) -> str:
    import re
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _find_existing_page(paper_url, title=None):
    """Return the existing Notion page dict for this paper, or None.

    Runs TWO independent checks and returns the first hit:
      (a) URL substring match on arxiv_id (tolerates abs/pdf/ar5iv/v1/v2),
          or exact URL match for non-arxiv papers.
      (b) Title contains-match (case-insensitive) — catches legacy
          entries with empty URL and minor title variations.

    Either check alone was insufficient in production:
      - URL-only missed the 2026-02-27 seed entries that had no URL set.
      - Title-only misses when the title was slightly rewritten.
    """
    api_url = f"https://api.notion.com/v1/databases/{NOTION_DB}/query"
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
    }

    # (a) URL match — ONLY when we actually have a URL/arxiv id to match on.
    # A blank value here is catastrophic: Notion's `url equals ""` matches
    # EVERY page (returns a full 100-row page), so `results[0]` would be an
    # arbitrary false "duplicate". That is exactly the 2026-06-02 incident
    # where a SocialNav add (whose paper_url had resolved to "") was reported
    # as already-existing and returned an unrelated LongTraceRL page.
    arxiv_id = extract_arxiv_id(paper_url)
    if arxiv_id or paper_url:
        url_filter = (
            {"property": "Paper URL", "url": {"contains": arxiv_id}} if arxiv_id
            else {"property": "Paper URL", "url": {"equals": paper_url}}
        )
        req = urllib.request.Request(api_url, data=json.dumps({"filter": url_filter}).encode(), headers=headers)
        with urllib.request.urlopen(req) as response:
            results = json.loads(response.read()).get('results', [])
            if results:
                return results[0]

    # (b) Title match — use a distinctive prefix/substring to stay within
    # Notion's `contains` semantics (case-insensitive, whitespace-tolerant).
    if title:
        stem = _normalize_title(title)
        # Use the longest leading chunk up to 60 chars; if the title has a
        # colon (common for "Title: Subtitle"), prefer the pre-colon part.
        probe = stem.split(":", 1)[0].strip() if ":" in stem else stem
        probe = probe[:60]
        if len(probe) >= 8:
            title_filter = {"property": "Paper Pages", "title": {"contains": probe}}
            req = urllib.request.Request(api_url, data=json.dumps({"filter": title_filter}).encode(), headers=headers)
            with urllib.request.urlopen(req) as response:
                for p in json.loads(response.read()).get('results', []):
                    # Confirm via normalized title match (avoid partial false hits)
                    for v in p["properties"].values():
                        if v.get("type") == "title":
                            existing = _normalize_title(
                                "".join(r["plain_text"] for r in v["title"])
                            )
                            if existing == stem or stem in existing or existing in stem:
                                return p
    return None


def check_notion_exists(paper_url, title=None):
    """Bool wrapper around _find_existing_page (back-compat for callers
    that only need existence, e.g. the nightly fetch dedup)."""
    return _find_existing_page(paper_url, title=title) is not None

def classify_paper(title, abstract):
    """Classify paper into research areas."""
    text = (title + ' ' + abstract).lower()
    areas = []

    # RL keywords
    if any(kw in text for kw in ['reinforcement learning', 'policy gradient', 'ppo', 'sac', 'reward', 'sim-to-real', 'policy optimization', 'actor-critic', 'q-learning', 'dqn']):
        areas.append('RL')

    # World Model
    if any(kw in text for kw in ['world model', 'dreamer', 'latent dynamics', 'rssm', 'model-based rl', 'model-based reinforcement']):
        areas.append('World Model')

    # Autonomous Navigation
    if any(kw in text for kw in ['navigation', 'path planning', 'obstacle avoidance', 'waypoint', 'exploration', 'traversability']):
        areas.append('Autonomous Navigation')

    # VLA
    if any(kw in text for kw in ['vision-language-action', 'vla', 'vision language action', 'llm robot', 'foundation model', 'multimodal robot', 'language conditioned', 'language-conditioned']):
        areas.append('VLA')

    # Control
    if any(kw in text for kw in ['mpc', 'model predictive control', 'trajectory optimization', 'whole-body control', 'contact', 'dynamics', 'optimal control']):
        areas.append('Control')

    # Computer Vision
    if any(kw in text for kw in ['perception', 'detection', 'segmentation', 'depth estimation', 'visual', 'image processing', 'object recognition']):
        areas.append('Computer Vision')

    # SLAM
    if any(kw in text for kw in ['slam', 'mapping', 'localization', 'loop closure', 'simultaneous localization']):
        areas.append('SLAM')

    # State Estimation
    if any(kw in text for kw in ['state estimation', 'odometry', 'imu', 'pose estimation', 'kalman filter', 'sensor fusion']):
        areas.append('State Estimation')

    # Scene Representation
    if any(kw in text for kw in ['nerf', '3dgs', 'gaussian splatting', 'occupancy', 'implicit representation', 'neural radiance']):
        areas.append('Scene Representation')

    # Generative Models
    if any(kw in text for kw in ['diffusion', 'vae', 'variational autoencoder', 'gan', 'generative']):
        areas.append('Generative Models')

    return areas if areas else ['RL']  # Default to RL if nothing matches

def infer_lab(authors, researcher_lab_map):
    """Infer lab/institution from authors."""
    labs = set()

    for author in authors:
        author_name = author if isinstance(author, str) else author.get('name', '')
        if author_name in researcher_lab_map:
            labs.add(researcher_lab_map[author_name])

    return list(labs)

def infer_venue(venue_str):
    """Infer journal/conference from venue string."""
    if not venue_str:
        return None

    venue_lower = venue_str.lower()

    if 'tro' in venue_lower or 'transactions on robotics' in venue_lower:
        return 'TRO'
    elif 'ral' in venue_lower or 'robotics and automation letters' in venue_lower:
        return 'RAL'
    elif 'ijrr' in venue_lower or 'international journal of robotics research' in venue_lower:
        return 'IJRR'
    elif 'science robotics' in venue_lower:
        return 'Science Robotics'

    return None

# Session cache — survives within-invocation dupes that slip past Notion's
# eventually-consistent query index (new page can take ~10-30s to appear).
_ADDED_THIS_SESSION: set[str] = set()


def _session_key(paper_url: str, title: str) -> str:
    aid = extract_arxiv_id(paper_url) or ""
    return aid or _normalize_title(title)[:80] or paper_url


def add_to_notion(paper, areas, labs, venue_field):
    """Add paper to Notion database — idempotent.

    Runs check_notion_exists() first; if duplicate, returns the existing
    page dict. Also keeps a session cache to survive Notion's
    eventually-consistent query index (observed: Pessimistic Bootstrapping
    + AMP + OccWorld all added 3-5x within a single minute, because a
    query right after POST didn't yet see the new page).
    """
    url = "https://api.notion.com/v1/pages"

    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
    }

    # Get paper URL
    if paper.get('externalIds', {}).get('ArXiv'):
        paper_url = f"https://arxiv.org/abs/{paper['externalIds']['ArXiv']}"
    elif paper.get('externalIds', {}).get('DOI'):
        paper_url = f"https://doi.org/{paper['externalIds']['DOI']}"
    else:
        # Accept the keys agents actually hand us via --add-paper: `paper_url`,
        # `url`, or a bare `arxiv_id`. Missing all three previously left
        # paper_url="" and silently broke dedup (see _find_existing_page note).
        paper_url = paper.get("paper_url") or paper.get("url") or ""
        if not paper_url and paper.get("arxiv_id"):
            paper_url = f"https://arxiv.org/abs/{paper['arxiv_id']}"

    title = paper.get("title", "")
    key = _session_key(paper_url, title)
    if key and key in _ADDED_THIS_SESSION:
        return {"skipped": True, "reason": "session-cache", "key": key}
    existing = _find_existing_page(paper_url, title=title)
    if existing is not None:
        if key: _ADDED_THIS_SESSION.add(key)
        # Return the existing page id so callers can PATCH into it directly
        # instead of re-querying Notion (whose index lags ~10-30s after a
        # write and has caused duplicate raw-curl re-creates).
        return {"skipped": True, "reason": "already-in-notion", "key": key,
                "id": existing.get("id")}

    # Format authors
    authors_list = paper.get('authors', [])
    if isinstance(authors_list[0], dict):
        authors_str = ', '.join([a.get('name', '') for a in authors_list[:5]])
    else:
        authors_str = ', '.join(authors_list[:5])

    if len(authors_list) > 5:
        authors_str += ', et al.'

    # Build properties
    properties = {
        "Paper Pages": {"title": [{"text": {"content": paper['title']}}]},
        "Paper URL": {"url": paper_url},
        "Authors": {"rich_text": [{"text": {"content": authors_str}}]},
        "Year": {"number": paper.get('year', datetime.now().year)},
        "분야": {"multi_select": [{"name": area} for area in areas]}
    }

    if labs:
        properties["연구실, 기관 소속"] = {"multi_select": [{"name": lab} for lab in labs]}

    if venue_field:
        properties["Journal, Conference"] = {"select": {"name": venue_field}}

    body = json.dumps({
        "parent": {"database_id": NOTION_DB},
        "properties": properties
    })

    req = urllib.request.Request(url, data=body.encode(), headers=headers)
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read())
    if key: _ADDED_THIS_SESSION.add(key)
    return result


# ---------------------------------------------------------------------------
# Duplicate healer (out-of-band, runs from the qa-heal systemd timer).
#
# The in-script dedup in add_to_notion only protects the sanctioned
# --add-paper path. Agents have repeatedly fallen back to raw
# `curl POST /v1/pages` when Notion's eventually-consistent query index hid a
# page they had just created — and raw POST bypasses every in-script guard, so
# no amount of prose ("never raw POST") reliably prevents the duplicate. This
# sweep cleans up regardless of how a duplicate was created: it groups every
# page by arxiv_id (or normalized title), keeps the richest page (most child
# blocks), backfills a missing URL onto the keeper, and archives the rest.
# Archived pages go to Notion trash (30-day recovery), so this is reversible.
# ---------------------------------------------------------------------------
_HTTP_TIMEOUT = 30  # Notion occasionally stalls mid-request; never block forever.


def _notion_headers():
    return {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json',
    }


def _query_all_pages():
    """Return every page in the DB. The explicit `sorts` is REQUIRED: without
    it Notion caps larger DBs at ~300 rows and still reports has_more=false."""
    api_url = f"https://api.notion.com/v1/databases/{NOTION_DB}/query"
    pages, cursor = [], None
    while True:
        body = {
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(api_url, data=json.dumps(body).encode(), headers=_notion_headers())
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            d = json.loads(r.read())
        pages.extend(d.get("results", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return pages


def _page_title(page):
    for v in page.get("properties", {}).values():
        if v.get("type") == "title":
            return "".join(r.get("plain_text", "") for r in v.get("title", []))
    return ""


def _page_url(page):
    for v in page.get("properties", {}).values():
        if v.get("type") == "url":
            return v.get("url") or ""
    return ""


def _block_count(page_id):
    """Number of top-level child blocks — a proxy for how much content a page
    holds, used to pick which duplicate to keep."""
    api = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    total, cursor = 0, None
    while True:
        u = api + (f"&start_cursor={cursor}" if cursor else "")
        req = urllib.request.Request(u, headers=_notion_headers())
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            d = json.loads(r.read())
        total += len(d.get("results", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return total


def _dedup_key(page):
    """Group key: arxiv_id when available (most reliable), else normalized
    title. Returns None for pages with neither (left untouched)."""
    aid = extract_arxiv_id(_page_url(page))
    if aid:
        return f"arxiv:{aid}"
    t = _normalize_title(_page_title(page))
    return f"title:{t}" if len(t) >= 8 else None


def _patch_page(page_id, payload):
    api = f"https://api.notion.com/v1/pages/{page_id}"
    req = urllib.request.Request(api, data=json.dumps(payload).encode(),
                                 headers=_notion_headers(), method="PATCH")
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def dedupe_notion(dry_run=False):
    """Find duplicate paper pages and archive all but the richest per group."""
    if not NOTION_TOKEN or not NOTION_DB:
        print("ERROR missing NOTION_TOKEN/NOTION_RESEARCH_DB", file=sys.stderr)
        sys.exit(2)
    pages = _query_all_pages()
    groups = {}
    for p in pages:
        k = _dedup_key(p)
        if k:
            groups.setdefault(k, []).append(p)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dup_groups:
        print(f"OK no duplicates among {len(pages)} pages")
        return
    total_archived = 0
    for k, grp in dup_groups.items():
        # Keep the richest page (most blocks), tie-broken by most-recent edit.
        ranked = sorted(
            grp,
            key=lambda p: (_block_count(p["id"]), p.get("last_edited_time", "")),
            reverse=True,
        )
        keeper, losers = ranked[0], ranked[1:]
        title = _page_title(keeper)[:60]
        # Backfill a missing URL onto the keeper from a loser that has one.
        if not _page_url(keeper):
            for l in losers:
                lu = _page_url(l)
                if lu:
                    print(f"BACKFILL-URL keep={keeper['id']} url={lu}")
                    if not dry_run:
                        _patch_page(keeper["id"], {"properties": {"Paper URL": {"url": lu}}})
                    break
        for l in losers:
            print(f"{'WOULD-ARCHIVE' if dry_run else 'ARCHIVE'} dup={l['id']} "
                  f"keep={keeper['id']} key={k} title={title!r}")
            if not dry_run:
                _patch_page(l["id"], {"archived": True})
            total_archived += 1
    print(f"{'DRY-RUN ' if dry_run else ''}done: {len(dup_groups)} duplicate "
          f"group(s), {total_archived} page(s) {'would be ' if dry_run else ''}archived")


def get_paper_url(paper):
    """Get canonical URL for a paper."""
    arxiv_id = paper.get('externalIds', {}).get('ArXiv')
    doi = paper.get('externalIds', {}).get('DOI')
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    elif doi:
        return f"https://doi.org/{doi}"
    return None


def fetch_papers(config, max_researchers=None, researcher_names=None):
    """Fetch papers from Semantic Scholar by followed researchers, deduplicate, check Notion.
    Returns list of new papers."""
    all_papers = []
    if researcher_names:
        researchers = researcher_names
    else:
        researchers = config['researchers']
        if max_researchers:
            researchers = researchers[:max_researchers]
    s2_author_ids = config.get('s2AuthorIds', {})

    # Researcher-based search (Semantic Scholar only)
    print(f"\nSearching papers by {len(researchers)} researchers...", file=sys.stderr)

    batch_size = 5
    for i in range(0, len(researchers), batch_size):
        batch = researchers[i:i+batch_size]
        print(f"Processing batch {i//batch_size + 1} ({i+1}-{min(i+batch_size, len(researchers))})...", file=sys.stderr)

        for researcher in batch:
            try:
                author_id = s2_author_ids.get(researcher)

                if not author_id:
                    print(f"  Searching author ID for {researcher}...", file=sys.stderr)
                    author_id = search_s2_author(researcher)
                    if author_id:
                        s2_author_ids[researcher] = author_id
                        print(f"    Found: {author_id}", file=sys.stderr)
                    else:
                        print(f"    Not found", file=sys.stderr)
                        continue

                papers = get_s2_author_papers(author_id)

                from datetime import timezone
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
                recent_papers = []

                for paper in papers:
                    pub_date_str = paper.get('publicationDate')
                    if pub_date_str:
                        try:
                            pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                            if pub_date.tzinfo is None:
                                pub_date = pub_date.replace(tzinfo=timezone.utc)
                            if pub_date >= cutoff_date:
                                paper['source'] = 'semantic_scholar'
                                paper['researcher'] = researcher
                                recent_papers.append(paper)
                        except:
                            pass

                if recent_papers:
                    print(f"  {researcher}: {len(recent_papers)} recent papers", file=sys.stderr)
                    all_papers.extend(recent_papers)

            except Exception as e:
                print(f"  Error processing {researcher}: {e}", file=sys.stderr)

        if i + batch_size < len(researchers):
            time.sleep(3)

    # Deduplicate
    print(f"\nDeduplicating {len(all_papers)} papers...", file=sys.stderr)

    seen_ids = set()
    unique_papers = []

    for paper in all_papers:
        arxiv_id = paper.get('externalIds', {}).get('ArXiv')
        doi = paper.get('externalIds', {}).get('DOI')
        paper_id = arxiv_id or doi or paper.get('title')

        if paper_id not in seen_ids:
            seen_ids.add(paper_id)
            unique_papers.append(paper)

    print(f"After deduplication: {len(unique_papers)} unique papers", file=sys.stderr)

    # Check Notion for existing papers
    print("\nChecking Notion for existing papers...", file=sys.stderr)

    new_papers = []
    for paper in unique_papers:
        try:
            paper_url = get_paper_url(paper)
            if not paper_url:
                continue

            if check_notion_exists(paper_url, title=paper.get("title")):
                continue

            paper['paper_url'] = paper_url
            new_papers.append(paper)
            time.sleep(0.3)  # Rate limiting for Notion API

        except Exception as e:
            print(f"  Error checking paper: {e}", file=sys.stderr)

    print(f"New papers not in Notion: {len(new_papers)}", file=sys.stderr)

    # Update config with new author IDs
    config['s2AuthorIds'] = s2_author_ids
    config['lastRun'] = datetime.now().isoformat()
    save_config(config)

    return new_papers


def backfill_papers(config, max_researchers=None, researcher_names=None, limit=10):
    """Fetch highly-cited papers (last 10 years) from followed researchers, not yet in Notion.
    Returns papers sorted by citation count descending."""
    all_papers = []
    if researcher_names:
        researchers = researcher_names
    else:
        researchers = config['researchers']
        if max_researchers:
            researchers = researchers[:max_researchers]
    s2_author_ids = config.get('s2AuthorIds', {})

    from datetime import timezone
    cutoff_year = datetime.now().year - 10

    print(f"\n[Backfill] Searching papers by {len(researchers)} researchers (last 10 years, by citations)...", file=sys.stderr)

    batch_size = 5
    for i in range(0, len(researchers), batch_size):
        batch = researchers[i:i+batch_size]
        print(f"Processing batch {i//batch_size + 1} ({i+1}-{min(i+batch_size, len(researchers))})...", file=sys.stderr)

        for researcher in batch:
            try:
                author_id = s2_author_ids.get(researcher)
                if not author_id:
                    print(f"  Searching author ID for {researcher}...", file=sys.stderr)
                    author_id = search_s2_author(researcher)
                    if author_id:
                        s2_author_ids[researcher] = author_id
                    else:
                        continue

                papers = get_s2_author_papers(author_id, limit=200)

                for paper in papers:
                    year = paper.get('year')
                    if year and year >= cutoff_year:
                        paper['source'] = 'backfill'
                        paper['researcher'] = researcher
                        all_papers.append(paper)

                print(f"  {researcher}: {len([p for p in papers if p.get('year', 0) >= cutoff_year])} papers (10yr)", file=sys.stderr)

            except Exception as e:
                print(f"  Error processing {researcher}: {e}", file=sys.stderr)

        if i + batch_size < len(researchers):
            time.sleep(3)

    # Sort by citation count (highest first)
    all_papers.sort(key=lambda p: p.get('citationCount', 0) or 0, reverse=True)

    # Deduplicate
    seen_ids = set()
    unique_papers = []
    for paper in all_papers:
        arxiv_id = paper.get('externalIds', {}).get('ArXiv')
        doi = paper.get('externalIds', {}).get('DOI')
        paper_id = arxiv_id or doi or paper.get('title')
        if paper_id not in seen_ids:
            seen_ids.add(paper_id)
            unique_papers.append(paper)

    print(f"[Backfill] {len(unique_papers)} unique papers, checking Notion...", file=sys.stderr)

    # Check Notion, return top N not in DB
    new_papers = []
    for paper in unique_papers:
        if len(new_papers) >= limit:
            break
        try:
            paper_url = get_paper_url(paper)
            if not paper_url:
                continue
            if check_notion_exists(paper_url, title=paper.get("title")):
                continue
            paper['paper_url'] = paper_url
            new_papers.append(paper)
            time.sleep(0.3)
        except Exception as e:
            print(f"  Error checking paper: {e}", file=sys.stderr)

    print(f"[Backfill] Found {len(new_papers)} highly-cited papers not in Notion", file=sys.stderr)

    # Update config
    config['s2AuthorIds'] = s2_author_ids
    save_config(config)

    return new_papers


def main():
    parser = argparse.ArgumentParser(description='Research paper collection')
    parser.add_argument('--fetch-only', action='store_true',
                        help='Fetch and deduplicate only, output JSON to stdout (no classification, no Notion writes)')
    parser.add_argument('--max-researchers', type=int, default=None,
                        help='Limit number of researchers to search (for testing)')
    parser.add_argument('--researchers', type=str, default=None,
                        help='Comma-separated list of researcher names to search (overrides config list)')
    parser.add_argument('--backfill', action='store_true',
                        help='Backfill mode: fetch highly-cited papers from last 10 years not yet in Notion')
    parser.add_argument('--backfill-limit', type=int, default=10,
                        help='Max papers to return in backfill mode (default: 10)')
    parser.add_argument('--add-paper', action='store_true',
                        help='Add a single paper with built-in duplicate check. '
                             'Reads one paper JSON object from stdin (the same '
                             'shape --fetch-only emits) and inserts into Notion. '
                             'Prints one of: ADDED <id>, SKIPPED <reason>, ERROR <msg>. '
                             'Classification/lab/venue can be overridden via flags below.')
    parser.add_argument('--areas', type=str, default=None,
                        help='Override areas (comma-separated) for --add-paper')
    parser.add_argument('--labs', type=str, default=None,
                        help='Override labs (comma-separated) for --add-paper')
    parser.add_argument('--venue', type=str, default=None,
                        help='Override venue for --add-paper (e.g. TRO, CoRL)')
    parser.add_argument('--dedupe', action='store_true',
                        help='Archive duplicate paper pages (same arxiv_id or normalized '
                             'title), keeping the richest in each group. Backfills a missing '
                             'URL onto the keeper. Needs only NOTION_TOKEN/NOTION_RESEARCH_DB; '
                             'does not read config.json, so it runs on the host.')
    parser.add_argument('--dry-run', action='store_true',
                        help='With --dedupe: report what would change without mutating Notion.')
    args = parser.parse_args()

    # --dedupe runs out-of-band (host systemd timer) and must not require the
    # container-only config.json, so handle it before load_config().
    if args.dedupe:
        dedupe_notion(dry_run=args.dry_run)
        return

    print("Loading config...", file=sys.stderr)
    config = load_config()

    researcher_names = None
    if args.researchers:
        researcher_names = [r.strip() for r in args.researchers.split(',')]

    if args.add_paper:
        try:
            paper = json.loads(sys.stdin.read())
        except Exception as e:
            print(f"ERROR bad-json {e}")
            sys.exit(2)
        if not paper.get("title"):
            print("ERROR missing-title"); sys.exit(2)

        # Resolve classification/lab/venue
        areas = ([a.strip() for a in args.areas.split(",") if a.strip()]
                 if args.areas else classify_paper(paper["title"], paper.get("abstract", "")))
        if args.labs:
            labs = [l.strip() for l in args.labs.split(",") if l.strip()]
        else:
            researcher_lab_map = config.get("researcherLabMap", {})
            labs = infer_lab(paper.get("authors", []), researcher_lab_map)
        venue_field = args.venue or infer_venue(paper.get("venue", ""))

        try:
            result = add_to_notion(paper, areas, labs, venue_field)
        except Exception as e:
            print(f"ERROR notion-call {e}")
            sys.exit(1)
        if isinstance(result, dict) and result.get("skipped"):
            rid = result.get("id")
            # Emit the existing page id when known so the caller can PATCH into
            # it directly instead of re-querying Notion's lagging index.
            print(f"SKIPPED {result.get('reason','duplicate')}" + (f" {rid}" if rid else ""))
            return
        pid = result.get("id") if isinstance(result, dict) else ""
        print(f"ADDED {pid}")
        return

    if args.backfill:
        papers = backfill_papers(config, max_researchers=args.max_researchers,
                                 researcher_names=researcher_names, limit=args.backfill_limit)
        print(json.dumps(papers, indent=2, ensure_ascii=False))
        return

    if args.fetch_only:
        new_papers = fetch_papers(config, max_researchers=args.max_researchers, researcher_names=researcher_names)
        # Output clean JSON to stdout for Claude to consume
        print(json.dumps(new_papers, indent=2, ensure_ascii=False))
        return

    # Legacy full mode (kept for backwards compatibility)
    researcher_lab_map = config['researcherLabMap']
    new_papers = fetch_papers(config, max_researchers=args.max_researchers, researcher_names=researcher_names)

    added_papers = []
    for paper in new_papers:
        try:
            areas = classify_paper(paper['title'], paper.get('abstract', ''))
            labs = infer_lab(paper.get('authors', []), researcher_lab_map)
            venue_field = infer_venue(paper.get('venue', ''))

            add_to_notion(paper, areas, labs, venue_field)

            added_papers.append({
                'title': paper['title'],
                'areas': areas,
                'labs': labs
            })

            print(f"  ✓ Added: {paper['title'][:60]}...", file=sys.stderr)
            time.sleep(0.5)

        except Exception as e:
            print(f"  ✗ Error adding paper: {e}", file=sys.stderr)

    print(f"\nSUMMARY: Added {len(added_papers)} new papers", file=sys.stderr)

    by_area = {}
    for paper in added_papers:
        for area in paper['areas']:
            if area not in by_area:
                by_area[area] = []
            by_area[area].append(paper['title'])

    result = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'total': len(added_papers),
        'by_area': by_area,
        'papers': added_papers
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
