"""A paper batch is handed to scheduled sessions one session-sized chunk at a time.

A scheduled run is capped at 50 turns, and two resumes each ran out after about
10 papers with subagents still in flight. The sweeper keeps at most BATCH_CHUNK
papers in the queue a session sees, and no more than NotebookLM's live budget pays
for, and the rest in a host-only backlog, and refills the queue from it once nothing
is running. Whatever it does, a paper must never be
dropped: a crash may leave one in both files, never in neither.
"""
import datetime
import json
import os
import re
import sqlite3
import subprocess
import time

import pytest

import sweep_batch_queue as sbq

UTC = datetime.timezone.utc

# `notebooklm usage --json` on the host (CLI 0.8.6), as a Pro account printed it,
# with all but two of its actions dropped and its percentages and reset times
# made up.
LIVE_USAGE = """{
  "status": "ready",
  "enabled": true,
  "available": true,
  "is_exhausted": false,
  "active_window": "five_hour",
  "windows": [
    {
      "kind": "five_hour",
      "used_percent": 55.0,
      "remaining_percent": 45.0,
      "resets_at": "2026-01-01T05:00:00+00:00"
    },
    {
      "kind": "weekly",
      "used_percent": 2.5,
      "remaining_percent": 97.5,
      "resets_at": "2026-01-07T05:00:00+00:00"
    }
  ],
  "actions": [
    {
      "code": 1,
      "kind": "audio_overview",
      "has_sufficient_quota": true,
      "cost_tier": "high",
      "remaining_deferred_artifact_generations": 6,
      "estimated_cost_percent": 9.8
    },
    {
      "code": 9,
      "kind": "flashcards",
      "has_sufficient_quota": true,
      "cost_tier": "low",
      "remaining_deferred_artifact_generations": 6,
      "estimated_cost_percent": 0.4666667
    }
  ]
}"""
FIVE_HOUR_RESETS = "2026-01-01T05:00:00+00:00"


def usage(five_hour_left=100.0, weekly_left=100.0, exhausted=False,
          five_hour_resets=FIVE_HOUR_RESETS):
    """The live meter's JSON with its numbers replaced. The defaults are an
    untouched budget."""
    u = json.loads(LIVE_USAGE)
    u["is_exhausted"] = exhausted
    for w, left in zip(u["windows"], (five_hour_left, weekly_left)):
        w["remaining_percent"] = left
        w["used_percent"] = 100.0 - left
    u["windows"][0]["resets_at"] = five_hour_resets
    return u


def covering(papers):
    """A meter whose five-hour window pays for `papers` and half of one more, and
    whose weekly window is untouched."""
    return usage(five_hour_left=(papers + 0.5) * sbq.PAPER_FIVE_HOUR_PERCENT)


# The papers a resume is handed while the budget is untouched.
CHUNK = min(sbq.BATCH_CHUNK,
            sbq.papers_affordable(sbq.parse_usage(json.dumps(usage()))))

# Copied from src/db.ts, including the context_mode migration.
SCHEMA = """
CREATE TABLE scheduled_tasks (
  id TEXT PRIMARY KEY,
  group_folder TEXT NOT NULL,
  chat_jid TEXT NOT NULL,
  prompt TEXT NOT NULL,
  schedule_type TEXT NOT NULL,
  schedule_value TEXT NOT NULL,
  next_run TEXT,
  last_run TEXT,
  last_result TEXT,
  status TEXT DEFAULT 'active',
  created_at TEXT NOT NULL,
  context_mode TEXT DEFAULT 'isolated'
);
CREATE TABLE registered_groups (
  jid TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  folder TEXT NOT NULL UNIQUE,
  trigger_pattern TEXT NOT NULL,
  added_at TEXT NOT NULL,
  container_config TEXT,
  requires_trigger INTEGER DEFAULT 1
);
"""


def paper(i, status="pending", **extra):
    p = {"id": f"p{i}", "title": f"Paper {i}", "arxiv_id": f"paper-{i}",
         "url": f"https://example.org/paper/{i}", "status": status,
         "task_id": None, "notion_page_id": None, "error": None}
    if status == "in_progress":
        p["task_id"] = f"task-{i}"
    p.update(extra)
    return p


def ids(papers):
    return [p["id"] for p in papers]


