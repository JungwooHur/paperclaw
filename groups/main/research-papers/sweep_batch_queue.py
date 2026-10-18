#!/usr/bin/env python3
"""Detect a STRANDED paper batch queue and re-dispatch it. Runs on the host from
paperclaw-qa-heal.service (systemd timer), like the other healers.

Why this exists
---------------
`papers_queue.json` is owned by an agent running INSIDE a container, and the
dispatcher pattern (groups/main/CLAUDE.md) has it fan out background subagents and
then *wait* for their task_notifications. That works in INTERACTIVE mode, where an
incoming WhatsApp message re-invokes the agent. It does NOT work for a SCHEDULED
task: task-scheduler closes the container's stdin, so the agent gets exactly ONE
turn — the moment it finishes its turn to "wait", the container exits and every
in-flight subagent dies with it.

Observed twice, in two different ways:
  * a watchdog restart detached the batch's containers mid-run (fixed separately by
    the liveness heartbeat), and
  * a scheduled resume dispatched 3 subagents, said "now I'll wait for
    task_notification", ended its turn 147s in, and the container exited — leaving
    3 papers frozen `in_progress` and 10 `pending` for TWO DAYS with zero progress.

In both cases the queue is simply abandoned: nothing re-enters the loop, so a batch
stays dead until a human notices. Prose can't fix this — the agent did exactly what
it said it would; the RUNTIME killed it. So recovery has to be structural and live
outside the container, on the same timer as the other healers:

  * Detect  — a queue that still has pending/in_progress work but hasn't been
              touched for --stale-min minutes while NO agent container is running.
  * Repair  — reconcile the dead `in_progress` entries back to `pending` (their
              containers are gone) and schedule a resume task, which the scheduler
              runs in a fresh container.

The resume prompt it writes is the important half: it tells the agent to POLL its
background subagents to completion instead of ending its turn, because ending the
turn is what kills them (see RESUME_PROMPT).

Polling to completion exposed a third limit: one session cannot hold a whole batch.
A scheduled run is capped at 50 turns by the runner, and two resumes each stopped
there after about 10 papers with 3 subagents still in flight. So each resume gets a
session-sized chunk (BATCH_CHUNK). The rest waits in BACKLOG, a host-only file no
agent prompt names, and the sweeper hands out the next chunk once a queue drains:

  * Trim    — a stranded queue keeps its first BATCH_CHUNK pending papers; the rest
              go to the FRONT of the backlog, in order.
  * Refill  — no queue (or a drained one), a non-empty backlog, nothing running:
              the next chunk becomes a fresh queue and a resume is scheduled.

Never lose a paper: both files are written atomically, and a paper is written to
its destination before it is removed from its source. A crash between the two
writes can duplicate a paper, which the subagent's dedup check makes harmless; it
cannot drop one. The queue is the agent's file, so right before any write the sweep
runs `docker ps` again and compares the queue with its first read, and writes
nothing if either changed (see still_quiet): an agent that started during the
seconds-long NotebookLM probe writes the queue too.

Idempotent and cheap. It writes nothing while the queue is still being worked or a
container is up, and never schedules a second resume while one is already scheduled
or ran within the cooldown. It writes in three cases only:

  * a stranded queue, no resume scheduled or recent: reconcile, trim, and schedule
    a resume;
  * a stranded queue with a resume already scheduled or recent: trim only, so the
    run that fires next still gets a session-sized queue;
  * no queue or a drained one, a non-empty backlog, no resume scheduled or recent:
    refill, and schedule a resume.

  sweep_batch_queue.py [--dry-run] [--stale-min N] [--cooldown-min N]
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
QUEUE = os.path.join(HERE, "papers_queue.json")
# Papers waiting for a later session, {"papers": [...]}. Host-only: no agent prompt
# names it, so an agent that deletes the queue at the end of a batch cannot lose it.
BACKLOG = os.path.join(HERE, "papers_backlog.json")
DB = os.path.join(REPO, "store", "messages.db")

# Papers per resume session. A scheduled run is capped at 50 turns by the runner
# (container/agent-runner/src/index.ts), and a paper costs the main agent about 5
# of them (dispatch, record, polls), so both resumes that ran out stopped after
# about 10 papers; 6 leaves room for retries and the summary. A scheduled run also
# emits nothing until its one turn ends, so the whole session must fit inside the
# group's 5 h no-output timeout: at 3 in parallel, 6 papers is two waves, well
# inside it. Raising the turn cap alone would only move the boundary to that kill.
BATCH_CHUNK = 6

# The queue lives at a different path inside the container.
QUEUE_IN_CONTAINER = "/workspace/group/research-papers/papers_queue.json"

RESUME_PROMPT = f"""[배치 자동 재개]

