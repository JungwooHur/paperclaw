# Paper scripts

The paper workflow these scripts serve is in `groups/main/CLAUDE.md`. Two of them
run **on the host** from the healer unit ([systemd/README.md](systemd/README.md)), independently of the
agent: the page audit and the batch queue sweeper.

## Page audit

The agent uploads translated sections with **hand-rolled multi-batch Notion
PATCH**. Notion returns `401 "API token is invalid"` on a large `children` payload —
a SIZE issue, not auth (the `ntn_` integration token never expires; a
`GET /pages/{id}` with the same token returns 200). The agent splits/retries and
**loses track of what it uploaded**, so whole sections get DROPPED (main body gone,
only the appendix left) or DUPLICATED/reordered. Block/heading/image counts still
look fine — **do NOT judge a page healthy from counts; check section content
coverage.** The mandatory guard `verify_sections.py` (Step 2-C in
`groups/main/CLAUDE.md`) exists, but the agent runs it by PROSE rule and can skip
it. So **`heal_verify` runs the audit on the 5-minute healer** regardless of the
agent — auto-dedups duplicate sections (keep-richest, capped) and LOUDLY flags
MISSING/CONTENT_LOSS/SUMMARIZED in the journal (`AUDIT …`). It can't recreate
content the agent never uploaded (needs re-processing), but a broken page is never
silent.

