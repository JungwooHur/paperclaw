# PaperClaw

Personal Claude assistant. See [README.md](README.md) for philosophy and setup. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for architecture decisions.

## Quick Context

Single Node.js process that connects to WhatsApp, routes messages to Claude Agent SDK running in containers (Linux VMs). Each group has isolated filesystem and memory.

## Key Files

| File | Purpose |
|------|---------|
| `src/index.ts` | Orchestrator: state, message loop, agent invocation |
| `src/channels/whatsapp.ts` | WhatsApp connection, auth, send/receive |
| `src/ipc.ts` | IPC watcher and task processing |
| `src/router.ts` | Message formatting and outbound routing |
| `src/config.ts` | Trigger pattern, paths, intervals |
| `src/container-runner.ts` | Spawns agent containers with mounts |
| `src/task-scheduler.ts` | Runs scheduled tasks |
| `src/db.ts` | SQLite operations |
| `groups/{name}/CLAUDE.md` | Per-group memory (isolated) |
| `container/skills/agent-browser.md` | Browser automation tool (available to all agents via Bash) |

## Skills

| Skill | When to Use |
|-------|-------------|
| `/setup` | First-time installation, authentication, service configuration |
| `/customize` | Adding channels, integrations, changing behavior |
| `/debug` | Container issues, logs, troubleshooting |
| `/update` | Pull upstream PaperClaw changes, merge with customizations, run migrations |
| `/qodo-pr-resolver` | Fetch and fix Qodo PR review issues interactively or in batch |
| `/get-qodo-rules` | Load org- and repo-level coding rules from Qodo before code tasks |

## Development

Run commands directly—don't tell the user to run them.

```bash
npm run dev          # Run with hot reload
npm run build        # Compile TypeScript
./container/build.sh # Rebuild agent container
```

Service management:
```bash
# macOS (launchd)
launchctl load ~/Library/LaunchAgents/com.paperclaw.plist
launchctl unload ~/Library/LaunchAgents/com.paperclaw.plist
launchctl kickstart -k gui/$(id -u)/com.paperclaw  # restart

# Linux (systemd)
systemctl --user start paperclaw
systemctl --user stop paperclaw
systemctl --user restart paperclaw
```

## Container Build Cache

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT invalidate COPY steps — the builder's volume retains stale files. To force a truly clean rebuild, prune the builder then re-run `./container/build.sh`.

## systemd unit staleness (paper healer)

The paper-page healers (back-matter, source-URL, math, furniture, figure, table cleanup) run **on the host** from `paperclaw-qa-heal.service` — NOT in the container. The installed unit lives at `~/.config/systemd/user/paperclaw-qa-heal.service`; the source of truth is `groups/main/research-papers/systemd/paperclaw-qa-heal.service`.

**Failure mode (real incident):** the unit was originally `cp`-installed, then `heal_paper_pages.py` was added as a new `ExecStart` in the repo unit — but the installed copy was never refreshed. So the whole `heal_paper_pages` step (all figure/table/math/furniture/back-matter healing) **silently never ran**, and every newly-processed paper kept its un-healed state even though the code was merged. The journal is the tell: `journalctl --user -u paperclaw-qa-heal.service` showed only the old 3 steps, with zero `healed N/M` output.

