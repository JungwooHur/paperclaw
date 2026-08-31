#!/usr/bin/env python3
"""Out-of-band scanner: create Notion Q&A callouts from WhatsApp bot
answers the agent forgot to save.

Recurring problem: the agent answers a paper question in chat and skips
step 4 of the Paper Q&A workflow (save to Notion). `auto_fix_qa.py`
can't heal this because there's no callout to fix — it was never
created in the first place. This scanner walks the messages DB and
retroactively creates the callout so future scans see it as healed.

Algorithm (per chat):
  1. Load recent messages (last N hours).
  2. Build a window of "active paper context": for each paper in
     $NOTION_RESEARCH_DB, check if a distinctive title keyword appears
     anywhere in the recent window. The most recently mentioned paper
     is the active one.
  3. Walk user→bot consecutive pairs. For each pair where the bot reply
     is a substantive answer (>=1500 chars, structured markdown, not a
     daily-report / scheduled-task summary), and the chat has an active
     paper, check whether any existing top-level callout on that paper
     page already contains the user question. If not, invoke
     `save_qa_callout.py` to create it.

Env: NOTION_TOKEN, NOTION_RESEARCH_DB
Usage: python3 auto_save_qa.py [--dry-run] [--hours N] [--chat JID]
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, subprocess, sys, time
import urllib.request, urllib.error

import layer_format

API = "https://api.notion.com/v1"

# Resolve PaperClaw root relative to this script so the healer doesn't carry a
# hardcoded user path. systemd's WorkingDirectory may differ, but this script
# lives at <repo>/groups/main/research-papers/auto_save_qa.py.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MSGS_DB = os.environ.get("PAPERCLAW_MSGS_DB", os.path.join(_REPO_ROOT, "store", "messages.db"))
SAVE_SCRIPT = os.path.join(_REPO_ROOT, "groups", "main", "research-papers", "save_qa_callout.py")
# See auto_fix_qa.py — Notion occasionally hangs mid-request; always use an
# explicit timeout so a single bad call can't block the whole scan cycle.
HTTP_TIMEOUT = 30

# Common words to drop from title keyword extraction. If you add an entry,
# make it lowercase — the membership test is case-insensitive.
#
# Heuristic: if a word appears in many paper titles AND is plausibly present
# in unrelated chat text (e.g. a task-completion report, a code block, a
# general ML question), it's a false-positive magnet for Tier 3 distinct-kw
# scoring. Keep it here. Overly aggressive filtering is safer than leaking
# generic words: an April 2026 misdetection between two papers was caused by
# "Control", "Space", and "Action" matching scattered mentions of those ML
# primitives.
COMMON_WORDS = {
    "the", "and", "for", "with", "from", "via", "using", "based", "towards",
    "learning", "model", "models", "training", "system", "method", "methods",
    "approach", "network", "networks", "deep", "neural", "paper", "pages",
    "a", "an", "of", "on", "in", "to", "is", "as", "by", "at", "or", "not",
    "new",
    # Generic paper-title descriptors
    "what", "matters", "empirical", "study", "analysis", "evaluation",
    "comparison", "benchmark", "survey", "taxonomy", "framework", "unified",
    "overview", "review", "investigation", "towards", "exploring",
    # Generic ML/RL primitives that show up everywhere in research-paper chat
    "control", "action", "actions", "space", "spaces", "reward", "policy",
    "policies", "agent", "agents", "environment", "environments", "state",
    "states", "observation", "observations", "task", "tasks", "goal",
    "goals", "loss", "losses", "optimization", "gradient", "gradients",
    "representation", "representations", "feature", "features", "embedding",
    "embeddings", "latent", "prediction", "predictions",
}

MIN_ANSWER_CHARS = 1200           # substantive answer threshold
MIN_PAPER_ANSWER_CHARS = 400      # ...relaxed once the pair is tied to a paper
DEFAULT_LOOKBACK_HOURS = 48
MAX_MSGS_PER_CHAT = 200           # cap scan per chat


def headers() -> dict:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        sys.exit("NOTION_TOKEN not set")
    return {
        "Authorization": f"Bearer {tok}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def api_get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers=headers())
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


def api_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path, method="POST",
        data=json.dumps(body).encode(), headers=headers(),
    )
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


# ---- Notion ---------------------------------------------------------------

def load_paper_pages() -> list[dict]:
    """Return [{'id', 'title', 'url', 'keywords'}]. Uses sort-stabilized pagination
    (see auto_fix_qa.py — unsorted queries silently drop pages)."""
    db = os.environ.get("NOTION_RESEARCH_DB")
    if not db:
        sys.exit("NOTION_RESEARCH_DB not set")
    out: list[dict] = []
    cur = None
    while True:
        body: dict = {
            "page_size": 100,
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        }
        if cur: body["start_cursor"] = cur
        d = api_post(f"/databases/{db}/query", body)
        for p in d["results"]:
            title = ""
            for k, v in p["properties"].items():
                if v.get("type") == "title":
                    title = "".join(r["plain_text"] for r in v["title"])
                    break
            url = ""
            for v in p["properties"].values():
                if v.get("type") == "url" and v.get("url"):
                    url = v["url"]
                    break
            out.append({
                "id": p["id"],
                "title": title,
                "url": url,          # carries the arxiv id, the one exact identifier
                "keywords": extract_title_keywords(title),
            })
        if d.get("has_more"): cur = d["next_cursor"]
        else: break
    # Build the keyword-document-frequency table used by _weighted_kw.
    _KW_DF.clear()
    for p in out:
        # each paper contributes once per distinct keyword
        for kw in set(w.lower() for w in p["keywords"]):
            _KW_DF[kw] = _KW_DF.get(kw, 0) + 1
    return out


# A model named with a Greek letter is written one way in the title and another
# by the person asking about it: the page says "\u03c00", the question says "pi0".
# Nobody types \u03c0 on a phone. Transliterating both sides makes the model name a
# normal token again — and it is the most distinctive token a title has.
_GREEK = {
    "\u03b1": "alpha", "\u03b2": "beta", "\u03b3": "gamma", "\u03b4": "delta",
    "\u03b5": "epsilon", "\u03b6": "zeta", "\u03b7": "eta", "\u03b8": "theta",
    "\u03b9": "iota", "\u03ba": "kappa", "\u03bb": "lambda", "\u03bc": "mu",
    "\u03bd": "nu", "\u03be": "xi", "\u03c0": "pi", "\u03c1": "rho",
    "\u03c3": "sigma", "\u03c4": "tau", "\u03c6": "phi", "\u03c7": "chi",
    "\u03c8": "psi", "\u03c9": "omega",
    "\u0391": "Alpha", "\u0392": "Beta", "\u0393": "Gamma", "\u0394": "Delta",
    "\u039b": "Lambda", "\u03a0": "Pi", "\u03a3": "Sigma", "\u03a6": "Phi",
    "\u03a8": "Psi", "\u03a9": "Omega",
}


# A model name is also typeset with SUBSCRIPT digits — "\u03c0\u2080.\u2087" — which no
# keyboard produces either, and a star between the letter and the number
# ("\u03c0*0.6") splits the token in two. Both are decoration on the same name.
_SUBDIGIT = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"
                          "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079",
                          "01234567890123456789")


def latinize(text: str) -> str:
    """Greek letters spelled out, so a title and a phone keyboard can meet."""
    if not text:
        return ""
    for g, name in _GREEK.items():
        if g in text:
            text = text.replace(g, name)
    text = text.translate(_SUBDIGIT)
    return re.sub(r"(?<=[A-Za-z])\*(?=[0-9])", "", text)


def extract_title_keywords(title: str) -> list[str]:
    """Distinctive tokens from a paper title — drop common words and
    too-short tokens. Title fragments are matched case-insensitively.

    Dedupe case-insensitively so a title like "... Space: An Action Space"
    doesn't double-count "Space" in the distinct-kw score.

    A token of 3 characters is kept when it carries a DIGIT: `pi0`, `rt2`, `gr2`
    are model names, as title-unique as a token gets, while a 3-letter English
    word is the opposite. Without that, the one word identifying the paper was
    dropped and scoring fell back to generic title words, which is how a question
    naming its paper outright was attributed to a different one.
    """
    title = latinize(title)
    words = re.findall(r"[A-Za-z가-힣][A-Za-z0-9가-힣\-]{3,}"
                       r"|[A-Za-z]{1,3}[0-9][0-9.]*", title)
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in COMMON_WORDS or lw in seen:
            continue
        seen.add(lw)
        out.append(w)
    return out


def _block_text(b: dict) -> str:
    t = b.get("type")
    payload = b.get(t, {}) if t else {}
    rts = payload.get("rich_text", []) if isinstance(payload, dict) else []
    return "".join(r.get("plain_text", "") for r in rts)


def fetch_top_callouts(page_id: str) -> list[dict]:
    """Return [{'question': str, 'body': str}] for each top-level toggle-style
    toggle callout on the page — used for already-saved detection by
    comparing either the question text or the answer body."""
    cur, out = None, []
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cur: path += f"&start_cursor={cur}"
        d = api_get(path); out.extend(d["results"])
        if d.get("has_more"): cur = d["next_cursor"]
        else: break
    results = []
    for b in out:
        if b["type"] != "callout": continue
        if not b.get("has_children"): continue
        kids_path = f"/blocks/{b['id']}/children?page_size=100"
        k = api_get(kids_path).get("results", [])
        question = ""
        body_parts: list[str] = []
        if k and k[0]["type"] == "toggle":
            # toggle-style: toggle label is the question, toggle children are body
            question = "".join(r["plain_text"]
                               for r in k[0]["toggle"].get("rich_text", []))
            tpath = f"/blocks/{k[0]['id']}/children?page_size=100"
            try:
                tkids = api_get(tpath).get("results", [])
                body_parts = [_block_text(bb) for bb in tkids]
            except Exception:
                pass
        elif b["callout"].get("rich_text"):
            question = "".join(r["plain_text"]
                               for r in b["callout"]["rich_text"])
            body_parts = [_block_text(bb) for bb in k]
        body = "\n".join(p for p in body_parts if p).strip()
        results.append({"question": question, "body": body})
    return results


# ---- Messages -------------------------------------------------------------

def fetch_recent_messages(hours: int, chat_filter: str | None):
    c = sqlite3.connect(MSGS_DB)
    c.row_factory = sqlite3.Row
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                          time.gmtime(time.time() - hours * 3600))
    q = """SELECT id, chat_jid, sender_name, content, timestamp,
                  is_from_me, is_bot_message
           FROM messages
           WHERE timestamp >= ?"""
    params = [since]
    if chat_filter:
        q += " AND chat_jid = ?"
        params.append(chat_filter)
    q += " ORDER BY chat_jid, timestamp ASC"
    return [dict(r) for r in c.execute(q, params)]


def group_by_chat(msgs: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for m in msgs: g.setdefault(m["chat_jid"], []).append(m)
    return g


# ---- Analysis -------------------------------------------------------------

def _count_kw(text: str, kws: list[str]) -> int:
    """Total keyword occurrences (same keyword counted multiple times)."""
    n = 0
    for kw in kws:
        n += len(re.findall(_kw_pattern(kw), text, re.I))
    return n


def _distinct_kw(text: str, kws: list[str]) -> int:
    """Count of distinct keywords that appear (better signal than raw count
    — a GAN answer mentions 'Adversarial' 20× but that's one keyword)."""
    return sum(1 for kw in kws
               if re.search(_kw_pattern(kw), text, re.I))


# Populated lazily by load_paper_pages() — keyword → number of paper titles
# that contain that keyword (case-insensitive). Used to break ties when
# several papers have the same distinct-kw count against the chat text:
# a paper whose matched kws are mostly title-unique compound names
# beats one whose matched kws are widely shared generic English (like
# "world" + "planning"). Inverse document frequency — classic trick.
_KW_DF: dict[str, int] = {}


def _weighted_kw(text: str, kws: list[str]) -> float:
    """Sum of IDF-weighted hits. kws with low document frequency in the
    overall paper DB count more. Used only as a tiebreaker — the base
    filter is still _distinct_kw ≥ 2."""
    score = 0.0
    for kw in kws:
        if re.search(_kw_pattern(kw), text, re.I):
            df = _KW_DF.get(kw.lower(), 1)
            # idf(x) = 1 / df — rare kws weigh more. Use 1/df rather than
            # log(N/df) so the difference between df=1 and df=20 is very
            # pronounced (matters most for title-unique compound paper
            # names that should dominate any generic kw).
            score += 1.0 / df
    return score


# `\b` does not hold between a latin word and a Korean particle: in "FAST논문에서"
# both `T` and `논` are word characters, so `\bFAST\b` does not match and the paper
# the user NAMED becomes invisible. The same trap has now been fixed three times
# in this repo — section labels, figure anchors, and here. Reject only a longer
# token in the SAME script, which is the thing a boundary actually needs to do.
def _kw_pattern(kw: str) -> str:
    return r"(?<![A-Za-z0-9])" + re.escape(kw) + r"(?![A-Za-z0-9])"


def _has_paper_reference(text: str, kws: list[str]) -> bool:
    """Explicit '[keyword] 논문' / '[keyword] paper' — very strong signal
    that this paper is the topic under discussion."""
    text = latinize(text)
    for kw in kws:
        if re.search(_kw_pattern(kw) + r"[^.\n]{0,30}?(?:논문|paper\b)",
                     text, re.I):
            return True
        if re.search(r"(?:논문|paper)[^.\n]{0,10}?" + _kw_pattern(kw),
                     text, re.I):
            return True
    return False


# Short enough that a partly-typed title still resolves, long enough that a generic
# phrase ("positional encoding") cannot match several papers at once.
_TITLE_PREFIX_MIN = 20


def _norm_for_title(text: str) -> str:
    """Letters and digits only — titles differ by punctuation, case and spacing."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _arxiv_id_of(paper: dict) -> str:
    m = re.search(r"\b(\d{4}\.\d{4,5})\b", paper.get("url") or "")
    return m.group(1) if m else ""


# A title that opens with an all-caps acronym — "FAST: Efficient Action
# Tokenization…" — carries a name people actually use: they write "FAST논문에서",
# not the full title. That name is title-unique in practice (of 57 such papers in
# the live DB, only one acronym is shared), and it is the ONLY distinctive token
# such a question contains — so it can never clear the "two distinct keywords"
# bar below, and the pair loses to a paper matching two generic words instead.
# Measured: a question naming this paper outright was about to be filed under an
# unrelated one whose title merely contains the word "fast".
# The paper's NAME is the token its title opens with, before the colon: an
# all-caps acronym, or a model number like the Greek-lettered ones (latinized
# first, so "\u03c00.5:" reads as "pi0.5:"). Document frequency cannot stand in for
# this — "fast" occurs in 14 titles, yet only one paper is NAMED FAST.
_NAME = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,9}(?:[-.][A-Za-z0-9]+)*)\s*[:\u2014-]")


def title_acronym(title: str) -> str | None:
    """The paper's leading name token, or None if the title does not open with one."""
    m = _NAME.match(latinize(title or "").strip())
    if not m:
        return None
    tok = m.group(1)
    if tok.isupper() and len(tok) >= 2:
        return tok                     # FAST, GUAPO, D2RL
    if any(c.isdigit() for c in tok):
        return tok                     # pi0.5, GR-2
    return None


_TEXT_CACHE: dict[str, bool] = {}


def _page_has_text(page_id: str, min_chars: int = 2000) -> bool:
    """Does this page carry a translation? Cached — a name collision is rare."""
    if page_id in _TEXT_CACHE:
        return _TEXT_CACHE[page_id]
    chars = 0
    try:
        d = api_get(f"/blocks/{page_id}/children?page_size=100")
        for b in d.get("results", []):
            if b.get("type") == "image":
                continue
            payload = b.get(b.get("type")) or {}
            if isinstance(payload, dict):
                chars += sum(len(x.get("plain_text", ""))
                             for x in payload.get("rich_text", []))
    except Exception:
        chars = min_chars              # unknown beats "empty"
    _TEXT_CACHE[page_id] = chars >= min_chars
    return _TEXT_CACHE[page_id]


def active_paper_at(window: list[dict], papers: list[dict],
                    bot_reply: str, user_question: str) -> dict | None:
    """Pick the paper for this Q&A pair.

    The current user↔bot pair is always the strongest signal — what the
    user just asked about IS the paper in play. History is used only
    (a) as a tiebreaker and (b) to recover the paper when the current
    pair is ambiguous but the user/bot implicitly continues an earlier
    paper thread.

    Priority (most → least reliable):
      1. Current pair has explicit '[kw] 논문' / 'paper [kw]' AND ≥2
         distinct keywords of that paper — strongest signal.
      2. Current pair has ≥2 distinct keywords of a paper (tier 3 of
         old ordering, promoted).
      3. History has explicit '[kw] 논문' AND the current pair also
         mentions at least one distinct keyword of that paper
         (consistency check — without this, a stray 'Methods paper'
         in a prior task-completion message wins over a totally
         unrelated current Q&A).
      4. Window scan: history msg with ≥2 distinct kws, most recent
         wins, AND current pair shares ≥1 of those kws.
    """
    history = window[:-1]  # everything except the current bot reply
    pair_text = bot_reply + "\n" + user_question

    # Tier 0 — the pair NAMES the paper: its full title, or its arxiv id. This is
    # concrete evidence and outranks every keyword heuristic. Without it, a question
    # quoting a title verbatim was still filed under a different paper, because the
    # tier below returned the FIRST paper that cleared a threshold rather than the
    # best one, and a sibling sharing a generic word ("transformer") came first.
    # The USER's question is checked on its own first. Searching the whole pair at
    # once let a bot reply that ENUMERATES other papers win on "longest title": a
    # question naming one paper verbatim was filed under a different paper the bot
    # had merely listed. What the user asked about is the paper in play.
    for text in (user_question, pair_text):
        named = []
        norm = _norm_for_title(text)
        for p in papers:
            title_norm = _norm_for_title(p.get("title", ""))
            # A PREFIX counts, not just the whole title. People name a paper by its
            # opening words ("The impact of positional encoding 논문에서 …"), and
            # requiring the full title made that miss — the pair then fell through
            # to keyword scoring and was filed under a different paper that merely
            # shares a generic word. The longest prefix wins, so a paper whose title
            # starts the same way but continues differently cannot steal it.
            hit = 0
            for length in range(len(title_norm), _TITLE_PREFIX_MIN - 1, -1):
                if title_norm[:length] in norm:
                    hit = length
                    break
            if hit:
                named.append((hit, p))
                continue
            aid = _arxiv_id_of(p)
            if aid and aid in text:
                named.append((10_000, p))
        if named:
            named.sort(key=lambda x: -x[0])   # arxiv id, else longest title match
            return named[0][1]

    # Tier 0.5 — the question names the paper by a TITLE-UNIQUE word, right next to
    # "논문"/"paper". That is naming, not keyword overlap, and it is decisive.
    #
    # It has to sit above the "≥2 distinct keywords" bar rather than inside it,
    # because a paper known by one name — an acronym, or a model number like the
    # Greek-lettered ones — offers exactly ONE distinctive token, and a question
    # about it contains nothing else to count. It therefore always lost to some
    # unrelated paper matching two generic words. Seen twice, on two different
    # papers, each filed under something that merely shared a common word.
    #
    # Uniqueness is measured, not assumed: only a keyword whose document frequency
    # across the whole DB is 1 qualifies. An acronym is checked case-SENSITIVELY —
    # the capitals are what make it a name, a lowercase spelling is just a word —
    # while a model number has no case to speak of.
    by_name: dict[str, list[dict]] = {}
    for p in papers:
        nm = title_acronym(p.get("title", ""))
        if nm:
            by_name.setdefault(nm.lower(), []).append(p)
    hits = []
    for nm, owners in by_name.items():
        # Several DIFFERENT papers sharing a name is ambiguous; several copies of
        # the SAME paper is not — duplicates are common here, and refusing on them
        # would throw away the clearest evidence a question can carry.
        # Several papers can open with the same name for two reasons: they are
        # copies of one paper, or one of them is a stub the collector added under a
        # shortened title. Neither is real ambiguity — the paper the reader means is
        # the one that HAS the paper on it. Only when more than one owner carries
        # content is the name genuinely shared, and then it decides nothing.
        withtext = [o for o in owners if _page_has_text(o["id"])] or owners
        if len({_norm_for_title(o.get("title", "")) for o in withtext}) > 1:
            continue
        owner = max(withtext, key=lambda o: o.get("last_edited_time", ""))
        pat = r"(?<![A-Za-z0-9])" + re.escape(nm) + r"(?![A-Za-z0-9])"
        q = latinize(user_question)
        if re.search(pat + r"[^.\n]{0,20}?(?:논문|paper\b)", q, re.I) \
           or re.search(r"(?:논문|paper)[^.\n]{0,10}?" + pat, q, re.I):
            hits.append((len(nm), owner))
    if hits:
        hits.sort(key=lambda x: -x[0])        # the longest name is the most specific
        if len(hits) == 1 or hits[0][0] > hits[1][0]:
            return hits[0][1]

    # Tier 1 — explicit paper reference in current pair with ≥2 distinct kws.
    # Rank the candidates instead of taking the first one the DB happens to list.
    # The USER's question decides; the bot's reply is only a fallback. Ranking both
    # together let the ANSWER win, and an answer is the wrong thing to score: it is
    # a technical explanation, so its vocabulary matches any paper whose title
    # happens to share a rare word with the topic. A question that named its paper
    # outright was filed under an unrelated one twice over — the explanation of the
    # method scored 1.07 on words like "interpolation" and "smoothing" against 0.64
    # for the paper the user actually asked about. This is the same rule Tier 0
    # already follows, applied here too.
    for text in (user_question, bot_reply):
        tier1 = []
        for p in papers:
            if (_has_paper_reference(text, p["keywords"])
                    and _distinct_kw(text, p["keywords"]) >= 2):
                tier1.append((_weighted_kw(text, p["keywords"]),
                              _distinct_kw(text, p["keywords"]), p))
        if tier1:
            tier1.sort(key=lambda x: (-x[0], -x[1]))
            return tier1[0][2]

    # Tier 2 — an EXPLICIT "X 논문" statement earlier in the thread, when the
    # current pair is consistent with it. This now outranks the keyword score
    # below: naming a paper is evidence, a keyword score is a guess, and a
    # follow-up ("그러니까, …") names nothing, so the guess used to win and filed
    # the answer on a neighbouring page. Explicit paper reference in history, with current-pair
    # consistency check. Most recent wins.
    for m in reversed(history):
        for p in papers:
            if (_has_paper_reference(m["content"], p["keywords"])
                    and _distinct_kw(m["content"], p["keywords"]) >= 2
                    and _distinct_kw(pair_text, p["keywords"]) >= 1):
                return p

    # Tier 3 — distinct keywords in current pair (no "paper" mention
    # needed). Base filter: ≥2 distinct kws AND IDF-weighted score ≥ 0.5
    # (i.e. at least one matched kw must be reasonably rare — a title-
    # unique compound name scores 1.0, so this passes any
    # paper with at least one distinctive kw hit). The weight threshold
    # suppresses bogus matches where the only signal is 2 generic English
    # words shared by 20+ paper titles (e.g. "world" + "planning").
    scored = []
    for p in papers:
        n = _distinct_kw(pair_text, p["keywords"])
        if n < 2:
            continue
        w = _weighted_kw(pair_text, p["keywords"])
        if w < 0.5:
            continue
        scored.append((n, w, p))
    if scored:
        scored.sort(key=lambda x: (-x[1], -x[0]))
        return scored[0][2]

    # Tier 4 — window scan with current-pair consistency check
    hits: list[tuple[int, dict]] = []
    for p in papers:
        if _distinct_kw(pair_text, p["keywords"]) < 1:
            continue
        last = -1
        for i, m in enumerate(history):
            if _distinct_kw(m["content"], p["keywords"]) >= 2:
                last = i
        if last >= 0:
            hits.append((last, p))
    if not hits: return None
    hits.sort(key=lambda x: -x[0])
    return hits[0][1]


def is_substantive_answer(content: str, paper_context: bool = False) -> bool:
    """Is this bot message an answer worth filing on a paper page?

    `paper_context` means the caller has ALREADY tied this pair to a specific
    paper (resolved paper page + the pair naming something paper-specific). That
    is far stronger evidence than any shape heuristic, so the markdown-structure
    score is dropped there and the length floor lowered: a correct answer about
    one equation is often plain prose and well under 1200 chars, and rejecting it
    is exactly the "질문이 자동으로 안 올라간다" complaint — the agent skipped Step 4
    and the backstop then declined to cover for it.
    """
    # A layer explanation from the guided-reading loop is long, substantive and
    # about a paper — everything this function looks for — but it is already
    # being recorded, in the reader's own words rather than the assistant's.
    # Filing it here would duplicate the material in the weaker shape.
    if layer_format.is_layer(content):
        return False
    floor = MIN_PAPER_ANSWER_CHARS if paper_context else MIN_ANSWER_CHARS
    if not content or len(content) < floor:
        return False
    # exclude scheduled task summaries / daily reports / "task done" confirmations
    low = content.lower()
    bad_signals = [
        "daily research update", "papers added", "scheduled task",
        "task has been completed", "added 1 new paper", "added 2 new paper",
        "nightly research",
        # Korean task-completion confirmations (paper translation / queue done)
        "완료되었습니다", "정리 완료", "notion에 정리했",
        "모든 논문 처리", "번역이 완료", "추가 완료", "저장 완료",
    ]
    if any(s in low for s in bad_signals):
        return False
    # require some markdown structure (headings, tables, code, bullets)
    score = 0
    if "###" in content: score += 2
    if "\n```" in content: score += 2
    if re.search(r"^\s*[-*]\s", content, re.M): score += 1
    if re.search(r"^\s*\d+\.\s", content, re.M): score += 1
    if "|" in content and content.count("|") >= 6: score += 2
    # Math is structure too — a derivation answer often has no headings or bullets.
    if re.search(r"\$[^$\n]+\$|\\\[|\\frac|\\theta|\\sum", content): score += 2
    if paper_context:
        return True          # paper identity already carries the decision
    return score >= 2


def is_question_like(content: str) -> bool:
    """Filter out imperative commands (추가해, 정리하자, 번역해, 찾아줘, ...)
    which aren't Q&A-worthy. Questions typically: end with '?', contain
    '뭐/무엇/왜/어떻게/어디', or ask for an explanation."""
    if not content: return False
    c = content.strip()
    if len(c) < 10: return False
    # Imperative paper-management commands — skip
    imperative_tails = [
        "정리하자", "정리해", "정리해줘", "추가해", "추가해줘",
        "찾아줘", "찾아봐", "번역해", "번역해줘", "올려줘", "저장해",
    ]
    cl = c.lower()
    if any(cl.endswith(t) for t in imperative_tails):
        return False
    if "?" in c:
        return True
    # A sentence that CLOSES like a statement is a remark, not a question. One such
    # ("…그 결과를 보여준다.") was filed as a Q&A, so the callout asked nothing.
    if re.search(r"(다|네|군|구나|음|임|죠|네요|습니다)\.?$", c):
        return False
    # Explicit "I want this explained" markers. The old rule ended here with
    # `len(c) >= 60 and "해" not in c[-4:]`, which rejected a real question purely
    # because it happened to end on a syllable containing 해 ("…까지는 이해했어") —
    # a question about the paper then never reached Notion at all.
    if re.search(r"(왜|뭐야|무엇|어떻게|어디|누구|언제|얼마|차이|의미|설명해|알려줘|뜻이야"
                 r"|이해가 안|이해 안|모르겠|헷갈|궁금|무슨|어느|은지|는지|을까|ㄹ까)", c):
        return True
    return len(c) >= 60


def bot_mentions_paper(content: str, paper: dict) -> bool:
    """Soft signal: bot reply cites paper-specific concepts (section #,
    figure #, equation #, or a keyword from the paper title)."""
    if re.search(r"\b(?:Eq(?:uation)?|Fig(?:ure)?|Section|Sec\.)\s*\d", content):
        return True
    for kw in paper["keywords"]:
        if re.search(_kw_pattern(kw), content, re.I):
            return True
    return False


def _norm_tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z가-힣]{3,}", text.lower()))


