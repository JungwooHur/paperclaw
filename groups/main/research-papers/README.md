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

**A section's ratio compares translated prose with source prose.** The page holds
figures and tables as images with their captions and drops back matter, so the
audit leaves out of the ratio what the page does not translate as body text:

- **Floats, body and caption, on the source side.** The source turns them into
  `_TABLE_MARK`s, which keep the span's extent. In LaTeXML a float is each outermost
  `<figure>` element: a figure (a prompt box set as text included), a table, an
  algorithm or a listing. In a PDF it is every line whose centre lies in the crop
  the renderer takes (`extract_pdf_media.float_crops`, the loop `render_media`
  renders) or in `extract_pdf_media.caption_regions` (the crops' caption grammar),
  so a float the renderer does not crop loses its caption only. A block the renderer
  reads as a heading (`extract_pdf_media._is_heading`) is kept inside a crop, so a
  crop that runs past its float does not unlocate the next section. A run of blanked
  lines counts as one line break, so a float of many lines adds nothing to the span.
  The audit reads each PDF page's text once without image bytes, plus a with-images
  textpage for block bounds (`extract_pdf_media.ReadOnce`, which holds the same
  blocks): the renderer's own reads carry every image's bytes, too slow to run on
  every `--source` audit of an image-heavy PDF.
- **Captions on the page.** The page skips a body paragraph opening on a float
  label set off by a separator (`extract_pdf_media.is_caption_text`; `Figure 1
  shows …` stays), since NotebookLM often translates a caption into one. Counting
  captions on one side only would push every ratio one way.
- **Table rows.** A run of 4+ short lines with no sentence end is a table, and a
  line whose tokens are mostly numbers (`22.37 16.98 … 64.13%`) is a table fragment
  at any length.
- **Back matter**, `Acknowledgement`/`Acknowledgment` singular included.

Captions still count as content where no ratio is taken: in the CONTENT_LOSS
floor (`content_chars`), so a section of tables only is not empty, and when
choosing which duplicate copy to keep.

**The page is read in reading order.** A `column_list` (text set beside a figure),
at any depth, stands for its columns' blocks, left column first, so that text
counts in the section the list sits in.

**Known limits.** A SUMMARIZED or CONTENT_LOSS deserves a hand check on a section:

- right after a PDF caption set at body size with no sentence end, which also blanks
  a body paragraph in its column and font size that starts within 26 pt below it
  (the grammar `_stitched` uses for crops);
- holding a float the renderer does not crop, in PDF mode (`render_media` reports
  it unrendered, such as a table with no ruled structure or a figure of text with
  no drawing), or the later pages of a table it crops on its first page only: that
  body counts as source prose;
- whose PDF text a float's crop still runs over: a line of body text between the
  float's graphics that the crop check lets through, text inside a drawing the
  crop takes (an abstract boxed over a teaser), or a title block of small print
  with no body line past it; that text, a heading aside, does not count. Body
  prose beside a figure or past its graphics is cut off (`_clear_of_body`);
- holding a figure whose own print that cut takes for body text: an unframed
  legend or title line of the figure's own (a body-size legend of 40 or more
  characters level with a plot, a small-print legend across the column wholly over
  it, a row title off-centre from its plot frames) is left out of the image and
  counts as source prose; a figure whose body-size caption sits within 4 pt under
  a column-wide body line of its own is refused and reported unrendered.

**A source is read by what it is, not by its name.** `--source` is a PDF (a URL
or a local path) or an HTML page (a URL). It is fetched or read once and read as a
PDF when its first kilobyte holds `%PDF-` (`https://arxiv.org/pdf/<id>` has no
`.pdf`), and a URL's body as HTML when served as `text/html` or
`application/xhtml+xml`. Anything else (a local file that is no PDF, a body
served as `application/pdf` that is none), a failed fetch or a missing file
raises, naming the source. `--arxiv` for a paper arXiv has no HTML of still
reports a WARN.

**A heading is found in the source by its label and title words**, the label read
as `section_key` reads it: `Appendix E. Title` is `E` + `Title` and `1) Title` is
`1` + `Title`, so a source printing `E Title`, `Appendix E Title`, `-E Title`
(LaTeXML's IEEE appendices) or `1) Title` locates it. Only title words of over two
letters are sought, after exactly as many words of one or two characters as the
title opens with, on the first word's line (`3.1 3D Title`, or `x0 5` for a title
`x₀.₅`), and with up to 8 other words between two (a title formula the source
spells in TeX). Any other word before the first title word makes no heading: for a
title `Method`, neither a list item `3) A method ...` nor `2) A baseline ...
requires each method` is one.
The label must open a line, so a cross-reference (`Results of Section 4.2 Title`,
`(Table B.3)`) or a number ending in the label (`12 Title`) is no heading. A section is measured only
when it and the section after it are located, and the page's last section only
when no located heading follows it: a heading found only in the table of contents
would otherwise run to the end.

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

