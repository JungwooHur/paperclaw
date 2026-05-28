#!/usr/bin/env python3
"""
Process papers: classify and add to Notion
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import time

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB = os.environ.get("NOTION_RESEARCH_DB")

def load_config():
    with open("/workspace/group/research-papers/config.json", 'r') as f:
        return json.load(f)

def classify_paper(paper, config):
    """Intelligently classify paper based on abstract and keywords"""
    abstract = paper.get('abstract', '').lower()
    title = paper.get('title', '').lower()
    text = abstract + ' ' + title

    # Classify areas based on keywords
    areas = []

    # RL keywords
    if any(kw in text for kw in ['reinforcement learning', 'policy gradient', 'ppo', 'sac', 'actor-critic', 'q-learning', 'dqn', 'ddpg']):
        areas.append('RL')

    # World Model keywords
    if any(kw in text for kw in ['world model', 'dreamer', 'latent dynamics', 'model-based rl', 'dynamics model', 'predictive model']):
        areas.append('World Model')

    # Autonomous Navigation keywords
    if any(kw in text for kw in ['autonomous navigation', 'path planning', 'obstacle avoidance', 'motion planning', 'trajectory planning']):
        areas.append('Autonomous Navigation')

    # VLA keywords
    if any(kw in text for kw in ['vision language action', 'vla', 'language model', 'llm', 'foundation model', 'multimodal', 'vision-language']):
        areas.append('VLA')

    # Control keywords
    if any(kw in text for kw in ['control', 'mpc', 'pid', 'controller', 'tracking control', 'feedback control', 'optimal control']):
        areas.append('Control')

    # Computer Vision keywords
    if any(kw in text for kw in ['computer vision', 'object detection', 'semantic segmentation', 'image classification', 'visual perception']):
        areas.append('Computer Vision')

    # SLAM keywords
    if any(kw in text for kw in ['slam', 'simultaneous localization', 'mapping', 'loop closure', 'visual odometry']):
        areas.append('SLAM')

    # State Estimation keywords
    if any(kw in text for kw in ['state estimation', 'kalman filter', 'particle filter', 'localization', 'pose estimation']):
        areas.append('State Estimation')

    # Scene Representation keywords
    if any(kw in text for kw in ['scene representation', 'nerf', 'gaussian splatting', '3d reconstruction', 'scene understanding']):
        areas.append('Scene Representation')

    # Generative Models keywords
    if any(kw in text for kw in ['generative model', 'diffusion', 'vae', 'gan', 'generation', 'synthesize']):
        areas.append('Generative Models')

    # If no areas found, try to infer from topic
    if not areas and 'topic' in paper:
        topic = paper['topic']
        if topic in config.get('notionFieldMap', {}).get('분야', {}):
            areas.append(topic)

    # Default to first topic area if still empty
    if not areas:
        areas.append('RL')

    return areas

def infer_lab(authors, config):
    """Infer lab/institution from authors"""
    lab_map = config.get('researcherLabMap', {})

    for author in authors:
        if author in lab_map:
            return lab_map[author]

    # Default
    return "Unknown"

def extract_venue(paper):
    """Extract conference/journal from paper"""
    venue = paper.get('venue', '')

    # Common abbreviations
    venue_map = {
        'international conference on robotics and automation': 'ICRA',
        'conference on robot learning': 'CoRL',
        'neural information processing systems': 'NeurIPS',
        'international conference on learning representations': 'ICLR',
        'conference on computer vision and pattern recognition': 'CVPR',
        'international conference on computer vision': 'ICCV',
        'robotics: science and systems': 'RSS',
        'ieee transactions on robotics': 'TRO',
        'robotics and automation letters': 'RAL',
    }

    venue_lower = venue.lower()
    for key, abbr in venue_map.items():
        if key in venue_lower:
            return abbr

    # Check if it's arXiv only
    if venue.lower() == 'arxiv' or not venue:
        return None

    return venue

def add_to_notion(paper, areas, lab, venue):
    """Add paper to Notion database"""
    url = "https://api.notion.com/v1/pages"

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # Prepare properties
    properties = {
        "Paper Pages": {
            "title": [{"text": {"content": paper['title']}}]
        },
        "Paper URL": {
            "url": paper.get('paper_url', '')
        },
        "Authors": {
            "rich_text": [{"text": {"content": ', '.join(paper.get('authors', []))}}]
        },
        "Year": {
            "number": paper.get('year', 2026)
        },
        "분야": {
            "multi_select": [{"name": area} for area in areas]
        },
        "연구실, 기관 소속": {
            "multi_select": [{"name": lab}]
        }
    }

    # Add venue if available
    if venue:
        properties["Journal, Conference"] = {
            "select": {"name": venue}
        }

    data = {
        "parent": {"database_id": NOTION_DB},
        "properties": properties
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers=headers,
        method='POST'
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            return result.get('id')
    except Exception as e:
        print(f"  Error adding to Notion: {e}", file=sys.stderr)
        return None

def main():
    config = load_config()

    # Load papers
    with open('papers.json', 'r') as f:
        papers = json.load(f)

    print(f"Processing {len(papers)} papers...")

    results = []

    # Limit for test: process all papers but save metadata
    for i, paper in enumerate(papers, 1):
        print(f"\n[{i}/{len(papers)}] {paper['title'][:80]}...")

        # Classify
        areas = classify_paper(paper, config)
        lab = infer_lab(paper.get('authors', []), config)
        venue = extract_venue(paper)

        print(f"  Areas: {', '.join(areas)}")
        print(f"  Lab: {lab}")
        print(f"  Venue: {venue or 'arXiv only'}")

        # Add to Notion
        page_id = add_to_notion(paper, areas, lab, venue)

        if page_id:
            print(f"  ✓ Added to Notion: {page_id}")
            results.append({
                'title': paper['title'],
                'areas': areas,
                'lab': lab,
                'venue': venue,
                'page_id': page_id,
                'paper_url': paper.get('paper_url', ''),
                'arxiv_id': paper.get('externalIds', {}).get('ArXiv', '')
            })
        else:
            print(f"  ✗ Failed to add")

        # Rate limit
        time.sleep(0.5)

    # Save results
    with open('notion_results.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n\nCompleted: {len(results)}/{len(papers)} papers added to Notion")
    print(f"Results saved to notion_results.json")

if __name__ == '__main__':
    main()
