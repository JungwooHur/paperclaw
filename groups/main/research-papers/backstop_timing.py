#!/usr/bin/env python3
"""Whether the agent already filed the answer this exchange produced.

The backstop exists to catch an answer the agent FORGOT to save. It decided
"already saved" by comparing wording — and the agent writes a different, shorter
answer onto the page than it sends to the chat, so the two never looked alike
enough and the same exchange was filed twice.

Wording cannot be made to work here. Measured across real pages: the duplicate
pair's bodies overlapped 0.19, while genuinely DIFFERENT questions on one page
reach 0.26. Any threshold that catches the duplicate merges real answers.

What separates them is WHEN, together with a far weaker question match than
wording alone could justify. Neither signal decides on its own — a callout in
the window may belong to a different question asked in the same minutes, and a
loose question match may be two questions about one subject — and skipping on
either alone would bury an answer the agent genuinely forgot, which is the one
thing this exists to prevent. Together they are decisive: measured across real
pages, different questions on one page overlap 0.14 at the 95th percentile,
while the duplicate pair measured 0.67.

Imports nothing but the standard library.
"""
import datetime
import re

# How long after the reply the agent may still be writing. Generous, because the
# cost of waiting is one delayed save and the cost of being wrong is a duplicate
# on a page a person reads.
GRACE = datetime.timedelta(minutes=10)


# Far below the wording check's own bar, because timing is carrying most of the
# weight here. Chosen from measurement: different questions on one page reach
# 0.14 at the 95th percentile and the duplicate pair reached 0.67.
MIN_QUESTION_MATCH = 0.40

# Shorter than this and containment means nothing — a three-word question is
# swallowed by anything.
MIN_TOKENS = 4


def _tokens(text: str) -> set:
    """Words worth comparing, with the marker the writer adds removed."""
    body = re.sub(r"^\s*Q\s*[:.]\s*", "", text or "", flags=re.I)
    return {w for w in re.findall(r"[\w가-힣]+", body.lower()) if len(w) > 1}


def _same_question(one: str, other: str) -> bool:
    """Do these two name the same question, allowing for a rewrite?"""
    a, b = _tokens(one), _tokens(other)
    if min(len(a), len(b)) < MIN_TOKENS:
        return False
    return len(a & b) / min(len(a), len(b)) >= MIN_QUESTION_MATCH


def _moment(stamp):
    """A timestamp as a datetime, or None if it cannot be read."""
    try:
        return datetime.datetime.fromisoformat(
            str(stamp).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def saved_during_exchange(callouts, asked_at, replied_at, question) -> bool:
    """Was a Q&A written to this page while this exchange was being answered?

    Args:
        callouts: Every Q&A callout on the page, as `{created, question}`.
        asked_at: When the question arrived.
        replied_at: When the answer was sent.
        question: The question this pair would be filed under.

    Returns:
        True when a callout both falls between the question and a grace period
        after the reply AND names the same question. A stamp that cannot be read
        is ignored rather than guessed at, and an unreadable question or reply
        time means this cannot decide at all — the wording check still runs
        either way, so refusing here costs nothing but a second opinion.
    """
    start, end = _moment(asked_at), _moment(replied_at)
    if start is None or end is None:
        return False
    limit = end + GRACE
    for entry in callouts or ():
        made = _moment(entry.get("created"))
        if made is None or not (start <= made <= limit):
            continue
        if _same_question(entry.get("question", ""), question):
            return True
    return False