_SECTION_LABEL = re.compile(r"(?<![\w.])(\d{1,2}(?:\.\d{1,2}){1,2})(?![\d.])(?!\s*배)")


def guess_section_label(question: str, answer: str) -> str:
    """The section this Q&A belongs to, as a heading label like "3.4.3".

    The question is authoritative — it is what the reader asked about — and only
    if it names no section do we look at the answer. Deepest label wins, so a
    question about 3.2.2 is not filed under 3.2.
    """
    for text in (question, answer):
        found = _SECTION_LABEL.findall(text or "")
        if found:
            return max(found, key=len)
    return ""


def already_saved(question: str, answer: str,
                  existing: list[dict]) -> bool:
    """Return True if an existing callout already captures this Q&A.

    Match on either (a) question text similarity OR (b) answer body
    similarity. Agents sometimes rephrase the question when saving
    (e.g. literal user message → cleaned-up question), so body match
    is the more reliable deduplication signal.
    """
    stem = re.sub(r"^(Q[:.\s]*)", "", question).strip()
    stem_norm = re.sub(r"\s+", " ", stem[:100].lower())
    q_tokens = _norm_tokens(stem)
    # Sample first/middle/last snippets of the answer for body matching
    a_tokens = _norm_tokens(answer)
    a_first_200 = re.sub(r"\s+", " ", answer[:300].lower())

    for e in existing:
        eq = e.get("question", "")
        eb = e.get("body", "")
        eq_norm = re.sub(r"\s+", " ", eq.lower())
        eb_norm = re.sub(r"\s+", " ", eb[:300].lower())

        # (a) question prefix or substring
        if stem_norm and (stem_norm[:60] in eq_norm or eq_norm[:60] in stem_norm):
            return True
        # (a') question token-set overlap
        eq_tokens = _norm_tokens(eq)
        if q_tokens and eq_tokens:
            inter = len(q_tokens & eq_tokens)
            if inter / max(len(q_tokens), len(eq_tokens)) >= 0.65:
                return True
            # CONTAINMENT, not just symmetric overlap. When the agent saves a Q&A it
            # rewrites the question ("…27번 수식까지는 이해했어" -> "…27번 수식 이후부터 왜
            # position…"), so one wording is largely contained in the other while the
            # symmetric ratio stays low — a real pair measured 0.38 symmetric but 0.75
            # contained, and was about to be filed a second time. Unrelated questions
            # on the same page measured 0.12, so the two are far apart. Require enough
            # tokens that a short question cannot be swallowed by a longer one.
            if (min(len(q_tokens), len(eq_tokens)) >= 4
                    and inter / min(len(q_tokens), len(eq_tokens)) >= 0.70):
                return True

        # (b) answer body token-set overlap — the robust signal
        eb_tokens = _norm_tokens(eb)
        if a_tokens and eb_tokens:
            overlap = len(a_tokens & eb_tokens) / max(len(a_tokens), len(eb_tokens))
            if overlap >= 0.45: return True
        # (b') direct prefix containment
        if a_first_200 and eb_norm and (
            a_first_200[:120] in eb_norm or eb_norm[:120] in a_first_200
        ):
            return True
    return False