중단된 논문 배치를 이어서 처리한다. 큐: {QUEUE_IN_CONTAINER}
groups/main/CLAUDE.md의 "Paper Processing (Background Subagent Dispatcher)"를 따르되,
아래 **턴 유지 규칙**이 그것보다 우선한다.

🚨 턴 유지 규칙 (이걸 어기면 배치가 또 죽는다)
이 세션은 예약 실행이라 stdin이 닫힌 one-shot 컨테이너다. 네가 턴을 끝내는 순간
컨테이너가 종료되고 **백그라운드 subagent가 전부 같이 죽는다.** 실제로 이전 재개
시도가 subagent 3개를 띄운 뒤 "task_notification을 기다리겠다"며 턴을 끝냈고, 그
대로 배치가 이틀간 멈췄다. 그러니:

- 작업이 하나라도 진행 중이면 **절대 턴을 끝내지 마라.** "기다리겠다"고 말하고
  응답을 마치는 것은 금지다.
- 대신 in_progress인 모든 task에 대해 `TaskOutput(task_id)`를 **반복 호출**하며
  완료될 때까지 능동적으로 폴링해라. (조회 간격이 필요하면 짧게 sleep)
- 하나가 끝나면 결과를 큐에 반영하고 즉시 다음 pending을 dispatch한 뒤 다시 폴링.
- **큐가 완전히 빌 때까지(pending=0 이고 in_progress=0) 이 루프를 유지**한 다음에만
  최종 요약을 보내고 턴을 끝내라.

절차
1. 큐를 읽는다. 새로 논문을 추가(ingest)하지 말고 이미 있는 pending만 처리한다.
2. in_progress가 3개 미만이고 pending이 남아있는 동안: pending 하나를 in_progress로
   바꿔 큐에 저장하고 Task(subagent_type:"general-purpose", run_in_background:true)로
   subagent 1개를 dispatch한 뒤 반환된 task_id를 큐에 기록한다.
3. subagent는 Subagent Prompt Template을 그대로 따른다 — **DEDUP CHECK 먼저**(이미
   Notion에 있으면 재번역 없이 done), NotebookLM 섹션별 전문 번역, 그림/표 주입,
   collect_papers.py --add-paper로 페이지 생성, verify_sections.py exit 0 확인.
4. 위 턴 유지 규칙대로 폴링하며 끝까지 진행한다.
5. 전부 끝나면 결과 요약을 send_message로 보내고 큐 파일을 삭제한다. 큐 파일 삭제는
   그대로 맞다 — 이 큐에는 한 세션 분량만 들어 있고, 나머지 논문은 호스트가 따로
   보관하다가 다음 세션에 자동으로 넘긴다. 큐의 "backlog_remaining" 값이 0보다 크면
   요약 끝에 "남은 N편은 이후 세션에서 자동으로 이어서 처리됩니다"라고 적어라.

🚨 환경성 실패는 논문 실패가 아니다 — 즉시 중단해라
NotebookLM 인증 만료("authentication expired", "run notebooklm login")나 rate limit을
만나면, 그건 그 논문의 문제가 아니라 **환경 문제**라서 남은 논문도 100% 똑같이 실패한다.
이때:
- 해당 논문을 failed로 기록하지 마라. **pending 그대로 두고 배치를 즉시 중단**해라.
  (실제로 인증이 만료된 상태에서 재개가 돌아 5편이 2분 만에 failed로 묻힌 적이 있다.)
- 남은 pending도 dispatch하지 말고, 사용자에게 "NotebookLM 인증 만료 — 호스트에서
  `notebooklm login` 필요"라고 알린 뒤 종료해라. 인증이 복구되면 자동으로 재개된다.

