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
thing this exists to prevent. Together they are decisive; the measurement
behind the question bar is at MIN_QUESTION_MATCH.

Imports the standard library and `latin`.
"""
import datetime
import re

from latin import latinize

# How long after the reply the agent may still be writing. Generous, because the
# cost of waiting is one delayed save and the cost of being wrong is a duplicate
# on a page a person reads.
GRACE = datetime.timedelta(minutes=10)


# Below the wording check's own bar, because timing carries most of the weight
# here. Measured over every distinct pair of questions filed on the same page
# (361 pairs, 2026-09-29): the three known duplicates score 0.55-0.83 under this
# tokenizer, and 5 pairs of genuinely different questions reach 0.50 — the same
# count the old tokenizer had at 0.45, and fewer than its 9 at the old 0.40,
# which caught only two of the three duplicates.
MIN_QUESTION_MATCH = 0.50

# Shorter than this and containment means nothing — a three-word question is
# swallowed by anything.
MIN_TOKENS = 4


def _tokens(text: str) -> set:
    """Words worth comparing, with the marker the writer adds removed.

    Latin and Hangul are split into separate runs, because a Korean particle
    glues straight onto the term before it: `pi_ref는` and `figure를` must count
    as `pi_ref` and `figure`, or the same question filed with a different
    particle shares none of its key terms. Greek is spelled out first, since the
    agent writes the paper's symbol (`π_ref`) where the reader typed its name.
    """
    body = re.sub(r"^\s*Q\s*[:.]\s*", "", text or "", flags=re.I)
    runs = re.findall(r"[a-z0-9_]+|[가-힣]+", latinize(body).lower())
    return {w for w in runs if len(w) > 1}


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