class Env:
    """A queue, a backlog, the backoff state and a DB in a temp dir, with nothing
    running and a clock that moves only when a test moves it."""

    def __init__(self, tmp_path, monkeypatch):
        self.queue_path = str(tmp_path / "papers_queue.json")
        self.backlog_path = str(tmp_path / "papers_backlog.json")
        self.state_path = str(tmp_path / "papers_sweep_state.json")
        self.db = str(tmp_path / "messages.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO registered_groups (jid, name, folder, trigger_pattern,"
            " added_at) VALUES ('main@g.us', 'Main', 'main', '@Andy',"
            " '2026-10-01T00:00:00Z')")
        conn.commit()
        conn.close()
        self.container = False
        self.auth = True
        self.usage = usage()
        self.probes = 0
        self.meter_reads = 0
        self.clock = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        monkeypatch.setattr(sbq, "QUEUE", self.queue_path)
        monkeypatch.setattr(sbq, "BACKLOG", self.backlog_path)
        monkeypatch.setattr(sbq, "SWEEP_STATE", self.state_path)
        monkeypatch.setattr(sbq, "DB", self.db)
        monkeypatch.setattr(sbq, "_now", lambda: self.clock)
        monkeypatch.setattr(sbq, "agent_container_running", lambda: self.container)
        monkeypatch.setattr(sbq, "notebooklm_auth_ok", self._probe)
        monkeypatch.setattr(sbq.subprocess, "run", self._run)

    def _probe(self):
        self.probes += 1
        return self.auth

    def _run(self, args, **kwargs):
        """The host CLI's `usage --json`, answering with self.usage: a dict is
        printed as the meter's JSON, a CompletedProcess is returned as it is, and
        an exception is raised."""
        assert list(args) == ["notebooklm", "usage", "--json"], args
        self.probes += 1
        self.meter_reads += 1
        if isinstance(self.usage, BaseException):
            raise self.usage
        if isinstance(self.usage, subprocess.CompletedProcess):
            return self.usage
        return subprocess.CompletedProcess(args, 0, json.dumps(self.usage), "")

    def advance(self, minutes):
        self.clock += datetime.timedelta(minutes=minutes)

    def write_queue(self, papers, idle_min=60, created_at="2026-10-01T00:00:00Z"):
        q = {"papers": papers, "session_processed": 3}
        if created_at is not None:
            q["created_at"] = created_at
        sbq._write_json(self.queue_path, q)
        then = time.time() - idle_min * 60
        os.utime(self.queue_path, (then, then))

    def write_backlog(self, papers):
        sbq._write_json(self.backlog_path, {"papers": papers})

    def queue(self):
        return sbq._load_json(self.queue_path)

    def backlog(self):
        return sbq.load_backlog()

    def files(self):
        out = {}
        for path in (self.queue_path, self.backlog_path, self.state_path):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    out[path] = f.read()
        return out

    def sql(self, query, args=()):
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(query, args).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def resumes(self):
        return [r[0] for r in self.sql(
            "SELECT id FROM scheduled_tasks WHERE id LIKE 'papers-batch-resume-%'")]

    def schedule_resume_ahead(self):
        self.sql(
            "INSERT INTO scheduled_tasks (id, group_folder, chat_jid, prompt,"
            " schedule_type, schedule_value, next_run, status, created_at)"
            " VALUES ('papers-batch-resume-earlier', 'main', 'main@g.us', 'resume',"
            " 'once', '', '2099-01-01T00:00:00Z', 'active', '2026-10-01T00:00:00Z')")

    def finish_session(self):
        """What a session that gets through its queue leaves behind: every pending
        paper done, the queue deleted, and the resume long past its cooldown."""
        done = [p["id"] for p in self.queue()["papers"] if p["status"] == "pending"]
        os.remove(self.queue_path)
        self.sql("DELETE FROM scheduled_tasks")
        return done

    def unfinished(self):
        """Every paper still owed a run, from both files."""
        q = self.queue() or {"papers": []}
        return {p["id"] for p in q["papers"] + self.backlog()
                if p["status"] != "done"}

    def sweep(self, *args):
        return sbq.main(list(args))


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_a_stranded_resume_keeps_one_chunk_and_backlogs_the_rest(env):
    env.write_queue([paper(0, "in_progress"), paper(1, "in_progress")]
                    + [paper(i) for i in range(2, 10)])

    assert env.sweep() == 0

    q = env.queue()
    assert ids(q["papers"]) == [f"p{i}" for i in range(CHUNK)]
    assert all(p["status"] == "pending" and p["task_id"] is None
               for p in q["papers"])
    assert ids(env.backlog()) == [f"p{i}" for i in range(CHUNK, 10)]
    assert q["backlog_remaining"] == 10 - CHUNK
    assert q["session_processed"] == 0
    assert len(env.resumes()) == 1


def test_the_next_chunk_is_queued_once_the_queue_is_gone(env):
    env.write_backlog([paper(i) for i in range(8)])

    assert env.sweep() == 0

    q = env.queue()
    assert ids(q["papers"]) == [f"p{i}" for i in range(CHUNK)]
    assert all(p["status"] == "pending" for p in q["papers"])
    assert ids(env.backlog()) == [f"p{i}" for i in range(CHUNK, 8)]
    assert q["backlog_remaining"] == 8 - CHUNK
    assert len(env.resumes()) == 1


def test_a_drained_queue_keeps_its_failures_and_drops_what_is_done(env):
    env.write_queue([paper(0, "done", notion_page_id="page-0"),
                     paper(1, "failed", error="no PDF found")])
    env.write_backlog([paper(i) for i in range(2, 5)])

    assert env.sweep() == 0

    papers = env.queue()["papers"]
    assert ids(papers) == ["p1", "p2", "p3", "p4"]
    assert papers[0]["status"] == "failed"
    assert papers[0]["error"] == "no PDF found"
    assert env.backlog() == []
    assert env.queue()["backlog_remaining"] == 0


def stranded_with_excess(env):
    env.write_queue([paper(i) for i in range(10)])


def backlog_only(env):
    env.write_backlog([paper(i) for i in range(10)])


def scheduled_with_excess(env):
    env.write_queue([paper(i) for i in range(10)])
    env.schedule_resume_ahead()


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_nothing_moves_while_a_container_is_up(env, setup):
    setup(env)
    env.container = True
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == []
    assert env.probes == 0, "the browser is never driven while a container is up"


