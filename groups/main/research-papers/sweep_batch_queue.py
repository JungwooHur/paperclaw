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

Idempotent and cheap: no-ops when the queue is absent, still being worked, already
drained, or a resume is already scheduled/just ran.

  sweep_batch_queue.py [--dry-run] [--stale-min N] [--cooldown-min N]
"""
import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
QUEUE = os.path.join(HERE, "papers_queue.json")
DB = os.path.join(REPO, "store", "messages.db")

# The queue lives at a different path inside the container.
QUEUE_IN_CONTAINER = "/workspace/group/research-papers/papers_queue.json"

RESUME_PROMPT = f"""[배치 자동 재개 — 좌초 감지됨]

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
5. 전부 끝나면 결과 요약을 send_message로 보내고 큐 파일을 삭제한다.

주의
- status가 "failed"인 항목은 건드리지 마라. 이미 Notion 페이지가 있어 dedup에 걸리며
  별도 재처리 대상이다.
- 토큰/시간이 부족해 남은 것을 이번에 못 끝내겠으면, 큐를 pending 상태로 정확히
  남겨두고 사용자에게 몇 편이 남았는지 알려라. 큐만 올바르면 자동 재개가 이어받는다.
"""


def load_queue():
    try:
        with open(QUEUE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"sweep: unreadable queue ({type(e).__name__}: {e})", file=sys.stderr)
        return None


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--stale-min", type=float, default=20.0,
                    help="queue untouched this long counts as stranded (default 20)")
    ap.add_argument("--cooldown-min", type=float, default=30.0,
                    help="don't re-schedule a resume this soon after the last (default 30)")
    a = ap.parse_args()

    q = load_queue()
    if q is None:
        return 0                                    # no batch in flight

    papers = q.get("papers", [])
    pending = [p for p in papers if p.get("status") == "pending"]
    running = [p for p in papers if p.get("status") == "in_progress"]
    if not pending and not running:
        return 0                                    # drained (only done/failed left)

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
            print(f"sweep: stranded ({len(pending)} pending, {len(running)} in_progress) "
                  f"but a resume is already scheduled/recent — skipping")
            return 0

        row = conn.execute(
            "SELECT jid FROM registered_groups WHERE folder = 'main' LIMIT 1").fetchone()
        if not row:
            print("sweep: no 'main' group registered", file=sys.stderr)
            return 1
        chat_jid = row[0]

        print(f"sweep: STRANDED batch — {len(pending)} pending, {len(running)} in_progress, "
              f"queue idle {age_min:.0f}m, no agent container"
              f"{' (dry-run)' if a.dry_run else ''}")
        if a.dry_run:
            return 0

        # Their containers are gone, so in_progress can never complete — hand them back
        # to pending. The subagent's own dedup check keeps already-finished work cheap.
        for p in running:
            p["status"] = "pending"
            p["task_id"] = None
            p["error"] = None
        q["session_processed"] = 0                  # fresh session -> fresh cap
        tmp = QUEUE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(q, f, ensure_ascii=False, indent=2)
        os.replace(tmp, QUEUE)                      # atomic: never a half-written queue

        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        tid = "papers-batch-resume-" + now.strftime("%Y%m%dT%H%M%S")
        conn.execute(
            "INSERT INTO scheduled_tasks (id, group_folder, chat_jid, prompt,"
            " schedule_type, schedule_value, context_mode, next_run, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, "main", chat_jid, RESUME_PROMPT, "once", "", "isolated",
             now.isoformat(), "active", now.isoformat()))
        conn.commit()
        print(f"sweep: reconciled {len(running)} in_progress -> pending, "
              f"scheduled {tid} ({len(pending) + len(running)} papers to resume)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
