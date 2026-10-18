"""A paper batch is handed to scheduled sessions one session-sized chunk at a time.

A scheduled run is capped at 50 turns, and two resumes each ran out after about
10 papers with subagents still in flight. The sweeper keeps BATCH_CHUNK papers in
the queue a session sees and the rest in a host-only backlog, and refills the
queue from it once nothing is running. Whatever it does, a paper must never be
dropped: a crash may leave one in both files, never in neither.
"""
import os
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
    """A queue, a backlog and a DB in a temp dir, with nothing running."""

    def __init__(self, tmp_path, monkeypatch):
        self.queue_path = str(tmp_path / "papers_queue.json")
        self.backlog_path = str(tmp_path / "papers_backlog.json")
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
        monkeypatch.setattr(sbq, "QUEUE", self.queue_path)
        monkeypatch.setattr(sbq, "BACKLOG", self.backlog_path)
        monkeypatch.setattr(sbq, "DB", self.db)
        monkeypatch.setattr(sbq, "agent_container_running", lambda: self.container)
        monkeypatch.setattr(sbq, "notebooklm_auth_ok", lambda: self.auth)

    def write_queue(self, papers, idle_min=60):
        sbq._write_json(self.queue_path, {"papers": papers,
                                          "created_at": "2026-10-01T00:00:00Z",
                                          "session_processed": 3})
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
        for path in (self.queue_path, self.backlog_path):
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


@pytest.mark.parametrize("broken", ["queue", "backlog"])
def test_an_unreadable_file_is_never_written_over(env, broken):
    env.write_queue([paper(i) for i in range(10)])
    env.write_backlog([paper(20)])
    path = env.queue_path if broken == "queue" else env.backlog_path
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"papers": [')
    before = env.files()

    assert env.sweep() == 1

    assert env.files() == before
    assert env.resumes() == []