def test_a_refill_waits_for_a_resume_already_scheduled(env):
    backlog_only(env)
    env.schedule_resume_ahead()
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == ["papers-batch-resume-earlier"]


@pytest.mark.parametrize("setup", [backlog_only, stranded_with_excess])
def test_an_expired_notebooklm_session_holds_the_batch_unmetered(env, setup):
    setup(env)
    env.auth = False
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == []
    assert env.meter_reads == 0, "the budget is read only for a live session"


def test_an_already_scheduled_resume_still_gets_a_session_sized_queue(env):
    scheduled_with_excess(env)
    limit = sbq.BATCH_CHUNK

    assert env.sweep() == 0

    assert ids(env.queue()["papers"]) == [f"p{i}" for i in range(limit)]
    assert ids(env.backlog()) == [f"p{i}" for i in range(limit, 10)]
    assert env.queue()["backlog_remaining"] == 10 - limit
    assert env.resumes() == ["papers-batch-resume-earlier"]


def test_moved_papers_go_to_the_front_of_the_backlog_once(env):
    # p8 is already waiting under another entry id, and p9, which has no arxiv
    # id, is matched by its url.
    env.write_backlog([paper(20), paper(8, id="p8-again"),
                       paper(9, arxiv_id=None, id="p9-again")])
    env.write_queue([paper(i) for i in range(9)] + [paper(9, arxiv_id=None)])

    assert env.sweep() == 0

    assert ids(env.backlog()) == [f"p{i}" for i in range(CHUNK, 10)] + ["p20"]


def test_order_is_kept_from_the_first_session_to_the_last(env):
    env.write_backlog([paper(20), paper(21)])
    env.write_queue([paper(0, "in_progress")] + [paper(i) for i in range(1, 15)])

    processed = []
    for _ in range(10):
        assert env.sweep() == 0
        if env.queue() is None:
            break
        assert len(env.queue()["papers"]) <= CHUNK
        processed += env.finish_session()

    assert processed == [f"p{i}" for i in range(15)] + ["p20", "p21"]
    assert env.backlog() == []
    assert env.resumes() == []


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only,
                                   scheduled_with_excess])
def test_no_paper_is_lost_by_a_sweep(env, setup):
    setup(env)
    before = env.unfinished()

    assert env.sweep() == 0

    assert env.unfinished() == before


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_a_crash_between_the_two_writes_loses_no_paper(env, setup, monkeypatch):
    setup(env)
    before = env.unfinished()
    real = sbq._write_json
    written = []

    def crash_on_second_write(path, data):
        if written:
            raise OSError("disk full")
        written.append(path)
        real(path, data)

    monkeypatch.setattr(sbq, "_write_json", crash_on_second_write)
    with pytest.raises(OSError):
        env.sweep()

    assert written, "the first write should have gone through"
    assert env.unfinished() >= before


def lands_during(env, monkeypatch, name, container_up):
    """Run the sweep's slow step `name` with a paper request landing inside it:
    the main container starts and its agent adds a paper to the queue. The
    container may already be gone again by the time the sweep looks."""
    real = getattr(sbq, name)

    def slow_step(*args):
        result = real(*args)
        env.container = container_up
        q = env.queue() or {"papers": []}
        env.write_queue(q["papers"] + [paper(99)], idle_min=0)
        return result

    monkeypatch.setattr(sbq, name, slow_step)


# The NotebookLM probes take seconds; the DB can be locked for up to 15 s.
@pytest.mark.parametrize("setup,slow_step", [
    (backlog_only, "notebooklm_auth_ok"),
    (stranded_with_excess, "notebooklm_auth_ok"),
    (backlog_only, "notebooklm_budget"),
    (stranded_with_excess, "notebooklm_budget"),
    (scheduled_with_excess, "recent_resume"),
])
@pytest.mark.parametrize("container_up", [True, False],
                         ids=["container-up", "container-gone"])
def test_a_queue_the_agent_writes_mid_sweep_is_never_written_over(
        env, monkeypatch, setup, slow_step, container_up):
    setup(env)
    owed = env.unfinished() | {"p99"}
    tasks = env.resumes()
    lands_during(env, monkeypatch, slow_step, container_up)

    assert env.sweep() == 0

    assert env.unfinished() == owed
    assert "p99" in ids(env.queue()["papers"])
    assert env.resumes() == tasks


def test_a_refill_stands_down_for_an_agent_that_started_mid_probe(
        env, monkeypatch):
    # The agent reads "no queue" while the probe runs and writes its own queue
    # once the sweep is over, so whatever the sweep put there is overwritten.
    backlog_only(env)
    owed = env.unfinished() | {"p99"}
    seen = {}

    def probe():
        env.container = True
        seen["queue"] = env.queue()
        return True

    monkeypatch.setattr(sbq, "notebooklm_auth_ok", probe)

    assert env.sweep() == 0
    env.write_queue((seen["queue"] or {"papers": []})["papers"] + [paper(99)],
                    idle_min=0)

    assert env.unfinished() == owed
    assert env.resumes() == []


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only,
                                   scheduled_with_excess])
def test_a_sweep_that_writes_never_reports_that_it_skipped(env, setup, capsys):
    setup(env)
    before = env.files()

    assert env.sweep() == 0

    assert env.files() != before
    assert "skipping" not in capsys.readouterr().out


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only,
                                   scheduled_with_excess])
