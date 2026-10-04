#!/usr/bin/env python3
"""Whether a page in the research DB is a paper at all.

The owner keeps other pages in the same DB — lectures they made, a video at the
top and its narration script beneath — filed under a venue of their own. The
paper repairs assume a translated paper: a body to strip arXiv links from,
titles to promote, an arXiv id to resolve from the title and figures to inject.
On a lecture page each of those is damage, and the first one already happened:
the reference list lost its links.

Imports nothing, so every caller can afford it.
"""

# Venues that mark a page as NOT a paper. The owner's own label, set in Notion.
NON_PAPER_VENUES = frozenset({"Learn"})


def venue(page: dict) -> str:
    """The page's venue (the select property), or an empty string."""
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "select" and prop.get("select"):
            name = (prop["select"] or {}).get("name") or ""
            if name:
                return name
    return ""


def is_paper(page: dict) -> bool:
    """False only for a page the owner filed under a non-paper venue.

    A page with no venue yet is a paper: most are added before anyone fills
    the venue in, and skipping them would leave new papers unrepaired.
    """
    return venue(page) not in NON_PAPER_VENUES


def main() -> int:
    """`page_kind.py --page <id>` prints `paper` or `lecture`."""
    import argparse

    from translate_fulltext import notion

    ap = argparse.ArgumentParser(description="Is this DB page a paper?")
    ap.add_argument("--page", required=True)
    page = notion("GET", f"/pages/{ap.parse_args().page}")
    print("paper" if is_paper(page) else "lecture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
