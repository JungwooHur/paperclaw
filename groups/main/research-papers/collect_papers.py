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

# The container path first, then the script's own directory. Hardcoding only the
# container path meant every host-side run of --add-paper died on FileNotFoundError,
# so the one sanctioned way to create a paper page could not be used while
# repairing pages from the host. Other scripts here already resolve paths this way.
_CONTAINER_CONFIG = "/workspace/group/research-papers/config.json"
CONFIG_PATH = (_CONTAINER_CONFIG if os.path.exists(_CONTAINER_CONFIG)
               else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "config.json"))
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
    # where adding a paper whose paper_url had resolved to "" was reported
    # as already-existing and returned an unrelated paper's page.
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


def paper_status(paper_url, title=None) -> tuple:
    """(state, page_id, kind) where state is MISSING / UNPROCESSED / PROCESSED.

    This is the question the dedup step actually needs answered. Asking "does a row
    exist" instead is what let four papers in one day be reported done while their
    pages held nothing but figures. `kind` is `classify_translation`'s answer, None
    when there is no page.
    """
    page = _find_existing_page(paper_url, title=title)
    if not page:
        return "MISSING", None, None
    pid = page["id"].replace("-", "")
    state, kind = status_for(read_blocks(pid))
    return state, pid, kind


MIN_TRANSLATED_CHARS = 500
# What a clear removes: the block types a translation fragment and the healers'
# own output are made of, with no children. Anything else on the page means
# someone put it there, and the clear refuses rather than guess.
CLEARABLE = ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
             "numbered_list_item", "quote", "equation", "divider", "code", "image")
# What a clear keeps in place: the reader's saved questions and their own pages.
KEEP_ON_CLEAR = ("callout", "child_page", "child_database")
CLEARED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cleared")


def _live(blocks: list) -> list:
    return [b for b in blocks if not b.get("archived") and not b.get("in_trash")]


def classify_translation(blocks: list) -> str:
    """EMPTY / PARTIAL / TRANSLATED: does this page carry the paper's translation?

    "A row exists" is NOT "the paper is done". The dispatcher creates the Notion
    page when it INGESTS a request, so by the time the subagent runs its dedup
    check the page is already there — and the rule "results non-empty -> return
    already_existed" then makes the paper permanently un-processable. Figures still
    get injected by the healer (it only needs the Paper URL), so the page ends up
    with images and not one word of text, and every later request skips it again.
    Observed on four papers in one day.

    Nor is a fragment of the text. This used to call a page done at 500
    characters, which an abstract alone clears, while the audit reports a page with
    figures, a section or two and under 4,000 characters as SKIPPED_TRANSLATION and
    says to re-process it. PARTIAL is that rule, read from `verify_sections`, so
    the page the audit sends back is one a request will take. Stricter in one
    respect: a page with no headings is partial only under the same bound, because
    a long body there is a translation whose titles came out as paragraphs.

    Images do not count, nor does the injected English reference list itself: both
    are placed from the source without any translation. What FOLLOWS the list does
    count — a translation appended below a list placed too early is still a
    translation, and reading it as empty is how a clear would destroy it.
    """
    import paragraph_headings
    import reference_section
    import verify_sections as vs
    text = reference_section.without_list(_live(blocks))
    if vs.body_chars(text) < MIN_TRANSLATED_CHARS:
        return "EMPTY"
    sections = vs.group_sections(text)
    partial = vs.is_partial_translation(text, sections) and bool(
        sections
        or paragraph_headings.no_heading_finding(text)[0] == "SKIPPED_TRANSLATION")
    return "PARTIAL" if partial else "TRANSLATED"