def test_a_dry_run_writes_nothing(env, setup):
    setup(env)
    before_files, before_tasks = env.files(), env.resumes()

    assert env.sweep("--dry-run") == 0

    assert env.files() == before_files
    assert env.resumes() == before_tasks


# What json.load rejects without a JSONDecodeError: an integer past Python's
# digit limit is a plain ValueError, and deep nesting a RecursionError.
TOO_MANY_DIGITS = '{"papers": [], "n": ' + "1" * 5000 + "}"
TOO_DEEP = '{"papers": ' + "[" * 100000


@pytest.mark.parametrize("text", ['{"papers": [', TOO_MANY_DIGITS, TOO_DEEP],
                         ids=["truncated", "too-many-digits", "too-deep"])
@pytest.mark.parametrize("broken", ["queue", "backlog"])
def test_an_unreadable_file_is_never_written_over(env, broken, text):
    env.write_queue([paper(i) for i in range(10)])
    env.write_backlog([paper(20)])
    path = env.queue_path if broken == "queue" else env.backlog_path
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    before = env.files()

    assert env.sweep() == 1

    assert env.files() == before
    assert env.resumes() == []


# A resume that settles no paper backs off.
#
# Measured: NotebookLM throttled its chat while `notebooklm list`, the auth probe,
# still worked, so every resume failed within minutes, left its papers pending as
# the prompt says, and messaged the owner. Without a backoff the sweeper would
# schedule the next one a cooldown later, all night long.

COOLDOWN = 30


def js_time(t):
    """A stamp in the form the scheduler writes last_run (Date.toISOString)."""
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def resume_runs(env, finished=0, minutes=4, failed=0,
                error="verify_sections: MISSING 3 sections"):
    """The resume the sweep just scheduled runs now and ends `minutes` later,
    having finished `finished` pending papers and marked the next `failed` of them
    failed with `error`. The rest stays pending, which is what the agent does on a
    throttle, and its last queue write ends the run."""
    active = env.sql("SELECT id FROM scheduled_tasks WHERE id LIKE"
                     " 'papers-batch-resume-%' AND status = 'active'")
    assert len(active) == 1, "the sweep should have scheduled one resume"
    env.advance(minutes)
    env.sql("UPDATE scheduled_tasks SET status = 'completed', next_run = NULL,"
            " last_run = ? WHERE id = ?", (js_time(env.clock), active[0][0]))
    q = env.queue()
    pending = [p for p in q["papers"] if p["status"] == "pending"]
    for p in pending[:finished]:
        p["status"] = "done"
        p["notion_page_id"] = "page-" + p["id"]
    for p in pending[finished:finished + failed]:
        p["status"] = "failed"
        p["error"] = error
    sbq._write_json(env.queue_path, q)
    os.utime(env.queue_path, (env.clock.timestamp(),) * 2)


def next_resume_after(env, capsys, minutes):
    """Scheduling waits `minutes` after the last resume ended, and no longer.
    Returns what the sweep printed while it waited."""
    resumes = len(env.resumes())
    env.advance(minutes - 1)
    before = env.files()
    probes = env.probes

    assert env.sweep() == 0

    waiting = capsys.readouterr().out
    assert len(env.resumes()) == resumes, f"resumed before {minutes}m"
    assert env.files() == before, "a sweep that backs off writes nothing"
    assert env.probes == probes, "a sweep that backs off never drives the browser"
    env.advance(2)

    assert env.sweep() == 0

    capsys.readouterr()
    assert len(env.resumes()) == resumes + 1, f"no resume after {minutes}m"
    return waiting


def first_resume(env, papers=4, **queue):
    env.write_queue([paper(i) for i in range(papers)], **queue)
    assert env.sweep() == 0
    assert len(env.resumes()) == 1


def test_two_resumes_that_finish_nothing_wait_60_then_120_minutes(env, capsys):
    first_resume(env)

    resume_runs(env)
    out = next_resume_after(env, capsys, 2 * COOLDOWN)
    # The line names n and the time left: one minute of the hour.
    assert re.search(r"backing off\b.*\bn=1\b.*\b1m\b", out)

    resume_runs(env)
    out = next_resume_after(env, capsys, 4 * COOLDOWN)
    assert re.search(r"backing off\b.*\bn=2\b.*\b1m\b", out)


def test_the_normal_cooldown_still_comes_first(env, capsys):
    first_resume(env)
    resume_runs(env)
    env.advance(COOLDOWN - 1)

    assert env.sweep() == 0

    assert len(env.resumes()) == 1
    assert "backing off" not in capsys.readouterr().out


def test_a_resume_that_finishes_a_paper_resets_the_count(env, capsys):
    first_resume(env)
    resume_runs(env)
    next_resume_after(env, capsys, 2 * COOLDOWN)
    resume_runs(env)
    next_resume_after(env, capsys, 4 * COOLDOWN)

    resume_runs(env, finished=1)
    env.advance(COOLDOWN + 1)
    assert env.sweep() == 0
    assert len(env.resumes()) == 4, "progress should cost only the cooldown"

    # And the count starts over: the next resume that gets nowhere is n=1 again.
    resume_runs(env)
    next_resume_after(env, capsys, 2 * COOLDOWN)


def test_papers_that_fail_for_reasons_of_their_own_are_progress(env, capsys):
    # A page verify_sections refuses is settled: no session will run that paper
    # again, so the batch moved even though nothing was done.
    first_resume(env, papers=6)
    resume_runs(env, failed=3)
    env.advance(COOLDOWN + 1)

    assert env.sweep() == 0

    assert len(env.resumes()) == 2, "settling papers should cost only the cooldown"
    assert "backing off" not in capsys.readouterr().out