**Fix / prevention:** the installed units are now **symlinks** to the repo files (content can't drift), and `/update` re-links + `daemon-reload`s them. If healers "aren't applying," first check the installed unit actually contains every `ExecStart` from the repo unit and run `systemctl --user daemon-reload`. Note: `heal_figures`/`heal_tables` are HTML-based (arxiv `arxiv.org/html/<id>`) — a **PDF-only paper (HTML 404)** can't be auto-healed for figures/tables.

## A timer can be `active` + `enabled` and still never fire again (check NEXT)

Both `paperclaw-qa-heal.timer` and `paperclaw-watchdog.timer` used `OnBootSec=` + `OnUnitActiveSec=` **together with `Persistent=true`**. That combination is a trap: `Persistent=` anchors on a REALTIME stamp (`~/.local/share/systemd/timers/stamp-*.timer`) while those two triggers are MONOTONIC (relative to the current boot). After a reboot systemd cannot map the pre-reboot stamp onto the new boot's monotonic timeline, so the timer sits with `NextElapseUSecMonotonic=infinity` — armed and dead.

**It fails silently in the worst way:** `systemctl --user is-active` says `active`, `is-enabled` says `enabled`, the unit files are correctly symlinked, and nothing is logged. **Only `list-timers` shows it — `NEXT` is `-`.** Real incident: one reboot killed BOTH timers for **six days**. Nothing healed and nothing guarded WhatsApp in that window, and a paper processed inside it shipped with 123 of its 124 equations invalid — a defect `heal_equations` repairs automatically on every run.

**Fix:** both timers now use wall-clock `OnCalendar=` (`*:0/5`, `*:0/30`), where the next elapse is always computable and `Persistent=true` actually means "catch up a missed run". **When a healer "isn't applying", check `systemctl --user list-timers` FIRST** — a blank `NEXT` means it has not been running at all, and no amount of debugging the healer code will explain the symptom:
```bash
systemctl --user list-timers 'paperclaw-*'     # NEXT must be a real timestamp
ls -la ~/.local/share/systemd/timers/          # stamp mtime = last real trigger
```

## Concurrent paper requests (NotebookLM serialization)

Multiple paper requests are meant to run as **parallel background subagents** (the dispatcher pattern in `groups/main/CLAUDE.md`) — the user sends N papers and must **never** have to serialize them by hand. The catch: every subagent drives ONE shared NotebookLM browser profile (`~/.notebooklm`), and Chrome can't be driven by two processes at once — concurrent `notebooklm ask` calls would collide and yield summarized/stub sections. So **`container/bin/notebooklm` is a `flock` wrapper** installed over the real CLI in the Dockerfile: it serializes every NotebookLM call system-wide, so parallel subagents QUEUE their asks instead of conflicting, while the rest of each paper's pipeline (figures, tables, Notion upload) still runs in parallel. **Do NOT advise sending papers one at a time — serialization is the wrapper's job.** Requires a container rebuild to take effect. (This is a real risk but was NOT the cause of the mid-July broken batch — that was the assembly bug below; don't conflate the two.)

## A stale vendored notebooklm-py lies about auth (keep it current)

`notebooklm-py` is an **unofficial CLI that drives the NotebookLM web app** with a Playwright cookie session, so it breaks whenever Google changes the app. The container installs it from the tracked copy in `vendor/notebooklm-py`, which had sat at **0.3.4 since the initial commit while upstream moved to 0.7.3** (8 releases).

**Failure mode (real incident, cost days):** the stale CLI reported a perfectly valid session as `Authentication expired or invalid … Run 'notebooklm login' to re-authenticate`. Every paper in a batch therefore failed within seconds, and each was recorded as permanently `failed`. The error message is confidently wrong and sends you down an auth rabbit hole — three interactive `notebooklm login` rounds later, the cookies were still the *same untouched ones from a month earlier*, because they were never the problem.

**The decisive test — do this before ever blaming auth:** run the SAME `~/.notebooklm/storage_state.json` against a current CLI in a throwaway venv:
```bash
python3 -m venv /tmp/nlm && /tmp/nlm/bin/pip install -q notebooklm-py
/tmp/nlm/bin/notebooklm list --json     # works here + fails on the installed one = the CLI is stale, not the session
```
Also note **`notebooklm doctor` is not trustworthy for this** — it only checks that an SID cookie exists in the file and happily prints `Auth ✓ pass` while every real request redirects to the Google sign-in page. `notebooklm list --json` is the only honest check.

**Upgrading:** replace `vendor/notebooklm-py` with the new sdist (drop `tests/`, `examples/`, `PKG-INFO` — the old vendored tree tracked neither), `pip install --user --break-system-packages --upgrade ./vendor/notebooklm-py` on the host so host-side probes agree with the container, then prune the builder and rebuild (see Container Build Cache). Verify the CLI both *inside* the image and on the host with `list --json`. The image's ENTRYPOINT speaks the agent JSON protocol, so probe it with `--entrypoint`.

## Silently-broken paper assembly (verify enforcement)

The agent uploads translated sections with **hand-rolled multi-batch Notion PATCH**. Notion returns `401 "API token is invalid"` on a large `children` payload — a SIZE issue, not auth (the `ntn_` integration token never expires; a `GET /pages/{id}` with the same token returns 200). The agent splits/retries and **loses track of what it uploaded**, so whole sections get DROPPED (a paper shipped with only its appendix, main body gone) or DUPLICATED/reordered. Block/heading/image counts still look fine — **do NOT judge a page healthy from counts; check section content coverage.** The mandatory guard `verify_sections.py` (Step 2-C) exists, but the agent runs it by PROSE rule and skips it, so broken pages ship silently. Structural fix: **`heal_verify` runs the audit on the 5-minute healer** regardless of the agent — auto-dedups duplicate sections (keep-richest, capped) and LOUDLY flags MISSING/CONTENT_LOSS/SUMMARIZED in the journal (`AUDIT …`). It can't recreate content the agent never uploaded (needs re-processing), but a broken page is never silent again.