def read_blocks(page_id, fetch=None):
    """The page's top-level blocks, or None when they cannot be read."""
    import urllib.error
    import verify_sections as vs
    try:
        return (fetch or vs.fetch_blocks)(page_id)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def status_for(blocks) -> tuple:
    """(state, kind) for a page that exists, judged on what it holds.

    Unreadable blocks answer PROCESSED: unknown beats "empty", since a transient
    error must never send a finished paper back through translation, let alone
    clear its page.
    """
    if blocks is None:
        return "PROCESSED", "TRANSLATED"
    kind = classify_translation(blocks)
    return ("PROCESSED" if kind == "TRANSLATED" else "UNPROCESSED"), kind


def _ours(block: dict, me) -> bool:
    """Did this integration write the block, and has no person edited it since?"""
    made = (block.get("created_by") or {}).get("id")
    edited = (block.get("last_edited_by") or {}).get("id", made)
    return bool(me) and made == me and edited == me


def plan_clear(blocks: list, me) -> dict:
    """`{"ids": [...]}` to archive before an unfinished page is processed again,
    or `{"refused": why}`. `me` is this integration's user id.

    The new translation is appended to the END of the page, so whatever stays
    ends up above it: the old fragment, and figures the figure healer will never
    move, since it leaves a page that already has captioned figures alone. So the
    fragment, the figures and the injected reference list go; the healers place
    figures, tables and the list again once text is on the page.

    Only blocks this integration wrote, and no person has edited since, are ever
    archived. A page in the DB is often a reading-list row the owner annotates —
    a line saying what to look at, a link, a bookmark — and those are theirs,
    whatever type they are; they stay where they are, above the new text. Saved
    questions and child pages stay too.

    Among the integration's own blocks, anything outside the fragment and healer
    types, a block with children (a Q&A nested under a paragraph), anything after
    the reference list, a second list, or a first-principles record anywhere means
    the page holds something a clear cannot judge, and the whole clear is refused:
    processing then appends below what is there, which is untidy and loses nothing.
    """
    import descent
    import reference_section
    if classify_translation(blocks) == "TRANSLATED":
        return {"refused": "the page carries a translation"}
    if not me:
        return {"refused": "cannot tell this integration's blocks from a person's"}
    live = _live(blocks)
    if descent.record_start(live) is not None:
        return {"refused": "the page holds a first-principles record"}
    spans = reference_section.list_spans(live)
    if len(spans) > 1:
        return {"refused": f"the page holds {len(spans)} reference lists"}
    end = spans[0][1] if spans else len(live)
    ids = []
    for k, b in enumerate(live):
        if b["type"] in KEEP_ON_CLEAR or not _ours(b, me):
            continue
        if k >= end:
            return {"refused": f"a {b['type']} block follows the reference list"}
        if b["type"] not in CLEARABLE or b.get("has_children"):
            kind = b["type"] + (" with children" if b.get("has_children") else "")
            return {"refused": f"the page holds a {kind} block"}
        ids.append(b["id"])
    return {"ids": ids}


def was_cleared(page_id) -> bool:
    """Has `clear_unprocessed` ever emptied this page? Its ids file says so."""
    key = page_id.replace("-", "")
    try:
        names = os.listdir(CLEARED_DIR)
    except OSError:
        return False
    return any(name.endswith(".json")
               and name[:-len(".json")].rpartition("-")[0].replace("-", "") == key
               for name in names)