def test_papers_the_environment_failed_are_no_progress(env, capsys):
    # A throttled session can mark its papers failed instead of leaving them
    # pending. They are retried, so nothing settled.
    first_resume(env, papers=6)
    resume_runs(env, failed=3, error="NotebookLM rate limit exceeded")

    out = next_resume_after(env, capsys, 2 * COOLDOWN)

    assert re.search(r"backing off\b.*\bn=1\b", out)


def test_the_wait_doubles_up_to_twelve_hours_and_stays_there(env, capsys):
    first_resume(env)
    for wait in (60, 120, 240, 480, 12 * 60, 12 * 60, 12 * 60):
        resume_runs(env)
        next_resume_after(env, capsys, wait)


@pytest.mark.parametrize("created_at,new_created_at,new_first", [
    ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", 0),
    (None, None, 10),
], ids=["new-created_at", "no-created_at-new-papers"])
def test_a_new_batch_counts_from_zero(env, capsys, created_at, new_created_at,
                                      new_first):
    first_resume(env, created_at=created_at)
    resume_runs(env)
    env.write_queue([paper(i) for i in range(new_first, new_first + 4)],
                    created_at=new_created_at)
    env.advance(COOLDOWN + 1)

    assert env.sweep() == 0

    assert len(env.resumes()) == 2
    assert "backing off" not in capsys.readouterr().out


def test_a_queue_without_created_at_is_recognised_by_its_papers(env, capsys):
    first_resume(env, created_at=None)
    resume_runs(env)

    next_resume_after(env, capsys, 2 * COOLDOWN)


def test_a_drained_queue_and_backlog_reset_the_count(env, capsys):
    first_resume(env)
    resume_runs(env)
    env.write_queue([paper(i, "done") for i in range(4)])

    assert env.sweep() == 0

    # The same created_at again, so only the reset can make this n=0.
    env.write_queue([paper(i) for i in range(4)])
    env.advance(COOLDOWN + 1)
    assert env.sweep() == 0
    assert len(env.resumes()) == 2
    assert "backing off" not in capsys.readouterr().out


def test_the_refill_path_records_the_batch_it_hands_out(env, capsys):
    backlog_only(env)
    assert env.sweep() == 0
    assert len(env.resumes()) == 1

    resume_runs(env)

    next_resume_after(env, capsys, 2 * COOLDOWN)


def test_with_its_task_row_gone_the_wait_runs_from_when_it_was_scheduled(
        env, capsys):
    first_resume(env)
    resume_runs(env)
    env.sql("DELETE FROM scheduled_tasks")

    # Four of the 60 minutes went by during the run.
    next_resume_after(env, capsys, 2 * COOLDOWN - 4)


def stranded_past_its_wait(env, capsys):
    first_resume(env)
    resume_runs(env)
    env.advance(2 * COOLDOWN + 1)
    capsys.readouterr()


def drained(env, capsys):
    first_resume(env)
    resume_runs(env, finished=4)
    capsys.readouterr()


@pytest.mark.parametrize("setup", [stranded_past_its_wait, drained])
def test_a_dry_run_leaves_the_backoff_state_alone(env, setup, capsys):
    setup(env, capsys)
    with open(env.state_path, "rb") as f:
        state = f.read()
    tasks = env.resumes()

    assert env.sweep("--dry-run") == 0

    with open(env.state_path, "rb") as f:
        assert f.read() == state
    assert env.resumes() == tasks


def missing(path):
    os.remove(path)