**`--json` is a contract, not a formatting preference.** `heal_verify` parses `verify_sections --json` stdout regardless of exit code and treats unparseable output as a crash, aborting that page's entire audit. So every early-return in `verify_sections.main()` must still emit the normal JSON shape. One didn't: a page with no headings printed a bare sentence, so every **not-yet-translated page — a page freshly added to the DB, a NORMAL state** — surfaced on the 5-minute healer as `verify-heal error RuntimeError` and had its dedup/flagging skipped entirely. It now emits a `NOT_TRANSLATED` finding instead (deliberately absent from `heal_verify`'s loud-flag list, so it's recorded without crying wolf). **If you add an early return to a `--json` tool here, return JSON.**

## A paper batch can silently die mid-run (queue sweeper)

`papers_queue.json` is driven by an agent INSIDE a container, and the dispatcher pattern has it fan out background subagents and then *wait* for their notifications. That only holds in INTERACTIVE mode, where an incoming message re-invokes the agent. **A scheduled task is a one-shot container** — `task-scheduler.ts` calls `queue.closeStdin()` before running it, so the agent gets exactly ONE turn, and **the instant it ends that turn the container exits and every in-flight background subagent is killed with it.**

Seen twice, both leaving the batch dead with nobody to notice: (1) a watchdog restart detached the containers mid-run (fixed by the liveness heartbeat above), and (2) a scheduled resume dispatched 3 subagents, announced "now I'll wait for `task_notification`", ended its turn 147 s in — and the batch sat frozen for **two days** (3 papers stuck `in_progress` mid-translation, 10 never dispatched). **Prose can't fix this: the agent did exactly what it said; the runtime killed it.** So there are two layers — `groups/main/CLAUDE.md` now requires a scheduled run to POLL `TaskOutput` to completion instead of ending its turn (so a run makes real progress), and **`sweep_batch_queue.py` runs on the 5-minute healer** as the structural backstop: a queue with `pending`/`in_progress` work, untouched ≥20 min, with NO agent container alive, gets its dead `in_progress` entries reconciled back to `pending` and a fresh resume scheduled. Guarded against misfiring — it no-ops while a container is up, while the queue is being touched, when drained, and when a resume is already scheduled or ran within the cooldown. **If a batch "isn't progressing", check the queue's mtime and `docker ps` before assuming a per-paper failure.**

## WhatsApp connection can hang silently (watchdog)

The main service's WhatsApp socket (baileys) can drop with `Connection closed reason: 405` — a connection failure, NOT a logout (the auth session is fine) — and get stuck in a reconnect loop that eventually goes fully SILENT: the process stopped logging for 13+ hours while systemd still reported it `active (running)`, so nothing auto-recovered and WhatsApp just stopped answering. `systemctl --user restart paperclaw` reconnects instantly from the stored session. To stop it recurring, **`scripts/paperclaw-watchdog.sh` runs on a 30-minute timer** (`systemd/paperclaw-watchdog.{service,timer}`, installed as SYMLINKS so they can't drift) and restarts paperclaw on three signals: (1) `logs/paperclaw.log` frozen ≥ 40 min, (2) a reconnect loop (≥3 `Connection closed` with no `Connected to WhatsApp` after), or (3) the last ≥3 heartbeats all report `whatsapp=down`. **Do NOT diagnose "no WhatsApp reply" as a per-message problem — check the connection/service first** (`systemctl --user status paperclaw`, tail `logs/paperclaw.log`).

### The watchdog's own footgun: don't confuse "idle/busy" with "hung" (heartbeat)