**`--json` is a contract, not a formatting preference.** `heal_verify` parses
`verify_sections --json` stdout regardless of exit code and treats unparseable
output as a crash, aborting that page's entire audit. So every early-return in
`verify_sections.main()` must still emit the normal JSON shape. A page with no
headings — a not-yet-translated page, freshly added to the DB, a NORMAL state —
emits a `NOT_TRANSLATED` finding (deliberately absent from `heal_verify`'s
loud-flag list, so it's recorded without crying wolf); a bare sentence there would
surface on every healer run as `verify-heal error RuntimeError` and skip that
page's dedup/flagging. **If you add an early return to a `--json` tool here,
return JSON.**

## Batch queue sweeper

`papers_queue.json` is driven by an agent INSIDE a container, and the dispatcher
pattern has it fan out background subagents and then *wait* for their
notifications. That only holds in INTERACTIVE mode, where an incoming message
re-invokes the agent. **A scheduled task is a one-shot container**: it takes no
piped messages, so the agent gets exactly ONE turn, and `task-scheduler.ts` closes
the container shortly after that turn ends, killing every in-flight background
subagent with it (`docs/SPEC.md`, "How Scheduling Works"). **Prose can't fix this: the agent does exactly what it says;
the runtime kills it.** So there are two layers — `groups/main/CLAUDE.md` requires a
scheduled run to POLL `TaskOutput` to completion instead of ending its turn (so a
run makes real progress), and **`sweep_batch_queue.py` runs on the 5-minute
healer** as the structural backstop: a queue with `pending`/`in_progress` work,
untouched ≥20 min, with NO agent container alive, gets its dead `in_progress`
entries reconciled back to `pending` and a fresh resume scheduled. Guarded against
misfiring — it writes nothing while a container is up or the queue is being
touched, and schedules no second resume while one is already scheduled or ran
within the cooldown. It does write in two more cases (below): a drained or absent
queue is refilled from the backlog, and an over-full stranded queue is trimmed even
while a resume is already scheduled.

**A resume that settles no paper backs off.** The auth probe runs `notebooklm
list`, which keeps working while NotebookLM throttles its chat, so under a chat
throttle each resume fails within minutes, leaves its papers pending and messages
the owner, and without a backoff the sweeper would schedule another every cooldown
all night. So each scheduled resume is recorded in
`research-papers/papers_sweep_state.json`, a host-only file like the backlog: which
batch it got and how many of its papers were settled: `done`, or `failed` for a
reason of their own, such as a page `verify_sections` refused. An env-failed paper
is retried, so it settles nothing. When that same batch is stranded again with no
more papers settled, the quiet time after the last resume becomes
`cooldown * 2**n`, capped at 12 h. The sweep prints `sweep: backing off` with n and
the time left, and writes nothing. This check runs before the auth probe. A new
batch, a drained queue, or a missing or broken state file starts again from n=0.

### Session-sized chunks

The runner gives a scheduled run `maxTurns: 50`, and a session that polls to the
end stops there after about 10 papers. So `sweep_batch_queue.py` gives each resume a
session-sized chunk, `BATCH_CHUNK = 6` papers. The rest waits in
`research-papers/papers_backlog.json`, a host-only file that no agent prompt names,
so an agent that deletes the queue at the end cannot lose it.

- A stranded queue keeps its first 6 pending papers, fewer when the NotebookLM
  budget covers fewer (below), and the rest move to the FRONT of the backlog.
- A queue that is gone or drained is refilled with the next chunk, and a resume is
  scheduled, when no container is up and no resume is recent.
- While a resume is already scheduled, a queue with too many papers is trimmed
  anyway.
- Both files are written atomically, destination first. A crash can duplicate a
  paper, which the subagent's dedup check makes harmless, but never drop one.
- Right before any write it runs `docker ps` again and compares the queue's
  bytes with its first read, and writes nothing if either changed. The
  NotebookLM auth probe alone takes seconds, and an agent that starts in that
  window writes the queue too: overwriting it would drop that agent's paper,
  or a refilled chunk that has already left the backlog.
- The queue carries `backlog_remaining`, so the final summary can say how many
  papers continue in later sessions.

**Why chunking and not a higher cap.** The 5 h no-output timeout bounds a
session whatever `maxTurns` says, because a scheduled run emits nothing until its
one turn ends. A higher cap would only turn a clean stop into a hard kill. A new
cap would also reach the main group only while its runner copy is unedited
(`docs/SPEC.md`, "Per-Group Copies: Settings and Runner"). `tests/test_sweep_batch_queue.py` covers
the chunking, and `src/task-scheduler.test.ts` covers the close.

### The live NotebookLM budget caps each chunk

A session started with too little budget left stops its papers half-translated,
and the budget they used is lost. So at both scheduling points, after the auth
probe and the backoff, the sweep reads `notebooklm usage --json` and hands the
resume no more whole papers than both windows' `remaining_percent` pay for at
`PAPER_FIVE_HOUR_PERCENT` and `PAPER_WEEKLY_PERCENT`, and none when `is_exhausted`.
The constants come from a measured meter: an ask is about 0.52 % of the five-hour
window, a paper 30-45 asks, and weekly use about a 21st of five-hour use. A budget
that covers no paper prints `budget covers no paper` with the reset time, writes
nothing and leaves the backoff count alone. A meter it cannot read (a non-zero
exit, a status other than `ready`, a missing window or field) prints
`cannot read the NotebookLM budget (<why>)`, writes nothing and exits 1: there is
no fallback, and the next cycle tries again. To recalibrate, grep the healer
journal for `last session used`. The next sweep that reads the budget prints it
when the last resume's batch is stranded, drained or deleted and the five-hour
window is the one that resume started in, and repeats it on every later sweep until
the next resume; the line ends with that resume's start time, so the copies read as
one measurement. An unused window's `resets_at` slides until its first use, so it
matches for five hours. A drained last chunk prints none: with the backlog empty,
nothing reads the budget.

### When a batch looks stalled

- **If a batch "isn't progressing", check the queue's mtime and `docker ps` before
  assuming a per-paper failure.**
- Read `papers_backlog.json` and the `papers-batch-resume-*` tasks as well as the
  queue — the queue only ever holds one session's share.
- **If resumes come hours apart, read `papers_sweep_state.json` and the
  `backing off` lines before suspecting the sweeper.**
- Whether a service restart orphaned it: [the bot's units README](../../../systemd/README.md#whatsapp-watchdog).
