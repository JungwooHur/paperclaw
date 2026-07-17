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

## Concurrent paper requests (NotebookLM serialization)

Multiple paper requests are meant to run as **parallel background subagents** (the dispatcher pattern in `groups/main/CLAUDE.md`) — the user sends N papers and must **never** have to serialize them by hand. The catch: every subagent drives ONE shared NotebookLM browser profile (`~/.notebooklm`), and Chrome can't be driven by two processes at once — concurrent `notebooklm ask` calls would collide and yield summarized/stub sections. So **`container/bin/notebooklm` is a `flock` wrapper** installed over the real CLI in the Dockerfile: it serializes every NotebookLM call system-wide, so parallel subagents QUEUE their asks instead of conflicting, while the rest of each paper's pipeline (figures, tables, Notion upload) still runs in parallel. **Do NOT advise sending papers one at a time — serialization is the wrapper's job.** Requires a container rebuild to take effect. (This is a real risk but was NOT the cause of the mid-July broken batch — that was the assembly bug below; don't conflate the two.)

## Silently-broken paper assembly (verify enforcement)

The agent uploads translated sections with **hand-rolled multi-batch Notion PATCH**. Notion returns `401 "API token is invalid"` on a large `children` payload — a SIZE issue, not auth (the `ntn_` integration token never expires; a `GET /pages/{id}` with the same token returns 200). The agent splits/retries and **loses track of what it uploaded**, so whole sections get DROPPED (a paper shipped with only its appendix, main body gone) or DUPLICATED/reordered. Block/heading/image counts still look fine — **do NOT judge a page healthy from counts; check section content coverage.** The mandatory guard `verify_sections.py` (Step 2-C) exists, but the agent runs it by PROSE rule and skips it, so broken pages ship silently. Structural fix: **`heal_verify` runs the audit on the 5-minute healer** regardless of the agent — auto-dedups duplicate sections (keep-richest, capped) and LOUDLY flags MISSING/CONTENT_LOSS/SUMMARIZED in the journal (`AUDIT …`). It can't recreate content the agent never uploaded (needs re-processing), but a broken page is never silent again.

## WhatsApp connection can hang silently (watchdog)

The main service's WhatsApp socket (baileys) can drop with `Connection closed reason: 405` — a connection failure, NOT a logout (the auth session is fine) — and get stuck in a reconnect loop that eventually goes fully SILENT: the process stopped logging for 13+ hours while systemd still reported it `active (running)`, so nothing auto-recovered and WhatsApp just stopped answering. `systemctl --user restart paperclaw` reconnects instantly from the stored session. To stop it recurring, **`scripts/paperclaw-watchdog.sh` runs on a 30-minute timer** (`systemd/paperclaw-watchdog.{service,timer}`, installed as SYMLINKS so they can't drift) and restarts paperclaw when either (1) `logs/paperclaw.log` is frozen ≥ 40 min (healthy operation writes every ~10 min at the 95th percentile), or (2) the recent log is a reconnect loop (≥3 `Connection closed` with no `Connected to WhatsApp` after). **Do NOT diagnose "no WhatsApp reply" as a per-message problem — check the connection/service first** (`systemctl --user status paperclaw`, tail `logs/paperclaw.log`).

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