def write(text):
    def corrupt(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    corrupt.__name__ = "corrupt"
    return corrupt


@pytest.mark.parametrize("damage", [
    missing,
    write('{"batch": '),
    write("\xff not json"),
    write("[]"),
    write('{"batch": 7, "settled_at_resume": 0, "resumes_without_progress": 3}'),
    write('{"batch": "created_at:2026-10-01T00:00:00Z", "settled_at_resume": 0,'
          ' "resumes_without_progress": -1}'),
    write('{"batch": "created_at:2026-10-01T00:00:00Z", "settled_at_resume": 0,'
          ' "resumes_without_progress": true}'),
    write('{"batch": "created_at:2026-10-01T00:00:00Z", "settled_at_resume": "0",'
          ' "resumes_without_progress": 3}'),
    write('{"batch": "created_at:2026-10-01T00:00:00Z", "settled_at_resume": '
          + "1" * 5000 + ', "resumes_without_progress": 3}'),
    write("[" * 100000),
], ids=["missing", "truncated", "not-json", "a-list", "batch-not-text",
        "negative-count", "bool-count", "text-count", "too-many-digits",
        "too-deep"])
def test_a_missing_or_broken_state_counts_from_zero(env, damage, capsys):
    first_resume(env)
    resume_runs(env)
    damage(env.state_path)
    env.advance(COOLDOWN + 1)
    capsys.readouterr()

    assert env.sweep() == 0

    assert len(env.resumes()) == 2
    assert "n=0" in capsys.readouterr().out


def test_a_wait_with_nothing_to_count_from_never_blocks_a_resume(env):
    first_resume(env)
    resume_runs(env)
    env.sql("DELETE FROM scheduled_tasks")
    with open(env.state_path, encoding="utf-8") as f:
        state = json.load(f)
    state["resumed_at"] = "not a time"
    with open(env.state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

    # Stale again, and well inside the hour an anchor would have asked for.
    env.advance(21)
    assert env.sweep() == 0

    assert len(env.resumes()) == 1


def no_room_for_state(env, monkeypatch):
    real = sbq._write_json

    def write(path, data):
        if path == env.state_path:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(sbq, "_write_json", write)


def test_a_state_that_cannot_be_written_never_costs_the_resume(
        env, monkeypatch, capsys):
    env.write_queue([paper(i) for i in range(4)])
    no_room_for_state(env, monkeypatch)

    assert env.sweep() == 0

    assert len(env.resumes()) == 1
    assert not os.path.exists(env.state_path)
    # Nothing was recorded before, so the next check does count from zero.
    assert "n=0" in capsys.readouterr().err


def test_a_failed_state_write_says_where_the_count_stands(
        env, monkeypatch, capsys):
    # The state the last resume wrote stays on disk, so the next check counts
    # from it: the wait stops growing rather than starting over.
    first_resume(env)
    resume_runs(env)
    next_resume_after(env, capsys, 2 * COOLDOWN)
    resume_runs(env)
    next_resume_after(env, capsys, 4 * COOLDOWN)     # n=2 recorded
    no_room_for_state(env, monkeypatch)
    resume_runs(env)
    env.advance(8 * COOLDOWN + 1)

    assert env.sweep() == 0

    assert len(env.resumes()) == 4
    err = capsys.readouterr().err
    assert re.search(r"\bn=3\b", err) and "n=0" not in err, err
    # And the next check does what the line said.
    resume_runs(env)
    out = next_resume_after(env, capsys, 8 * COOLDOWN)
    assert re.search(r"backing off\b.*\bn=3\b", out)


# NotebookLM's live budget sizes each resume.
#
# Measured: a session started 6 papers with too little budget left, 3 of them
# stopped half-translated, and the budget they had used was lost. So a resume is
# handed only the papers the meter still pays for, and none when it pays for none.

@pytest.fixture
def seoul():
    """Local time is UTC+9 while the test runs."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "KST-9"
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_the_live_meter_reads_as_its_two_windows():
    budget = sbq.parse_usage(LIVE_USAGE)

    assert budget.exhausted is False
    assert budget.five_hour.left == pytest.approx(45.0)
    assert budget.five_hour.used == pytest.approx(55.0)
    assert budget.five_hour.resets == datetime.datetime(2026, 1, 1, 5, tzinfo=UTC)
    assert budget.weekly.left == pytest.approx(97.5)
    assert budget.weekly.resets == datetime.datetime(2026, 1, 7, 5, tzinfo=UTC)


def test_a_refill_queues_only_what_the_budget_covers(env, capsys):
    env.write_backlog([paper(i) for i in range(4)])
    env.usage = covering(2)

    assert env.sweep() == 0

    assert ids(env.queue()["papers"]) == ["p0", "p1"]
    assert ids(env.backlog()) == ["p2", "p3"]
    assert env.queue()["backlog_remaining"] == 2
    assert len(env.resumes()) == 1
    # The line that says it scheduled names the chunk and the budget behind it.
    left = f"{2.5 * sbq.PAPER_FIVE_HOUR_PERCENT:.1f}% left"
    line = [ln for ln in capsys.readouterr().out.splitlines() if "scheduled" in ln]
    assert len(line) == 1 and "queued 2 " in line[0] and left in line[0], line


def test_a_stranded_queue_keeps_only_what_the_budget_covers(env):
    env.write_backlog([paper(20)])
    env.write_queue([paper(i) for i in range(6)])
    env.usage = covering(3)

    assert env.sweep() == 0

    assert ids(env.queue()["papers"]) == ["p0", "p1", "p2"]
    assert ids(env.backlog()) == ["p3", "p4", "p5", "p20"]
    assert env.queue()["backlog_remaining"] == 4
    assert len(env.resumes()) == 1


@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_a_budget_that_covers_more_still_hands_out_one_session(
        env, setup, monkeypatch):
    # Cheap papers, so the untouched meter pays for more than a session holds.
    monkeypatch.setattr(sbq, "PAPER_FIVE_HOUR_PERCENT", 5.0)
    assert sbq.papers_affordable(sbq.parse_usage(json.dumps(env.usage))) > 10
    setup(env)

    assert env.sweep() == 0

    limit = sbq.BATCH_CHUNK
    assert ids(env.queue()["papers"]) == [f"p{i}" for i in range(limit)]
    assert ids(env.backlog()) == [f"p{i}" for i in range(limit, 10)]
    assert len(env.resumes()) == 1


def test_the_weekly_window_binds_when_it_covers_fewer(env):
    env.write_backlog([paper(i) for i in range(4)])
    env.usage = usage(weekly_left=2.5 * sbq.PAPER_WEEKLY_PERCENT)

    assert env.sweep() == 0

    assert ids(env.queue()["papers"]) == ["p0", "p1"]
    assert ids(env.backlog()) == ["p2", "p3"]


# Each meter, and whether its weekly window pays for no more papers than its
# five-hour one does.
@pytest.mark.parametrize("meter,weekly_binds", [
    (dict(exhausted=True, five_hour_left=50.0), False),
    (dict(exhausted=True, five_hour_left=50.0, weekly_left=0.0), True),
    (dict(five_hour_left=0.9 * sbq.PAPER_FIVE_HOUR_PERCENT), False),
    (dict(weekly_left=0.9 * sbq.PAPER_WEEKLY_PERCENT), True),
    (dict(exhausted=True, five_hour_left=0.0, weekly_left=0.0), True),
], ids=["exhausted", "weekly-exhausted", "five-hour-under-one-paper",
        "weekly-under-one-paper", "both-exhausted"])
@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_a_budget_that_covers_no_paper_writes_nothing(env, setup, meter,
                                                      weekly_binds, seoul,
                                                      capsys):
    setup(env)
    env.usage = usage(**meter)
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == []
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if "covers no paper" in ln]
    assert len(line) == 1, line
    # The resets in local time: 05:00 UTC, the weekly one on 7 January.
    assert re.search(r"five-hour [\d.]+% left \(resets 14:00\)", line[0]), line
    weekly_reset = re.search(r"weekly [\d.]+% left \(resets 2026-01-07 14:00\)",
                             line[0])
    assert bool(weekly_reset) == weekly_binds, line


def test_a_sweep_the_budget_stops_is_no_resume_without_progress(env, capsys):
    first_resume(env)
    resume_runs(env)
    env.advance(2 * COOLDOWN + 1)               # past the wait n=1 asks for
    env.usage = usage(exhausted=True, five_hour_left=0.0)
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert len(env.resumes()) == 1
    env.usage = usage()
    assert env.sweep() == 0
    assert len(env.resumes()) == 2
    with open(env.state_path, encoding="utf-8") as f:
        assert json.load(f)["resumes_without_progress"] == 1


# The meter does not clamp: a window spent past its allowance reads below 0.
@pytest.mark.parametrize("meter", [dict(five_hour_left=-0.5),
                                   dict(weekly_left=-0.5)],
                         ids=["five-hour", "weekly"])
@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_a_window_spent_past_its_allowance_covers_no_paper(env, setup, meter,
                                                           capsys):
    setup(env)
    env.usage = usage(**meter)
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == []
    assert "covers no paper" in capsys.readouterr().out


def meter_with(edit):
    u = usage()
    edit(u)
    return u


def drop(*path):
    def edit(u):
        for key in path[:-1]:
            u = u[key]
        del u[path[-1]]
    return edit


def put(value, *path):
    def edit(u):
        for key in path[:-1]:
            u = u[key]
        u[path[-1]] = value
    return edit


USAGE_ARGS = ["notebooklm", "usage", "--json"]


# Each answer, and a word the line must carry to say what was wrong with it.
@pytest.mark.parametrize("answer,why", [
    (subprocess.CompletedProcess(USAGE_ARGS, 2, "",
                                 "Error: No such command 'usage'.\n"),
     "No such command"),
    (subprocess.CompletedProcess(USAGE_ARGS, 0, "Live compute usage\n", ""), "JSON"),
    (subprocess.CompletedProcess(USAGE_ARGS, 0, "[]", ""), "object"),
    (meter_with(put("skipped", "status")), "status"),
    (meter_with(put(False, "enabled")), "enabled"),
    (meter_with(drop("is_exhausted")), "is_exhausted"),
    (meter_with(lambda u: u["windows"].pop()), "weekly"),
    (meter_with(put(None, "windows", 0, "remaining_percent")), "remaining_percent"),
    (meter_with(put("45%", "windows", 1, "remaining_percent")), "remaining_percent"),
    (meter_with(drop("windows", 0, "used_percent")), "used_percent"),
    (meter_with(drop("windows", 1, "resets_at")), "resets_at"),
    (meter_with(put("2026-01-01T05:00:00", "windows", 0, "resets_at")), "no zone"),
    (meter_with(lambda u: u["windows"].append(dict(u["windows"][0]))),
     "2 five_hour"),
    (FileNotFoundError(2, "No such file or directory", "notebooklm"), "notebooklm"),
    (subprocess.TimeoutExpired(USAGE_ARGS, 120), "timed out"),
], ids=["cli-without-usage", "not-json", "not-an-object", "skipped", "disabled",
        "no-is_exhausted", "no-weekly-window", "percent-missing", "percent-text",
        "used-missing", "no-resets_at", "naive-resets_at", "two-five_hour-windows",
        "no-cli", "timeout"])
@pytest.mark.parametrize("setup", [stranded_with_excess, backlog_only])
def test_a_budget_that_cannot_be_read_stops_the_sweep_loudly(env, setup, answer,
                                                             why, capsys):
    setup(env)
    env.usage = answer
    before = env.files()

    assert env.sweep() == 1

    assert env.files() == before
    assert env.resumes() == []
    err = capsys.readouterr().err
    assert "cannot read the NotebookLM budget" in err and why in err, err


SPENT = r"^sweep: last session used 34\.5% of the five-hour budget for "
STAMP = r"\d{4}-\d\d-\d\d \d\d:\d\d"


def first_chunk_of_four(env, scheduled_by):
    """The first resume, handed 4 papers beside one that failed for a reason of
    its own, with one more left in the backlog, by the stranded path or by a
    refill."""
    env.usage = usage(five_hour_left=90.0)
    broken = paper(9, "failed", error="no PDF found")
    if scheduled_by == "stranded":
        env.write_backlog([paper(10)])
        env.write_queue([paper(i) for i in range(4)] + [broken])
    else:
        env.write_backlog([paper(i) for i in range(5)])
        env.write_queue([broken])
    assert env.sweep() == 0
    assert len(env.resumes()) == 1
    statuses = [p["status"] for p in env.queue()["papers"]]
    assert sorted(statuses) == ["failed"] + ["pending"] * 4, statuses


@pytest.mark.parametrize("scheduled_by", ["stranded", "refill"])
@pytest.mark.parametrize("finished,deleted,line", [
    (2, False, SPENT + r"2 settled paper\(s\), resumed " + STAMP + "$"),
    (4, False, SPENT + r"4 paper\(s\), resumed " + STAMP + "$"),
    (4, True, SPENT + r"4 paper\(s\), resumed " + STAMP + "$"),
], ids=["stranded", "drained", "deleted"])
def test_a_session_reports_what_it_spent(env, capsys, scheduled_by, finished,
                                         deleted, line):
    first_chunk_of_four(env, scheduled_by)
    resume_runs(env, finished=finished)
    if deleted:
        os.remove(env.queue_path)               # as a session that drains it does
    env.usage = usage(five_hour_left=55.5)
    env.advance(5)
    assert env.sweep() == 0                     # the timer's next run, in the cooldown
    env.advance(COOLDOWN)
    capsys.readouterr()

    assert env.sweep() == 0

    out = capsys.readouterr().out
    assert len(re.findall(line, out, re.M)) == 1, out
    assert len(env.resumes()) == 2


def test_the_spend_line_names_the_resume_it_measures(env, capsys):
    first_chunk_of_four(env, "stranded")
    resumed = sbq.load_state()["resumed_at"]
    resume_runs(env, finished=4)
    env.usage = usage(exhausted=True, five_hour_left=0.0)
    lines = []
    for _ in range(3):                          # three sweeps while nothing is affordable
        env.advance(COOLDOWN + 1)
        capsys.readouterr()
        assert env.sweep() == 0
        lines += [ln for ln in capsys.readouterr().out.splitlines()
                  if "last session used" in ln]

    stamp = sbq._parse_time(resumed).astimezone().strftime("%Y-%m-%d %H:%M")
    assert len(lines) == 3 and len(set(lines)) == 1, lines
    assert lines[0].endswith(f", resumed {stamp}"), lines


@pytest.mark.parametrize("finished,papers", [
    (2, [paper(i) for i in range(20, 24)]),
    (4, [paper(i, "done") for i in range(20, 24)]),
], ids=["stranded", "drained"])
def test_no_spend_is_reported_for_another_batch(env, capsys, finished, papers):
    first_chunk_of_four(env, "stranded")
    resume_runs(env, finished=finished)
    env.write_queue(papers, created_at="2026-10-02T00:00:00Z")
    env.usage = usage(five_hour_left=55.5)
    env.advance(COOLDOWN + 1)
    capsys.readouterr()

    assert env.sweep() == 0

    assert "last session used" not in capsys.readouterr().out
    assert len(env.resumes()) == 2


# The five-hour window's resets_at slides with the clock until its first use
# anchors it (vendor/notebooklm-py/docs/adr/0037-live-usage-and-quota-api.md), so a
# resume handed an unused window reads a later resets_at once its session used it.
# The resume is recorded with the reset at 05:00.
@pytest.mark.parametrize("left_then,resets_now,reported", [
    (100.0, "2026-01-01T06:00:00+00:00", True),
    (100.0, "2026-01-01T09:59:59+00:00", True),
    (100.0, "2026-01-01T10:00:00+00:00", False),
    (100.0, "2026-01-01T04:59:59+00:00", False),
    (90.0, "2026-01-01T05:00:00+00:00", True),
    (90.0, "2026-01-01T06:00:00+00:00", False),
    (90.0, "2026-01-01T10:00:00+00:00", False),
], ids=["unused-then-anchored-1h-on", "unused-then-anchored-just-inside-5h",
        "unused-then-5h-on", "unused-then-earlier", "used-same-reset",
        "used-then-moved-1h", "used-then-reset"])
def test_spend_is_reported_only_inside_the_window_the_resume_started_in(
        env, capsys, left_then, resets_now, reported):
    env.usage = usage(five_hour_left=left_then)
    first_resume(env)
    resume_runs(env, finished=2)
    env.usage = usage(five_hour_left=left_then - 34.5, five_hour_resets=resets_now)
    env.advance(COOLDOWN + 1)
    capsys.readouterr()

    assert env.sweep() == 0

    out = capsys.readouterr().out
    assert bool(re.search(SPENT + r"2 settled paper\(s\), resumed " + STAMP + "$", out, re.M)) == reported, out
    assert len(env.resumes()) == 2


def test_no_spend_is_reported_from_a_state_without_a_reading(env, capsys):
    env.usage = usage(five_hour_left=90.0)
    first_resume(env)
    resume_runs(env, finished=2)
    env.usage = usage(five_hour_left=55.5)
    with open(env.state_path, encoding="utf-8") as f:
        state = json.load(f)
    state = {k: v for k, v in state.items() if not k.startswith("five_hour")}
    with open(env.state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    env.advance(COOLDOWN + 1)
    capsys.readouterr()

    assert env.sweep() == 0

    assert "last session used" not in capsys.readouterr().out
    assert len(env.resumes()) == 2
