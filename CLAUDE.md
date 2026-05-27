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

## Living Documentation Policy

**Every debugging session that finds a root cause must update the relevant CLAUDE.md and push.**

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