## Writing a script or a repair

- **Add the `!` line when you add a script.** `.gitignore` ignores
  `groups/main/research-papers/*` and re-admits each script by an explicit `!`
  line, so a NEW file is silently untracked — `git add` refuses it with a hint that
  is easy to read past — while it still runs here, because the container mounts the
  working tree. To audit:
  ```bash
  for f in groups/main/research-papers/*.py; do git ls-files --error-unmatch "$f" >/dev/null 2>&1 || echo "UNTRACKED $f"; done
  ```
- **A new healer that edits text must stop at the reference list**, as every other
  healer does through `reference_section` (`clean_source_urls.plan_edits` works on
  `reference_section.body_blocks`): the English list the healer injects carries its
  arXiv and DOI links on purpose.
- **A new kind of page in the DB needs its venue in `NON_PAPER_VENUES`, or the paper
  repairs will run on it.** `page_kind.is_paper` is the one place that decides, from
  the venue the owner sets, and `heal_paper_pages.heal` skips every repair on a
  non-paper page.
- **Any healer that deletes "everything after X" needs a fraction cap**, as
  `strip_backmatter` refuses a cut over half the page.
- **A loop that deletes and re-adds is invisible to a copy count; what prevents it
  is the writer and the remover consulting the same predicate.** If one ever
  recurs on the reference list, the tell is one page logging `REFERENCES injected`
  on every cycle.
- **`\b` does not hold between a Latin word or a number and a Korean particle**: in
  `XYZ논문에서`, `Table 2에` or `3.2.2는` both sides are word characters, so a
  `\b` or `(?![\w.])` boundary makes the mention invisible. Reject only what the
  boundary must exclude: a longer token in the same script
  (`(?<![A-Za-z0-9])kw(?![A-Za-z0-9])`), or a longer number (`(?![0-9])`).
- **Fix a converter defect in the converter** (`save_qa_callout._MATH`), not only in
  the healers — otherwise every new translation re-creates the damage.
- **A 400 from Notion is always specific — never let it reach you as "Bad
  Request".** Its body names the exact block and rule
  (`body.children[84].paragraph.rich_text.length should be ≤ 100`), so a raised
  error carries the response body.
- **Validate a detector by dry-running it over the WHOLE DB, not over the page that
  motivated it.**
- **Before widening what counts as a heading, dry-run the duplicate healer over the
  pages it will touch**: a rule that CREATES headings exposes every rule that reads
  them.
- **Before landing a crop-geometry change in `extract_pdf_media`, A/B it**:
  `tools/ab_render.py` renders `render_media` against a base ref with contact
  sheets, each side's unrendered captions and the base's floats the branch lost;
  judge every added, changed and removed crop from them.
- **A script that opens a PDF sends MuPDF's messages to stderr**
  (`fitz.set_messages(fd=2)` before `fitz.open`). PyMuPDF prints them on stdout by
  default (`MuPDF error: ...`), into any JSON the script prints there;
  `tests/test_mupdf_messages.py` runs each such script on a PDF that makes MuPDF
  print one.
- `heal_skip.txt`, beside the scripts, lists page ids the healer leaves completely
  alone, compared dash-insensitively since Notion prints both forms: the owner's
  switch for a page arranged BY HAND, whose own edits would otherwise keep it in the
  healer's window. Not a heuristic.

## Reading what the healer reports

- **When a healer's log says it skipped something, check that the code path after
  the message really ends there.**
- **When a healer keeps "fixing" pages that are already correct, check what it uses
  to recognise its own output.**
- **A number in a caption is not always arabic** (`TABLE I`, `Table A:`) — check
  before assuming a page "has no tables".
- **Missing citations are mostly an input-side limit.** The translation source is
  NotebookLM's own indexed text, which leaves `\cite{...}` keys unresolved and
  carries only part of a paper's citations; the linker links the ones that survive,
  and the rest is not a translation or linking bug — do not go looking for one.
  `link_page` reports `warning: body has no citations (N in source)` when the
  translation dropped every marker: **a page in that state needs re-translating; the
  linker cannot help it.**

## Re-processing a page

`collect_papers.py --status … --clear-unprocessed` (the subagent's step 0 in
`groups/main/CLAUDE.md`) does not cover two runs of the same paper at once: the
second can clear what the first has uploaded so far, and the page ends up with both
copies for `heal_verify` to dedup. And a genuinely short paper (3+ figures, at most
4 sections, under 4,000 characters) reads as unfinished to both the audit and the
status, so a request re-translates it and the list never comes.