def strip_bot_prefix(content: str) -> str:
    """Remove the 'Claude Paper Reviewer: ' lead-in and any trailing
    WhatsApp pleasantries for a cleaner callout body."""
    content = re.sub(r"^Claude Paper Reviewer:\s*", "", content)
    content = re.sub(r"\n+더 궁금한[^\n]*$", "", content).strip()
    return content


# ---- Save -----------------------------------------------------------------

def save_callout(page_id: str, question: str, answer_md: str,
                 expect_title: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"    DRY: would save Q='{question[:60]}' to page={page_id[-12:]}",
              file=sys.stderr)
        return True
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(answer_md)
        answer_path = f.name
    cmd = ["python3", SAVE_SCRIPT,
           "--page", page_id,
           "--expect-title", expect_title,
           "--question", question[:2000],
           "--answer-file", answer_path]
    # Aim at a section. Without --section the callout is appended at the PAGE END,
    # and auto_fix_qa only re-places callouts that are nested under another block —
    # a top-level one keeps its position — so everything this backstop saved stayed
    # stranded after the conclusion, far from the section it is about.
    section = guess_section_label(question, answer_md)
    try:
        r = subprocess.run(cmd + (["--section", section] if section else []),
                           capture_output=True, text=True)
        if r.returncode != 0 and section:
            # The page may not label that heading; placing it at the end is still
            # better than not saving it at all.
            print(f"    section '{section}' did not match — retrying unplaced",
                  file=sys.stderr)
            r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    save FAIL: {r.stderr.strip()[:300]}", file=sys.stderr)
            return False
        print(f"    saved: {r.stdout.strip()}", file=sys.stderr)
        return True
    finally:
        try: os.unlink(answer_path)
        except Exception: pass


