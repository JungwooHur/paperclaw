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


def check_notion_exists(paper_url, title=None):
    """Check if paper already exists in Notion database.

    Runs TWO independent checks and returns True if either hits:
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

    # (a) URL match
    arxiv_id = extract_arxiv_id(paper_url)
    url_filter = (
        {"property": "Paper URL", "url": {"contains": arxiv_id}} if arxiv_id
        else {"property": "Paper URL", "url": {"equals": paper_url}}
    )
    req = urllib.request.Request(api_url, data=json.dumps({"filter": url_filter}).encode(), headers=headers)
    with urllib.request.urlopen(req) as response:
        if len(json.loads(response.read()).get('results', [])) > 0:
            return True

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
                                return True
    return False

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
        paper_url = paper.get("paper_url", "")

    title = paper.get("title", "")
    key = _session_key(paper_url, title)
    if key and key in _ADDED_THIS_SESSION:
        return {"skipped": True, "reason": "session-cache", "key": key}
    if check_notion_exists(paper_url, title=title):
        if key: _ADDED_THIS_SESSION.add(key)
        return {"skipped": True, "reason": "already-in-notion", "key": key}

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
    args = parser.parse_args()

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
            print(f"SKIPPED {result.get('reason','duplicate')}")
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
