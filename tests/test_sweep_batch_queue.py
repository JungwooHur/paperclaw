"""A paper batch is handed to scheduled sessions one session-sized chunk at a time.

A scheduled run is capped at 50 turns, and two resumes each ran out after about
10 papers with subagents still in flight. The sweeper keeps BATCH_CHUNK papers in
the queue a session sees and the rest in a host-only backlog, and refills the
queue from it once nothing is running. Whatever it does, a paper must never be
dropped: a crash may leave one in both files, never in neither.
"""
import datetime
import json
import os
import re
import sqlite3
import time

import pytest

import sweep_batch_queue as sbq

CHUNK = sbq.BATCH_CHUNK

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
        self.probes = 0
        self.clock = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        monkeypatch.setattr(sbq, "QUEUE", self.queue_path)
        monkeypatch.setattr(sbq, "BACKLOG", self.backlog_path)
        monkeypatch.setattr(sbq, "SWEEP_STATE", self.state_path)
        monkeypatch.setattr(sbq, "DB", self.db)
        monkeypatch.setattr(sbq, "_now", lambda: self.clock)
        monkeypatch.setattr(sbq, "agent_container_running", lambda: self.container)
        monkeypatch.setattr(sbq, "notebooklm_auth_ok", self._probe)

    def _probe(self):
        self.probes += 1
        return self.auth

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


def test_a_refill_waits_out_an_expired_notebooklm_session(env):
    backlog_only(env)
    env.auth = False
    before = env.files()

    assert env.sweep() == 0

    assert env.files() == before
    assert env.resumes() == []


def test_an_already_scheduled_resume_still_gets_a_session_sized_queue(env):
    scheduled_with_excess(env)

    assert env.sweep() == 0

    assert ids(env.queue()["papers"]) == [f"p{i}" for i in range(CHUNK)]
    assert ids(env.backlog()) == [f"p{i}" for i in range(CHUNK, 10)]
    assert env.queue()["backlog_remaining"] == 10 - CHUNK
    assert env.resumes() == ["papers-batch-resume-earlier"]


def test_moved_papers_go_to_the_front_of_the_backlog_once(env):
    # p8 is already waiting under another entry id, and p9, which has no arxiv
    # id, is matched by its url.
    env.write_backlog([paper(20), paper(8, id="p8-again"),
                       paper(9, arxiv_id=None, id="p9-again")])
    env.write_queue([paper(i) for i in range(9)] + [paper(9, arxiv_id=None)])

    assert env.sweep() == 0

    assert ids(env.backlog()) == ["p6", "p7", "p8", "p9", "p20"]


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


# The NotebookLM probe takes seconds; the DB can be locked for up to 15 s.
@pytest.mark.parametrize("setup,slow_step", [
    (backlog_only, "notebooklm_auth_ok"),
    (stranded_with_excess, "notebooklm_auth_ok"),
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


def test_a_state_that_cannot_be_written_never_costs_the_resume(env, monkeypatch):
    env.write_queue([paper(i) for i in range(4)])
    real = sbq._write_json

    def no_room_for_state(path, data):
        if path == env.state_path:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(sbq, "_write_json", no_room_for_state)

    assert env.sweep() == 0

    assert len(env.resumes()) == 1
    assert not os.path.exists(env.state_path)