# ---- Main -----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    ap.add_argument("--chat", help="scan only this chat JID")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    papers = load_paper_pages()
    msgs = fetch_recent_messages(args.hours, args.chat)
    chats = group_by_chat(msgs)

    # Cache of (page_id → existing [{question, body}] dicts)
    q_cache: dict[str, list[dict]] = {}

    saved_count = 0
    for chat_jid, ordered in chats.items():
        if len(ordered) > MAX_MSGS_PER_CHAT:
            ordered = ordered[-MAX_MSGS_PER_CHAT:]
        # iterate bot messages; treat the preceding non-bot msg as the question
        for i, m in enumerate(ordered):
            if not (m["is_bot_message"] and m["is_from_me"]):
                continue
            # Cheap floor only; the real decision is made below, once the pair
            # has been tied to a paper (see is_substantive_answer's docstring).
            if not m["content"] or len(m["content"]) < MIN_PAPER_ANSWER_CHARS:
                continue
            # find preceding user message (not from bot)
            user_msg = None
            for j in range(i - 1, -1, -1):
                n = ordered[j]
                if n["is_bot_message"]: continue
                # anything typed by a human counts as question
                user_msg = n
                break
            if user_msg is None: continue
            if not is_question_like(user_msg["content"]): continue
            # active paper context uses a sliding 30-msg window up to this bot msg
            window = ordered[max(0, i - 30): i + 1]
            paper = active_paper_at(window, papers,
                                    bot_reply=m["content"],
                                    user_question=user_msg["content"])
            if paper is None:
                continue
            # extra guard: either bot answer cites paper-specific stuff, or
            # the user message does (otherwise it's a random conversation in
            # a chat where a paper happened to be mentioned earlier).
            if not (bot_mentions_paper(m["content"], paper)
                    or bot_mentions_paper(user_msg["content"], paper)):
                continue
            # The pair is now tied to a paper, so judge the answer with that in
            # hand. An answer that OFFERS to save ("Notion에 추가할까요?") is proof the
            # agent did not save it — exactly the pair this backstop exists for.
            if not is_substantive_answer(m["content"], paper_context=True):
                continue
            question = f"Q: {user_msg['content'].strip()}"
            answer_md = strip_bot_prefix(m["content"])

            # Dedup check across ALL plausible paper pages, not just the
            # resolved one. Historical incident: the resolver used to pick
            # the wrong paper for two concept Q&As, and
            # the per-page-only dedup would have happily created bogus
            # duplicates on the wrong paper's page. By checking any paper
            # page that shares ≥1 distinct kw with the current pair, we
            # catch Q&As that were correctly saved on a sibling page
            # before the resolver was improved.
            pair_text = m["content"] + "\n" + user_msg["content"]
            candidate_ids = {paper["id"]}
            for cp in papers:
                if cp["id"] == paper["id"]: continue
                if _distinct_kw(pair_text, cp["keywords"]) >= 1:
                    candidate_ids.add(cp["id"])
            # Also every paper the CONVERSATION has been about. A follow-up names
            # nothing ("그러니까, …"), so it shares no title keyword with the paper
            # under discussion and the pair-only filter above cannot see the page
            # where this very Q&A was already filed — it would be saved a second
            # time on whichever page the resolver picked.
            window_text = "\n".join(w["content"] or "" for w in window)
            ctx = sorted(((_weighted_kw(window_text, cp["keywords"]), cp["id"])
                          for cp in papers
                          if _distinct_kw(window_text, cp["keywords"]) >= 2),
                         reverse=True)
            # Cap it: every candidate is one Notion fetch, and an unbounded set trips
            # the rate limit, which now defers the pair instead of deduping it.
            candidate_ids.update(pid for _, pid in ctx[:8])

            already = False
            unknown = False
            for pid in candidate_ids:
                existing = q_cache.get(pid)
                if existing is None:
                    try:
                        existing = fetch_top_callouts(pid)
                    except Exception as e:
                        # FAIL CLOSED. This used to fall back to `existing = []`,
                        # i.e. "the page has no Q&A" — so a transient 429 turned the
                        # dedup off and the pair was filed again. A duplicate callout
                        # is permanent; a save deferred to the next cycle is not.
                        print(f"  fetch {pid} failed: {e} — deferring this pair",
                              file=sys.stderr)
                        unknown = True
                        break
                    q_cache[pid] = existing
                if already_saved(question, answer_md, existing):
                    if pid != paper["id"]:
                        print(f"  skip (already on different paper page "
                              f"{pid[-12:]}): {question[:60]}", file=sys.stderr)
                    already = True
                    break
            if unknown:
                continue
            if already:
                continue

            print(f"[{chat_jid[:20]:20}] {m['timestamp']}: missing Q&A for "
                  f"paper='{paper['title'][:50]}'", file=sys.stderr)
            print(f"    Q: {user_msg['content'][:80]}", file=sys.stderr)
            ok = save_callout(paper["id"], question, answer_md,
                              paper["title"], args.dry_run)
            if ok:
                saved_count += 1
                # Add to cache so subsequent messages don't re-create
                q_cache[paper["id"]].append({"question": question,
                                              "body": answer_md[:500]})

    if saved_count == 0:
        print("no missing Q&A found")
    else:
        print(f"created {saved_count} Q&A callout(s)")


if __name__ == "__main__":
    main()