def clear_unprocessed(page_id) -> dict:
    """Archive an unfinished page's blocks so the paper is processed into it afresh.

    Judges the page on what it holds NOW: the page and its blocks are read again
    and `plan_clear` applied again. The ids are written to
    `cleared/<page>-<time>.json` before anything is archived, and an archived block
    comes back with PATCH `archived: false`. The file is also how the healer knows
    to put the reference list back on a page older than its usual one-week window
    (`was_cleared`).
    """
    import http.client
    import page_kind
    import verify_sections as vs
    from translate_fulltext import notion
    try:
        if not page_kind.is_paper(notion("GET", f"/pages/{page_id}")):
            return {"refused": "not a paper page"}
        plan = plan_clear(vs.fetch_blocks(page_id), _me_id())
    except (RuntimeError, OSError, ValueError, http.client.HTTPException) as e:
        return {"refused": f"page unreadable: {e}"[:200]}
    if "refused" in plan:
        return plan
    rep = {"archived": 0}
    if not plan["ids"]:
        return rep
    try:
        os.makedirs(CLEARED_DIR, exist_ok=True)
        rep["ids_file"] = os.path.join(CLEARED_DIR, f"{page_id}-{int(time.time())}.json")
        with open(rep["ids_file"], "w") as f:
            json.dump({"page": page_id, "ids": plan["ids"]}, f)
    except OSError as e:
        return {"refused": f"cannot record the ids first: {e}"[:200]}
    for bid in plan["ids"]:
        try:
            notion("PATCH", f"/blocks/{bid}", {"archived": True})
            rep["archived"] += 1
        except (RuntimeError, OSError, ValueError, http.client.HTTPException) as e:
            rep.setdefault("failed", []).append(f"{bid}: {e}"[:160])
        time.sleep(0.34)
    return rep


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
    eventually-consistent query index (observed: several papers each added
    3-5x within a single minute, because a
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
    authors_list = paper.get('authors') or []
    if isinstance(authors_list, str):
        # A plain string arrives from --add-paper's stdin JSON; an EMPTY one then
        # indexed [0] and crashed page creation with "string index out of range",
        # which the CLI reported as an opaque `ERROR notion-call`.
        authors_list = [a.strip() for a in authors_list.split(',') if a.strip()]
    if authors_list and isinstance(authors_list[0], dict):
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


def _page_text_len(page_id) -> int:
    """Characters of PROSE on a page — images excluded.

    `_block_count` counts blocks, and an image is a block. That makes a page's
    rank depend on how many pictures it carries: a page holding 5,278 re-injected
    figure copies and not one word outranks a fully translated one. Content is
    text; pictures are placed from the Paper URL alone and prove nothing.
    """
    api = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    chars, cursor = 0, None
    try:
        while True:
            u = api + (f"&start_cursor={cursor}" if cursor else "")
            req = urllib.request.Request(u, headers=_notion_headers())
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                d = json.loads(r.read())
            for b in d.get("results", []):
                t = b.get("type")
                if t == "image":
                    continue
                payload = b.get(t) or {}
                if isinstance(payload, dict):
                    chars += sum(len(x.get("plain_text", ""))
                                 for x in payload.get("rich_text", []))
                    if t == "equation":
                        chars += len(payload.get("expression") or "")
            if not d.get("has_more"):
                return chars
            cursor = d.get("next_cursor")
    except Exception:
        return chars


_BOT_ID = []


def _me_id():
    """This integration's own user id, so its edits can be told from a person's."""
    if not _BOT_ID:
        try:
            req = urllib.request.Request("https://api.notion.com/v1/users/me",
                                         headers=_notion_headers())
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                # The integration's own user id is the TOP-LEVEL `id`; `bot.owner`
                # is the human who installed it, which is not what we compare to.
                _BOT_ID.append(json.loads(r.read()).get("id"))
        except Exception:
            _BOT_ID.append(None)
    return _BOT_ID[0]


def _edited_by_human(page) -> bool:
    """True when the LAST edit to this page did not come from this integration.

    The one signal that separates a machine-made duplicate from a page somebody is
    working in. Unknown counts as human: a page we cannot attribute must not be
    destroyed on a guess.
    """
    who = (page.get("last_edited_by") or {}).get("id")
    me = _me_id()
    if not who or not me:
        return True
    return who != me


def _dedup_keys(page):
    """EVERY identity key a page carries: its arxiv id AND its normalized title.

    Both, not "arxiv id else title". The commonest duplicate shape is a stub page
    with no Paper URL beside the real page that has one — under an either/or key
    those two land in DIFFERENT groups (`title:…` vs `arxiv:…`) and the sweep
    never sees them as duplicates, so the pair survives forever. Emitting both
    keys and unioning on any shared one groups them.
    """
    keys = []
    aid = extract_arxiv_id(_page_url(page))
    if aid:
        keys.append(f"arxiv:{aid}")
    t = _normalize_title(_page_title(page))
    if len(t) >= 8:
        keys.append(f"title:{t}")
    return keys


def _group_by_identity(pages):
    """Group pages that share ANY identity key (union-find over the key sets)."""
    parent = list(range(len(pages)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    seen = {}
    keyed = set()
    for i, p in enumerate(pages):
        for k in _dedup_keys(p):
            keyed.add(i)
            if k in seen:
                union(seen[k], i)
            else:
                seen[k] = i
    groups = {}
    for i in sorted(keyed):
        groups.setdefault(find(i), []).append(pages[i])
    # Label each group by a stable key so the log line stays informative.
    out = {}
    for root, grp in groups.items():
        keys = _dedup_keys(pages[root])
        out[keys[0] if keys else f"group:{root}"] = grp
    return out


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
    groups = _group_by_identity(pages)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dup_groups:
        print(f"OK no duplicates among {len(pages)} pages")
        return
    total_archived = 0
    for k, grp in dup_groups.items():
        # Keep the richest page (most blocks), tie-broken by most-recent edit.
        ranked = sorted(
            grp,
            key=lambda p: (_page_text_len(p["id"]), p.get("last_edited_time", "")),
            reverse=True,
        )
        keeper, losers = ranked[0], ranked[1:]
        keeper_text = _page_text_len(keeper["id"])
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
            # Two refusals. Archiving is destructive and this healer exists to clear
            # STUBS left by a double-create — not to adjudicate between two pages
            # that both hold work.
            #
            # A page went to the trash under this rule while its owner was arranging
            # it by hand: the duplicate was ranked on block count, the copy with more
            # blocks won, and a page with a full translation on it disappeared. The
            # owner restored it and the healer then re-placed every figure over their
            # arrangement. Nothing in the log said "a person is working here" —
            # nothing was looking.
            if _edited_by_human(l):
                print(f"KEEP-HUMAN dup={l['id']} keep={keeper['id']} key={k} "
                      f"title={title!r} (last edit was not this integration's)")
                continue
            l_text = _page_text_len(l["id"])
            if l_text > 500 and l_text > keeper_text:
                print(f"KEEP-RICHER dup={l['id']} keep={keeper['id']} key={k} "
                      f"title={title!r} ({l_text} chars vs keeper {keeper_text})")
                continue
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
    parser.add_argument('--status', type=str, default=None, metavar='URL_OR_ID',
                        help='print MISSING/UNPROCESSED/PROCESSED <page_id>; '
                             'exit 0 only when the paper is actually translated')
    parser.add_argument('--clear-unprocessed', action='store_true',
                        help='with --status: when the answer is UNPROCESSED, archive '
                             'the fragment, figures and reference list this integration '
                             'wrote (the owner\'s own blocks and saved Q&A stay) so the '
                             'paper is processed into a clean page')
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
    if args.status:
        st, pid, kind = paper_status(args.status)
        if st == "UNPROCESSED" and args.clear_unprocessed:
            try:
                rep = clear_unprocessed(pid)
            except Exception as e:  # the status line below must still be printed
                rep = {"refused": f"clear failed: {type(e).__name__}: {e}"[:200]}
            print(f"clear ({kind.lower()}): {json.dumps(rep)}", file=sys.stderr)
        elif st == "UNPROCESSED" and kind == "PARTIAL":
            print("the page holds part of a translation; --clear-unprocessed "
                  "empties it before processing", file=sys.stderr)
        print(f"{st} {pid or ''}".strip())
        return 0 if st == "PROCESSED" else 1
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