The original watchdog decided "hung" purely from `logs/paperclaw.log` freshness, assuming "healthy operation writes every ~10 min." **That assumption was false** — paperclaw legitimately logs *nothing* for ~1h when idle, and also while a long paper batch's subagent containers grind through NotebookLM. So the watchdog kept restarting a perfectly healthy service **~hourly** (journal: `restarting paperclaw (log frozen 58m)`), and **each restart detached the batch's containers and destroyed the in-memory dispatcher loop that owns `papers_queue.json`** — a 27-paper batch stalled this way with 12 papers never dispatched and one task frozen `in_progress` for days. The dispatcher pattern assumes the main agent stays alive for the whole batch and has no restart-recovery, so an orphaned queue just sits there. **Fix:** `src/index.ts` emits an unconditional `heartbeat whatsapp=up|down` every 10 min, so log-freshness only goes stale when the event loop is *actually* hung; the watchdog's condition (3) reads the heartbeat's WA status to still catch a dead-but-not-reconnecting socket (which keeps the log fresh via heartbeats, so condition 1 can't see it). **Lesson: a liveness check needs a signal the healthy process actively emits — inferring liveness from incidental activity (log writes) false-positives the moment the process is correctly quiet.** If a long batch ever stalls, check whether a service restart orphaned it: `docker ps` (no paper containers), `papers_queue.json` mtime old with `pending`/stuck-`in_progress` entries, and `journalctl --user -u paperclaw-watchdog.service` for restart lines.

## Public Repo Hygiene (MANDATORY before every commit/push/PR)

This is a **public repository**. The owner's personal data and research activity must never reach tracked files, commit messages, or PR titles/bodies.

**Never include, anywhere git-tracked or GitHub-visible:**
- Secrets/tokens of any kind (`.env` values, Notion/Claude/X tokens, cookies)
- Personal identifiers: emails, phone numbers, real WhatsApp JIDs, Notion page/DB UUIDs
- **Specific papers the owner processed**: arxiv IDs, paper titles, author names tied to actual usage. When documenting an incident, genericize: "paper A / paper B", `<arxiv-id>`, "an author-year paper". Famous papers are fine ONLY as illustrative examples (like the README's), never as incident records.
- Runtime artifacts: `store/`, `data/`, `logs/`, `attachments/`, `conversations/`, `notebooks.json`, `papers_queue.json`, `research-papers/config.json`, any `.db`/`.pdf`

**Enforcement (structural, not just prose):**
- `.husky/pre-commit` + `.husky/commit-msg` run `scripts/check-sensitive.sh`, which blocks forbidden paths (even `git add -f`) and scans added lines / commit messages for secrets, emails, phones, JIDs, arxiv IDs, and UUIDs.
- False positive? Fix the wording first; only as a last resort `PAPERCLAW_ALLOW_SENSITIVE=1 git commit ...`.
- PR bodies aren't covered by git hooks — apply the same rules manually when writing them.

## Living Documentation Policy

**Every debugging session that finds a root cause must update the relevant CLAUDE.md and push.** Documentation written under this policy is still subject to Public Repo Hygiene above — record the *lesson*, never the *specific paper*.

This codebase improves through accumulated operational knowledge. When a bug is found and fixed in a terminal session:

1. **Identify which CLAUDE.md owns the fix:**
   - Root-level `CLAUDE.md` — core infrastructure bugs (container mounts, build cache, service restart, TypeScript compilation)
   - `groups/main/CLAUDE.md` — paper workflow bugs (Notion API quirks, ar5iv failures, figure extraction, translation issues)

2. **What to document** — only non-obvious findings worth preserving:
   - Root cause (not just the symptom)
   - The fix and *why* it works
   - Edge cases or failure modes discovered
   - DO NOT duplicate things already in the code or obvious from reading it

3. **Format** — add to the relevant section as a concise note or update existing instructions. Use a `### Known Issues & Fixes` subsection if there's no better home.

4. **Always commit and push immediately after the fix:**
   ```bash
   git add CLAUDE.md groups/main/CLAUDE.md   # whichever changed
   git commit -m "docs: <what was learned>"
   git push origin main
   ```

**Examples of things worth documenting:**
- `~/.notebooklm` must be writable (not readonly) — containers write conversation state
- ar5iv returns HTTP 200 even for failed conversions — must validate content size + markers
- Notion PATCH image blocks: omit `type` field, use `{"image": {"external": {"url": "..."}}}`
- PyMuPDF text blocks include figure labels — use drawing bboxes for figure boundary detection
- Callout blocks with `rich_text: []` render a blank line — put content in rich_text directly
- WhatsApp `documentMessage` (PDFs etc.) lives inside `documentWithCaptionMessage.message` when a caption is attached, and its caption field is separate from `imageMessage.caption`. The inbound message handler must unwrap `documentWithCaptionMessage`/`ephemeralMessage`/`viewOnceMessage` before reading `.caption`, otherwise PDF+caption messages get `content=""` and are silently dropped. PDF bytes themselves must be downloaded via `downloadMediaMessage` and written under `groups/<folder>/attachments/<msgId>.pdf` so the container agent sees them at `/workspace/group/attachments/...`
