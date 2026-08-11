#!/usr/bin/env python3
"""Single source of truth for "which NotebookLM notebook belongs to this paper".

Why
---
`notebooks.json` is supposed to answer that question, but keeping it up to date was
a prose rule ("save {arxiv_id: notebook_id}") the agent followed only sometimes. The
result compounds silently:

  * a notebook is created and never recorded, so the NEXT question about that paper
    finds nothing, creates ANOTHER notebook, re-uploads the PDF and waits for
    NotebookLM to index it — minutes of latency before the ask can even start, and a
    second notebook for the same paper;
  * every entry is written TWICE, once in each direction ({key: notebook_id} and
    {notebook_id: key}), so the file looks twice as populated as it is — 47 entries
    describing 23 papers. Reading it tolerantly and rewriting it canonically removes
    that illusion, which is what made the real gap (23 registered vs 189 upstream)
    hard to see.

So creation must record itself, and a lookup must tolerate what is already on disk.
`container/bin/notebooklm` routes `create` through `get_or_create` here, which makes
creation idempotent by title and writes the registry — the agent cannot forget,
because it is no longer the agent's job.

  notebook_registry.py --repair                 # canonicalize inverted entries
  notebook_registry.py --backfill               # record notebooks that exist upstream
  notebook_registry.py --dedupe                 # report papers holding >1 notebook
  notebook_registry.py --get-or-create --title T [--key K] [--json]
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "notebooks.json")
LOCK = REGISTRY + ".lock"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ARXIV = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")


def real_cli() -> str:
    """The unwrapped CLI.

    The wrapper holds an flock for the whole command, so anything it calls must NOT
    go back through the wrapper — a second flock on the same file would block
    forever. Inside the container the real binary is moved aside by the Dockerfile;
    on the host `notebooklm` already IS the real one.
    """
    return (os.environ.get("NOTEBOOKLM_REAL")
            or ("/usr/local/lib/notebooklm-real"
                if os.path.exists("/usr/local/lib/notebooklm-real") else "notebooklm"))


def _norm_title(title: str) -> str:
    """Comparison form for a notebook title — punctuation and case carry no meaning."""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def key_from_title(title: str) -> str:
    """The registry key a notebook title implies.

    Prefer an arxiv id wherever it appears (`Paper: Some Title (1234.56789)`), since
    that is what a lookup has in hand; otherwise fall back to the title itself with
    the `Paper: ` prefix removed.
    """
    title = (title or "").strip()
    m = _ARXIV.search(title)
    if m:
        return m.group(1)
    return re.sub(r"^paper:\s*", "", title, flags=re.I).strip() or title


def load() -> dict:
    """The registry as `{key: notebook_id}`, whichever direction it is stored in.

    Reading tolerantly is what makes the 24 inverted entries usable again without a
    migration step — `--repair` then rewrites them so the file matches what is read.
    """
    try:
        with open(REGISTRY) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if not isinstance(v, str):
            continue
        if _UUID.match(v):
            out[k] = v
        elif _UUID.match(k):
            out[v] = k                      # stored backwards — flip it on read
    return out


def record(key: str, notebook_id: str) -> None:
    """Add one mapping, atomically and under a lock.

    Parallel paper subagents each create notebooks, so a plain read-modify-write
    would lose entries. The lock covers the whole cycle and the rename is atomic, so
    a crash mid-write cannot leave a truncated registry.
    """
    if not key or not notebook_id:
        return
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load()
        if current.get(key) == notebook_id:
            return
        current[key] = notebook_id
        fd, tmp = tempfile.mkstemp(dir=HERE, prefix=".notebooks.", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, REGISTRY)


def list_notebooks() -> list:
    """Every notebook on the account, or [] if the CLI cannot answer."""
    try:
        out = subprocess.run([real_cli(), "list", "--json"], capture_output=True,
                             text=True, timeout=300).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if isinstance(data, dict):
        data = data.get("notebooks", [])
    return [n for n in data if isinstance(n, dict) and n.get("id")]


def resolve(key: str, title: str = "", notebooks: list = None):
    """Find an existing notebook for this paper, recording it if it was unrecorded.

    Registry first (no network). Then the account itself, because a notebook created
    before this script existed — or by a run that forgot to record — is still
    perfectly usable and re-creating it is the expensive mistake.
    """
    reg = load()
    if key and reg.get(key):
        return reg[key]
    if title and reg.get(key_from_title(title)):
        return reg[key_from_title(title)]

    if notebooks is None:
        notebooks = list_notebooks()
    want_title = _norm_title(title)
    for nb in notebooks:
        nb_title = nb.get("title") or ""
        hit = ((key and key_from_title(nb_title) == key)
               or (want_title and _norm_title(nb_title) == want_title))
        if hit:
            record(key or key_from_title(nb_title), nb["id"])
            return nb["id"]
    return None


def create(title: str) -> dict:
    """Create a notebook through the real CLI and return its payload."""
    out = subprocess.run([real_cli(), "create", title, "--json"],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "notebooklm create failed")
    data = json.loads(out.stdout)
    nb = data.get("notebook") or data
    if not nb.get("id"):
        raise RuntimeError(f"create returned no id: {out.stdout[:200]}")
    return nb


def get_or_create(title: str, key: str = "") -> tuple:
    """(notebook, created) for this paper. Idempotent by key, then by title."""
    key = key or key_from_title(title)
    existing = resolve(key, title)
    if existing:
        return {"id": existing, "title": title}, False
    nb = create(title)
    record(key, nb["id"])
    return nb, True


def repair() -> dict:
    """Rewrite the registry in the canonical direction, dropping nothing."""
    canonical = load()
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fd, tmp = tempfile.mkstemp(dir=HERE, prefix=".notebooks.", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(canonical, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, REGISTRY)
    return {"entries": len(canonical)}


def backfill() -> dict:
    """Record every notebook that exists upstream but is missing from the registry."""
    notebooks = list_notebooks()
    before = load()
    added = 0
    for nb in notebooks:
        title = nb.get("title") or ""
        # Only titles that actually identify a paper. A notebook called something
        # arbitrary would be registered under its own title, which no lookup ever
        # asks for — it would only pad the file. `resolve` still finds those by
        # matching the title upstream.
        if not (_ARXIV.search(title) or title.lower().startswith("paper:")):
            continue
        key = key_from_title(title)
        if not key or before.get(key) == nb["id"]:
            continue
        if key in before:
            continue                        # first one wins; --dedupe reports the rest
        record(key, nb["id"])
        before[key] = nb["id"]
        added += 1
    return {"upstream": len(notebooks), "added": added, "entries": len(load())}


def dedupe() -> dict:
    """Report papers holding more than one notebook.

    Reporting only, on purpose: deleting a notebook is irreversible and the extra one
    may be the one with the uploaded source. Pointing the registry at a single
    notebook already stops the duplication from growing.
    """
    groups = {}
    for nb in list_notebooks():
        groups.setdefault(key_from_title(nb.get("title") or ""), []).append(nb)
    dups = {k: v for k, v in groups.items() if len(v) > 1 and k}
    return {"papers_with_duplicates": len(dups),
            "extra_notebooks": sum(len(v) - 1 for v in dups.values()),
            "keys": sorted(dups)[:20]}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repair", action="store_true")
    g.add_argument("--backfill", action="store_true")
    g.add_argument("--dedupe", action="store_true")
    g.add_argument("--get-or-create", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--title", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.get_or_create:
        if not a.title:
            print("--title is required", file=sys.stderr)
            return 2
        nb, created = get_or_create(a.title, a.key)
        if a.json:
            # Same shape the real CLI prints, so callers parse one contract.
            print(json.dumps({"notebook": nb, "reused": not created}))
        else:
            verb = "Created notebook:" if created else "Reusing notebook:"
            print(f"{verb} {nb['id']} - {nb.get('title', a.title)}")
        return 0

    if a.repair:
        print(json.dumps(repair()))
    elif a.backfill:
        print(json.dumps(backfill()))
    elif a.dedupe:
        print(json.dumps(dedupe(), ensure_ascii=False))
    else:
        reg = load()
        print(json.dumps({"entries": len(reg), "upstream": len(list_notebooks())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
