#!/usr/bin/env python3
"""When to try a failed Notion request again, and how long to wait first.

Notion rate-limits a burst routinely. Two call paths reach it from here — the
read helpers in `auto_save_qa` and the read/write helper in `translate_fulltext`
— and only the second one retried, so a single 429 on a read killed whatever was
running: a healer cycle lost its whole page, an injection stopped halfway
through a section.

The decision lives here rather than in either caller, because two copies of a
retry policy drift into two different policies, and the one that drifts is
always the one nobody was looking at.

Imports nothing but the standard library, like `reference_section`, so a healer
can never fail to load because of it.
"""

MAX_RETRIES = 4
MAX_RETRY_WAIT = 30.0

# A rate limit and a transient server error are worth repeating. A 400 means the
# payload is wrong and a 404 means the page is not there; repeating either only
# spends the window a real retry needs.
RETRY_CODES = (429, 500, 502, 503, 504)


def should_retry(err, attempt: int, max_retries: int = MAX_RETRIES) -> bool:
    """Is this failure worth trying again?

    Args:
        err: The `HTTPError` that was raised.
        attempt: Which attempt just failed, counting from 1.
        max_retries: How many attempts the caller allows in total.

    Returns:
        True when the code is transient and attempts remain.
    """
    return attempt < max_retries and getattr(err, 'code', None) in RETRY_CODES


def retry_delay(err, attempt: int) -> float:
    """Seconds to wait before the next attempt.

    Notion's own `Retry-After` wins when it is present and readable, because it
    knows when the window reopens. Otherwise the wait doubles. Either way it is
    capped: a retry that sleeps for minutes is a hang wearing a retry's clothes.
    """
    header = (getattr(err, 'headers', None) or {}).get('Retry-After')
    try:
        wait = float(header)
    except (TypeError, ValueError):
        wait = 1.0 * (2 ** (attempt - 1))
    return min(max(wait, 0.5), MAX_RETRY_WAIT)