주의
- status가 "failed"인 항목은 건드리지 마라. 이미 Notion 페이지가 있어 dedup에 걸리며
  별도 재처리 대상이다.
- 토큰/시간이 부족해 남은 것을 이번에 못 끝내겠으면, 큐를 pending 상태로 정확히
  남겨두고 사용자에게 몇 편이 남았는지 알려라. 큐만 올바르면 자동 재개가 이어받는다.
"""


# An ENVIRONMENTAL failure — the shared NotebookLM session died or is throttled, so
# EVERY paper fails instantly and identically. Such a paper isn't broken and must not
# be buried as permanently `failed`; it's retryable the moment the environment is fixed.
_ENV_FAIL = re.compile(
    r"auth\w*\s+(expired|invalid)|re-?authenticate|notebooklm login"
    r"|rate.?limit|quota", re.I)


def notebooklm_auth_ok():
    """True/False if we can tell whether the NotebookLM session is alive, None if not.

    Probed ONLY when we're about to schedule a resume (rare), because it drives a real
    browser session and isn't free. An expired session is the difference between a
    resume that works and one that marches through the queue marking every paper
    `failed` in a couple of minutes."""
    try:
        p = subprocess.run(["notebooklm", "list", "--json"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None                                   # can't tell — don't block recovery
    blob = (p.stdout or "") + (p.stderr or "")
    if _ENV_FAIL.search(blob):
        return False
    return True if p.returncode == 0 else None


class Unreadable(Exception):
    """A queue or backlog exists but cannot be parsed. Never write over it: the
    papers in it would be gone."""


def _load_json(path):
    """The parsed file, or None when it does not exist. Raises Unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise Unreadable(f"{os.path.basename(path)}: {type(e).__name__}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("papers", []), list):
        raise Unreadable(f"{os.path.basename(path)}: not a {{\"papers\": [...]}} object")
    return data


def load_queue():
    return _load_json(QUEUE)


def queue_snapshot():
    """The queue's bytes, or None when there is no queue. Raises Unreadable."""
    try:
        with open(QUEUE, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unreadable(f"{os.path.basename(QUEUE)}: {type(e).__name__}: {e}") from e


def still_quiet(snapshot) -> bool:
    """Checked right before writing, against the queue as `main` first read it.

    The queue belongs to the agent. Between the first look and a write, the sweep
    runs the NotebookLM probe (seconds) and waits on the DB (up to 15 s), and a
    paper request landing in that window starts the main container, whose agent
    writes the queue too. Writing over that would drop the agent's paper, or, on a
    refill, the chunk just taken out of the backlog: its only other copy. So stand
    down if a container is up now or the queue changed, and let the next cycle look
    again. `docker ps` goes first because it is the slow one: the queue is read
    last, leaving only the write itself after it."""
    if agent_container_running():
        print("sweep: an agent container started during the sweep — writing nothing")
        return False
    try:
        now = queue_snapshot()
    except Unreadable:
        now = not snapshot          # anything but the snapshot
    if now != snapshot:
        print("sweep: the queue changed during the sweep — writing nothing")
        return False
    return True


def load_backlog():
    data = _load_json(BACKLOG)
    return list(data.get("papers", [])) if data else []


def _write_json(path, data):
    """Atomic: a reader sees the old file or the new one, never half of one."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def paper_key(p):
    """What makes two entries the same paper: the arxiv id, else the url. An entry
    with neither has no key and is never merged into another."""
    aid = str(p.get("arxiv_id") or "").strip().lower()
    if aid:
        return "arxiv:" + re.sub(r"v\d+$", "", aid)
    url = str(p.get("url") or "").strip()
    return "url:" + url if url else None


def push_front(backlog, papers):
    """`papers` ahead of `backlog`, both in order, one entry per paper key."""
    out, seen = [], set()
    for p in list(papers) + list(backlog):
        k = paper_key(p)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(p)
    return out


def split_chunk(papers):
    """(papers kept in the queue, pending papers past the first BATCH_CHUNK)."""
    pending = [p for p in papers if p.get("status") == "pending"]
    excess = pending[BATCH_CHUNK:]
    moved = {id(p) for p in excess}
    return [p for p in papers if id(p) not in moved], excess


def as_pending(p):
    p = dict(p)
    p["status"] = "pending"
    p["task_id"] = None
    p["error"] = None
    return p


def write_trimmed(q, backlog):
    """Write `q` holding at most BATCH_CHUNK pending papers, the rest moved to the
    front of the backlog. Returns (papers moved, backlog length).

    Queue -> backlog, so the backlog is written FIRST: a crash before the queue
    write leaves the moved papers in both files, never in neither."""
    kept, excess = split_chunk(q.get("papers", []))
    if excess:
        backlog = push_front(backlog, excess)
        _write_json(BACKLOG, {"papers": backlog})
    q["papers"] = kept
    q["backlog_remaining"] = len(backlog)
    _write_json(QUEUE, q)
    return len(excess), len(backlog)


def schedule_resume(conn, chat_jid):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    tid = "papers-batch-resume-" + now.strftime("%Y%m%dT%H%M%S")
    conn.execute(
        "INSERT INTO scheduled_tasks (id, group_folder, chat_jid, prompt,"
        " schedule_type, schedule_value, context_mode, next_run, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, "main", chat_jid, RESUME_PROMPT, "once", "", "isolated",
         now.isoformat(), "active", now.isoformat()))
    conn.commit()
    return tid


def main_chat_jid(conn):
    row = conn.execute(
        "SELECT jid FROM registered_groups WHERE folder = 'main' LIMIT 1").fetchone()
    if not row:
        print("sweep: no 'main' group registered", file=sys.stderr)
    return row[0] if row else None


def auth_expired():
    """Don't resume into a broken environment. With a dead NotebookLM session every
    paper fails within seconds, so an unguarded resume doesn't stall — it marches
    through the queue burning each remaining paper into `failed` (observed: 5
    papers buried in ~2 minutes). Better to stay put and say why."""
    if notebooklm_auth_ok() is False:
        print("sweep: NotebookLM session is EXPIRED — refusing to resume (a resume "
              "now would mark every remaining paper failed). Run `notebooklm login` "
              "on the host; the batch resumes automatically after that.",
              file=sys.stderr)
        return True
    return False


def agent_container_running() -> bool:
    """True if any agent container is up — the batch may legitimately be working, so
    we must not reconcile its in_progress entries out from under it."""
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return True          # can't tell -> assume alive, never re-dispatch blindly
    return any(n.startswith("paperclaw-") for n in out.split())


def recent_resume(conn, cooldown_min: int) -> bool:
    """True if a resume task is already queued to run, or ran within the cooldown —
    so a slow-starting batch isn't piled up with duplicate resumes."""
    row = conn.execute(
        "SELECT 1 FROM scheduled_tasks WHERE id LIKE 'papers-batch-resume-%'"
        " AND status = 'active' AND next_run IS NOT NULL LIMIT 1").fetchone()
    if row:
        return True
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(minutes=cooldown_min)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM scheduled_tasks WHERE id LIKE 'papers-batch-resume-%'"
        " AND last_run IS NOT NULL AND last_run > ? LIMIT 1", (cutoff,)).fetchone()
    return bool(row)


def refill(q, backlog, a, snapshot) -> int:
    """No batch in flight: hand the next chunk of the backlog to a fresh session."""
    if not backlog:
        return 0                                    # nothing waiting
    if agent_container_running():
        print(f"sweep: {len(backlog)} papers in the backlog but an agent container "
              f"is up — waiting")
        return 0
    if not os.path.exists(DB):
        print(f"sweep: no DB at {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB, timeout=15)
    try:
        if recent_resume(conn, a.cooldown_min):
            print(f"sweep: {len(backlog)} papers in the backlog but a resume is already "
                  f"scheduled/recent — skipping")
            return 0
        chat_jid = main_chat_jid(conn)
        if chat_jid is None:
            return 1

        chunk, rest = backlog[:BATCH_CHUNK], backlog[BATCH_CHUNK:]
        print(f"sweep: REFILL — next {len(chunk)} of {len(backlog)} backlog papers, "
              f"no batch in flight{' (dry-run)' if a.dry_run else ''}")
        if auth_expired() or a.dry_run or not still_quiet(snapshot):
            return 0

        # A drained queue still holds the last session's results. `done` ones were
        # reported and go. `failed` ones are real failures (an env-failed one would
        # have kept the queue from counting as drained) and stay visible, as does
        # anything in a status this script does not know, rather than be dropped.
        kept = [p for p in (q or {}).get("papers", []) if p.get("status") != "done"]
        _write_json(QUEUE, {
            "papers": kept + [as_pending(p) for p in chunk],
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "session_processed": 0,
            "backlog_remaining": len(rest),
        })
        # Backlog -> queue, so the queue is written FIRST: a crash before this line
        # leaves the chunk in both files, never in neither.
        _write_json(BACKLOG, {"papers": rest})

        tid = schedule_resume(conn, chat_jid)
        print(f"sweep: queued {len(chunk)} papers, {len(rest)} left in the backlog, "
              f"scheduled {tid}")
        return 0
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--stale-min", type=float, default=20.0,
                    help="queue untouched this long counts as stranded (default 20)")
    ap.add_argument("--cooldown-min", type=float, default=30.0,
                    help="don't re-schedule a resume this soon after the last (default 30)")
    a = ap.parse_args(argv)

    try:
        # Taken before the parse, so a write that lands between the two still
        # reads as a change in still_quiet.
        snapshot = queue_snapshot()
        q = load_queue()
        backlog = load_backlog()
    except Unreadable as e:
        print(f"sweep: unreadable {e} — leaving both files alone", file=sys.stderr)
        return 1

    papers = q.get("papers", []) if q else []
    pending = [p for p in papers if p.get("status") == "pending"]
    running = [p for p in papers if p.get("status") == "in_progress"]
    # Papers buried by an environmental failure (dead NotebookLM session, rate limit)
    # are retryable, not broken — count them as work so a queue that a bad session
    # marked "all failed" is still recovered once the environment is healthy again.
    retryable = [p for p in papers
                 if p.get("status") == "failed" and _ENV_FAIL.search(str(p.get("error") or ""))]
    if not pending and not running and not retryable:
        return refill(q, backlog, a, snapshot)      # absent or drained

    age_min = (datetime.datetime.now().timestamp() - os.path.getmtime(QUEUE)) / 60
    if age_min < a.stale_min:
        return 0                                    # someone is actively working it

    if agent_container_running():
        print(f"sweep: queue idle {age_min:.0f}m but an agent container is up — waiting")
        return 0

    if not os.path.exists(DB):
        print(f"sweep: no DB at {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB, timeout=15)
    try:
        if recent_resume(conn, a.cooldown_min):
            # No second resume, but whichever run fires next still gets a
            # session-sized queue.
            over = len(pending) > BATCH_CHUNK
            print(f"sweep: stranded ({len(pending)} pending, {len(running)} in_progress) "
                  f"but a resume is already scheduled/recent — "
                  + (f"no new resume, trimming the queue to {BATCH_CHUNK} pending"
                     if over else "skipping"))
            if over:
                if a.dry_run:
                    print(f"sweep: would move {len(pending) - BATCH_CHUNK} pending papers "
                          f"to the backlog (dry-run)")
                elif still_quiet(snapshot):
                    moved, left = write_trimmed(q, backlog)
                    print(f"sweep: moved {moved} pending papers to the backlog "
                          f"({left} waiting)")
            return 0

        chat_jid = main_chat_jid(conn)
        if chat_jid is None:
            return 1

        print(f"sweep: STRANDED batch — {len(pending)} pending, {len(running)} in_progress, "
              f"{len(retryable)} env-failed, queue idle {age_min:.0f}m, no agent container"
              f"{' (dry-run)' if a.dry_run else ''}")
        if auth_expired() or a.dry_run or not still_quiet(snapshot):
            return 0

        # Their containers are gone, so in_progress can never complete — hand them back
        # to pending. The subagent's own dedup check keeps already-finished work cheap.
        # Same for papers a dead session / rate limit buried: the environment is healthy
        # again (checked just above), so give them another go instead of losing them.
        for p in running + retryable:
            p.update(as_pending(p))
        q["session_processed"] = 0                  # fresh session -> fresh cap
        moved, left = write_trimmed(q, backlog)

        tid = schedule_resume(conn, chat_jid)
        print(f"sweep: reconciled {len(running)} in_progress + {len(retryable)} env-failed "
              f"-> pending, moved {moved} to the backlog ({left} waiting), scheduled {tid}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
