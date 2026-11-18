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
| `container/skills/agent-browser/SKILL.md` | Browser automation tool (available to all agents via Bash) |

## Area Docs

Read the doc for the area a change touches. A root cause found in an area goes to that area's README, and a constraint to a test.

| Doc | Area |
|-----|------|
| [systemd/README.md](systemd/README.md) | Service unit, rebuild on restart, WhatsApp watchdog, timers |
| [container/README.md](container/README.md) | Image build cache, the NotebookLM wrapper |
| [vendor/README.md](vendor/README.md) | Vendored notebooklm-py: upgrade, what a stale copy breaks |
| [groups/main/research-papers/README.md](groups/main/research-papers/README.md) | Host-side paper scripts: page audit, batch queue sweeper, writing a script or repair, reading healer reports, re-processing a page |
| [groups/main/research-papers/systemd/README.md](groups/main/research-papers/systemd/README.md) | Paper healer unit |
| [scripts/README.md](scripts/README.md) | How the leak gates decide |
| [docs/SPEC.md](docs/SPEC.md) | Host behaviour: per-group copies, usage limits, scheduled runs |
| [docs/DEBUG_CHECKLIST.md](docs/DEBUG_CHECKLIST.md) | What to check first when the bot, a healer, a batch or CI goes wrong |

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

The paper-processing scripts under `groups/main/research-papers/` are Python and
have their own suite. CI runs it as its own step, after the TypeScript checks:

```bash
pip install -r requirements-dev.txt   # once
pytest                                 # tests/ , offline, no Notion access
```

Tests live in `tests/` and reach the scripts through `tests/conftest.py`, which
puts that directory on the path the same way the scripts reach each other. Only
pure helpers are covered — anything that calls Notion is left to the dry-run
flags the scripts already carry.

**The test suites must stay offline.** A test that reaches the network turns an
unrelated outage into a red build, and a red build that isn't your fault trains
you to ignore it.

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

## Public Repo Hygiene (MANDATORY before every commit/push/PR)

This is a **public repository**. The owner's personal data and research activity must never reach tracked files, commit messages, or PR titles/bodies.

**Never include, anywhere git-tracked or GitHub-visible:**
- Secrets/tokens of any kind (`.env` values, Notion/Claude/X tokens, cookies)
- Personal identifiers: emails, phone numbers, real WhatsApp JIDs, Notion page/DB UUIDs
- **Specific papers the owner processed**: arxiv IDs, paper titles, author names tied to actual usage. When documenting an incident, genericize: "paper A / paper B", `<arxiv-id>`, "an author-year paper". Famous papers are fine ONLY as illustrative examples (like the README's), never as incident records.
- Runtime artifacts: `store/`, `data/`, `logs/`, `attachments/`, `conversations/`, `notebooks.json`, `papers_queue.json`, `research-papers/config.json`, any `.db`/`.pdf`

**Enforcement (structural, not just prose)** — how each gate decides is in [scripts/README.md](scripts/README.md):
- Commits pass through `.husky/pre-commit` + `.husky/commit-msg`, and **pushing any branch already publishes**, so `.husky/pre-push` is the gate. Commit authors and committers must be GitHub noreply addresses.
- `scripts/private_values.py` matches the owner's REAL values, not shapes: the private keys of the main checkout's `.env`, the registered group JIDs, and `~/.config/paperclaw/private-values` (one value per line; outside the work tree, so it can never be staged). Add anything personal no pattern would recognise there. It has no override; a real value is never a false positive.
- False positive? Fix the wording first; only as a last resort `PAPERCLAW_ALLOW_SENSITIVE=1 git commit ...` (or `git push`, or in front of the gh command). It skips the pattern scan only.
- **No assistant session URLs and no tool footers — in commit messages AND in PR
  bodies.** Both are appended by the assistant's own tooling by default, so they
  have to be removed on every commit and every PR, not just remembered once.
  `check-sensitive.sh` blocks the session URL in commits, and the gh guard
  below blocks it in PR text. The **"Generated with Claude Code" footer** carries
  no private data; it is simply not wanted on this repo's public history.
  **Nothing matches the footer** — write the body without it rather than editing
  it afterwards.
- **PR and issue text is checked by a hook, not by memory.** Write the body to a
  file in its own call, then pass `--body-file` in a call of its own.

**Write the body about the CODE, not about the paper.** The leak that keeps
recurring is not a secret — it is PAPER CONTENT: section titles, which figure the
text cites where, one paper's results. "a figure with no anchor fell to the page
end" is the finding; "the Experimental Evaluation section had its figures
stranded" names someone's research. Anonymise concretely — `section 4`, `an
appendix`, `one affected page` — and keep the numbers, which are what make a
claim checkable.
