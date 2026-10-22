# PaperClaw Specification

A personal Claude assistant accessible via WhatsApp, with persistent memory per conversation, scheduled tasks, and email integration.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Folder Structure](#folder-structure)
3. [Configuration](#configuration)
4. [Memory System](#memory-system)
5. [Session Management](#session-management)
6. [Message Flow](#message-flow)
7. [Commands](#commands)
8. [Scheduled Tasks](#scheduled-tasks)
9. [MCP Servers](#mcp-servers)
10. [Deployment](#deployment)
11. [Security Considerations](#security-considerations)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        HOST (macOS)                                  │
│                   (Main Node.js Process)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐                     ┌────────────────────┐        │
│  │  WhatsApp    │────────────────────▶│   SQLite Database  │        │
│  │  (baileys)   │◀────────────────────│   (messages.db)    │        │
│  └──────────────┘   store/send        └─────────┬──────────┘        │
│                                                  │                   │
│         ┌────────────────────────────────────────┘                   │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────┐    ┌──────────────────┐    ┌───────────────┐  │
│  │  Message Loop    │    │  Scheduler Loop  │    │  IPC Watcher  │  │
│  │  (polls SQLite)  │    │  (checks tasks)  │    │  (file-based) │  │
│  └────────┬─────────┘    └────────┬─────────┘    └───────────────┘  │
│           │                       │                                  │
│           └───────────┬───────────┘                                  │
│                       │ spawns container                             │
│                       ▼                                              │
├─────────────────────────────────────────────────────────────────────┤
│                     CONTAINER (Linux VM)                              │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    AGENT RUNNER                               │   │
│  │                                                                │   │
│  │  Working directory: /workspace/group (mounted from host)       │   │
│  │  Volume mounts:                                                │   │
│  │    • groups/{name}/ → /workspace/group                         │   │
│  │    • groups/global/ → /workspace/global/ (non-main only)        │   │
│  │    • data/sessions/{group}/.claude/ → /home/node/.claude/      │   │
│  │    • Additional dirs → /workspace/extra/*                      │   │
│  │                                                                │   │
│  │  Tools (all groups):                                           │   │
│  │    • Bash (safe - sandboxed in container!)                     │   │
│  │    • Read, Write, Edit, Glob, Grep (file operations)           │   │
│  │    • WebSearch, WebFetch (internet access)                     │   │
│  │    • agent-browser (browser automation)                        │   │
│  │    • mcp__paperclaw__* (scheduler tools via IPC)                │   │
│  │                                                                │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| WhatsApp Connection | Node.js (@whiskeysockets/baileys) | Connect to WhatsApp, send/receive messages |
| Message Storage | SQLite (better-sqlite3) | Store messages for polling |
| Container Runtime | Containers (Linux VMs) | Isolated environments for agent execution |
| Agent | @anthropic-ai/claude-agent-sdk (0.2.29) | Run Claude with tools and MCP servers |
| Browser Automation | agent-browser + Chromium | Web interaction and screenshots |
| Runtime | Node.js 20+ | Host process for routing and scheduling |

---

## Folder Structure

```
paperclaw/
├── CLAUDE.md                      # Project context for Claude Code
├── docs/
│   ├── SPEC.md                    # This specification document
│   ├── REQUIREMENTS.md            # Architecture decisions
│   └── SECURITY.md                # Security model
├── README.md                      # User documentation
├── package.json                   # Node.js dependencies
├── tsconfig.json                  # TypeScript configuration
├── .mcp.json                      # MCP server configuration (reference)
├── .gitignore
│
├── src/
│   ├── index.ts                   # Orchestrator: state, message loop, agent invocation
│   ├── channels/
│   │   └── whatsapp.ts            # WhatsApp connection, auth, send/receive
│   ├── ipc.ts                     # IPC watcher and task processing
│   ├── router.ts                  # Message formatting and outbound routing
│   ├── config.ts                  # Configuration constants
│   ├── types.ts                   # TypeScript interfaces (includes Channel)
│   ├── logger.ts                  # Pino logger setup
│   ├── db.ts                      # SQLite database initialization and queries
│   ├── group-queue.ts             # Per-group queue with global concurrency limit
│   ├── mount-security.ts          # Mount allowlist validation for containers
│   ├── whatsapp-auth.ts           # Standalone WhatsApp authentication
│   ├── task-scheduler.ts          # Runs scheduled tasks when due
│   └── container-runner.ts        # Spawns agents in containers
│
├── container/
│   ├── Dockerfile                 # Container image (runs as 'node' user, includes Claude Code CLI)
│   ├── build.sh                   # Build script for container image
│   ├── agent-runner/              # Code that runs inside the container
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── src/
│   │   │   ├── index.ts           # Entry point (query loop, IPC polling, session resume)
│   │   │   ├── turn-attribution.ts # Which inputs each result answered
│   │   │   └── ipc-mcp-stdio.ts   # Stdio-based MCP server for host communication
│   │   └── test/                  # Its tests, kept out of src/ (compiled at every start)
│   └── skills/
│       └── agent-browser.md       # Browser automation skill
│
├── dist/                          # Compiled JavaScript (gitignored)
│   └── agent-runner-src/          # The runner staged by the same build; a host pins it to data/runner-bundle/ when it starts
│
├── .claude/
│   └── skills/
│       ├── setup/SKILL.md              # /setup - First-time installation
│       ├── customize/SKILL.md          # /customize - Add capabilities
│       ├── debug/SKILL.md              # /debug - Container debugging
│       ├── add-telegram/SKILL.md       # /add-telegram - Telegram channel
│       ├── add-gmail/SKILL.md          # /add-gmail - Gmail integration
│       ├── add-voice-transcription/    # /add-voice-transcription - Whisper
│       ├── x-integration/SKILL.md      # /x-integration - X/Twitter
│       ├── convert-to-apple-container/  # /convert-to-apple-container - Apple Container runtime
│       └── add-parallel/SKILL.md       # /add-parallel - Parallel agents
│
├── groups/
│   ├── CLAUDE.md                  # Global memory (all groups read this)
│   ├── main/                      # Self-chat (main control channel)
│   │   ├── CLAUDE.md              # Main channel memory
│   │   └── logs/                  # Task execution logs
│   └── {Group Name}/              # Per-group folders (created on registration)
│       ├── CLAUDE.md              # Group-specific memory
│       ├── logs/                  # Task logs for this group
│       └── *.md                   # Files created by the agent
│
├── store/                         # Local data (gitignored)
│   ├── auth/                      # WhatsApp authentication state
│   └── messages.db                # SQLite database (messages, chats, scheduled_tasks, task_run_logs, registered_groups, sessions, router_state)
│
├── data/                          # Application state (gitignored)
│   ├── sessions/                  # Per-group session data (.claude/ dirs with JSONL transcripts)
│   ├── runner-bundle/             # The runner this host hands to groups, pinned when it started
│   ├── env/env                    # Copy of .env for container mounting
│   └── ipc/                       # Container IPC (messages/, tasks/)
│
├── logs/                          # Runtime logs (gitignored)
│   ├── paperclaw.log               # Host stdout
│   └── paperclaw.error.log         # Host stderr
│   # Note: Per-container logs are in groups/{folder}/logs/container-*.log
│
└── launchd/
    └── com.paperclaw.plist         # macOS service configuration
```

---

## Configuration

Configuration constants are in `src/config.ts`:

```typescript
import path from 'path';

export const ASSISTANT_NAME = process.env.ASSISTANT_NAME || 'Andy';
export const POLL_INTERVAL = 2000;
export const SCHEDULER_POLL_INTERVAL = 60000;

// Paths are absolute (required for container mounts)
const PROJECT_ROOT = process.cwd();
export const STORE_DIR = path.resolve(PROJECT_ROOT, 'store');
export const GROUPS_DIR = path.resolve(PROJECT_ROOT, 'groups');
export const DATA_DIR = path.resolve(PROJECT_ROOT, 'data');

// Container configuration
export const CONTAINER_IMAGE = process.env.CONTAINER_IMAGE || 'paperclaw-agent:latest';
export const CONTAINER_TIMEOUT = parseInt(process.env.CONTAINER_TIMEOUT || '1800000', 10); // 30min default
export const IPC_POLL_INTERVAL = 1000;
export const IDLE_TIMEOUT = parseInt(process.env.IDLE_TIMEOUT || '1800000', 10); // 30min — keep container alive after last result
export const MAX_CONCURRENT_CONTAINERS = Math.max(1, parseInt(process.env.MAX_CONCURRENT_CONTAINERS || '5', 10) || 5);

export const TRIGGER_PATTERN = new RegExp(`^@${ASSISTANT_NAME}\\b`, 'i');
```

**Note:** Paths must be absolute for container volume mounts to work correctly.

### Container Configuration

Groups can have additional directories mounted via `containerConfig` in the SQLite `registered_groups` table (stored as JSON in the `container_config` column). Example registration:

```typescript
registerGroup("1234567890@g.us", {
  name: "Dev Team",
  folder: "dev-team",
  trigger: "@Andy",
  added_at: new Date().toISOString(),
  containerConfig: {
    additionalMounts: [
      {
        hostPath: "~/projects/webapp",
        containerPath: "webapp",
        readonly: false,
      },
    ],
    timeout: 600000,
  },
});
```

Additional mounts appear at `/workspace/extra/{containerPath}` inside the container.

**Mount syntax note:** Read-write mounts use `-v host:container`, but readonly mounts require `--mount "type=bind,source=...,target=...,readonly"` (the `:ro` suffix may not work on all runtimes).

### Claude Authentication

Configure authentication in a `.env` file in the project root. Two options:

**Option 1: Claude Subscription (OAuth token)**
```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```
Generate it with `claude setup-token` and pick the account in the browser. Do not copy the access token out of `~/.claude/.credentials.json`: that one is short-lived and refreshed by the CLI, so a copy stops working within hours.

**Option 2: Pay-per-use API Key**
```bash
ANTHROPIC_API_KEY=sk-ant-api03-...
```

The host re-reads a fixed allowlist from `.env` on every spawn (`readSecrets()` in `src/container-runner.ts`: the two auth variables, `NOTION_TOKEN`, `NOTION_RESEARCH_DB`, `OUTPUT_LANGUAGE`) and passes it to the agent runner inside the JSON on stdin, so nothing is written to disk or mounted, and other `.env` variables are not exposed to the agent. Because it is read per spawn, a new token takes effect on the next container without a restart. See [SECURITY.md](SECURITY.md) for how the auth variables are kept from the agent's own subprocesses.

**Model.** The container is started with `ANTHROPIC_MODEL` set to `AGENT_MODEL` (`.env`, default `claude-sonnet-5-5`), and the `sonnet`/`opus`/`haiku` aliases are remapped to the same family. Without that, the CLI bundled with the agent SDK uses the default model it shipped with, which an account may not be able to use.

### Changing the Assistant Name

Set the `ASSISTANT_NAME` environment variable:

```bash
ASSISTANT_NAME=Bot npm start
```

Or edit the default in `src/config.ts`. This changes:
- The trigger pattern (messages must start with `@YourName`)
- The response prefix (`YourName:` added automatically)

### Placeholder Values in launchd

Files with `{{PLACEHOLDER}}` values need to be configured:
- `{{PROJECT_ROOT}}` - Absolute path to your paperclaw installation
- `{{NODE_PATH}}` - Path to node binary (detected via `which node`)
- `{{HOME}}` - User's home directory

---

## Memory System

PaperClaw uses a hierarchical memory system based on CLAUDE.md files.

### Memory Hierarchy

| Level | Location | Read By | Written By | Purpose |
|-------|----------|---------|------------|---------|
| **Global** | `groups/CLAUDE.md` | All groups | Main only | Preferences, facts, context shared across all conversations |
| **Group** | `groups/{name}/CLAUDE.md` | That group | That group | Group-specific context, conversation memory |
| **Files** | `groups/{name}/*.md` | That group | That group | Notes, research, documents created during conversation |

### How Memory Works

1. **Agent Context Loading**
   - Agent runs with `cwd` set to `groups/{group-name}/`
   - Claude Agent SDK with `settingSources: ['project']` automatically loads:
     - `../CLAUDE.md` (parent directory = global memory)
     - `./CLAUDE.md` (current directory = group memory)

2. **Writing Memory**
   - When user says "remember this", agent writes to `./CLAUDE.md`
   - When user says "remember this globally" (main channel only), agent writes to `../CLAUDE.md`
   - Agent can create files like `notes.md`, `research.md` in the group folder

3. **Main Channel Privileges**
   - Only the "main" group (self-chat) can write to global memory
   - Main can manage registered groups and schedule tasks for any group
   - Main can configure additional directory mounts for any group
   - All groups have Bash access (safe because it runs inside container)

---

## Session Management

Sessions enable conversation continuity - Claude remembers what you talked about.

### How Sessions Work

1. Each group has a session ID stored in SQLite (`sessions` table, keyed by `group_folder`)
2. Session ID is passed to Claude Agent SDK's `resume` option
3. Claude continues the conversation with full context
4. Session transcripts are stored as JSONL files in `data/sessions/{group}/.claude/`

### Per-Group Copies: Settings and Runner

Each group gets two copies under `data/sessions/{group}/` the first time its container starts, and they are kept differently.

**`.claude/settings.json` is seeded once** and never refreshed. A change to the settings defaults in `src/container-runner.ts` therefore reaches new groups only. To apply one to an existing group, delete that group's `settings.json` and it is recreated on the next run. A setting that must reach every group is passed with `-e` on the container command instead, as the model pin is.

**`agent-runner-src/` follows the bundled runner unless the group edited it.** The container compiles this copy, mounted writable at `/app/src`, not `container/agent-runner/src/`, so an agent can change its own runner without touching other groups. The bundle is the runner the host's own build staged: `npm run build` copies `container/agent-runner/src/` to `dist/agent-runner-src/` (`src/stage-runner.ts`), and a host running from `dist/` refreshes groups from that copy as it was when the host started (below). `npm run dev` runs the sources and refreshes from the checkout. Beside the copy, outside the mount, `agent-runner-src.seed.json` records a content hash of the copy as it was installed, taken from the copy and not the bundle, so a bundle that changes during the copy cannot make an unedited copy look edited. The hash covers the sorted relative paths and their contents, so mtimes and readdir order do not change it. At every container start, `src/runner-seed.ts` compares the copy with the bundle and the marker:

| The copy | What happens |
|----------|--------------|
| is missing or empty | the bundle is copied in and the marker written. An empty directory is what the container runtime leaves after mounting a copy that did not exist |
| equals the bundle | kept; a missing or stale marker is rewritten (this adopts a copy seeded before markers existed). The host also adopts every unmarked copy equal to the bundle when it starts, so a copy is not left unmarked until its group's next container start, which may come after the next runner change |
| equals the marker, and the bundle changed | replaced by the bundle, marker updated, logged at info with the group and both short hashes |
| differs from the marker | kept, since it was edited in place. A WARN names the group whenever the bundle has changed since seeding |
| differs from the bundle and has no marker | kept with a WARN, like an edited copy, because an old seed cannot be told from an edit |

The replaced copy is removed after the swap, so there is no older copy to fall back to. A replacement is copied into a sibling directory and swapped in by rename, so a failure never leaves a half-copied tree. A replaced copy that cannot be removed afterwards (a directory made read-only inside the container, say) is left beside the copy with a WARN naming its path, for the owner to remove by hand; it never blocks a later refresh. A failure to hash, copy or swap is logged as an error, and the container starts with the copy as it is.

A runner change therefore takes effect like a host change: with the restart. The build the restart runs (`ExecStartPre=`) stages it, and a host running from `dist/` copies the staged runner to `data/runner-bundle/` when it starts and refreshes groups only from that copy, at each unedited group's next container start. A `git pull`, or a build while the host runs (setup, `/update`, a recipe that builds before it rebuilds the image), reaches no group before the next restart. The copy is compared with the staged runner after it is taken and taken again if a build swapped it meanwhile, so it never holds half of two versions. When nothing is staged, or no whole copy can be taken, it is removed: each container start then logs a WARN and keeps the group's copy as it is, never falling back to the checkout or to an earlier host's copy.

The build stages the runner only after tsc succeeded, and a failed tsc writes nothing (`noEmitOnError`), so a failed tsc leaves the previous host and the previous runner in place together. Staging refuses a runner that does not parse (an unresolved merge conflict, say) and fails the build. That comes after tsc wrote the new host, so the new host then runs beside the previous runner. Either way, at startup a staged copy older than `container/agent-runner/src/` is named in a WARN, as a `dist/` older than `src/` is, and the cause is at the start of `logs/paperclaw.log`, where tsc and the staging step both print. The check is syntax only, because type-checking needs the runner's packages, which only the image has. The image compiles the runner at every container start, under `set -e`, so a runner that parses but does not compile is still staged, and after the restart every container of those groups exits at start. **Rebuild the image with `./container/build.sh` before the restart when a change touches `container/Dockerfile` or anything under `container/agent-runner/` outside `src/`** (`package.json` for a new package, `tsconfig.json`): the image bakes those in, and a runner that needs them fails the same way until it is rebuilt. `/update` does this.

**To take the bundled runner after an edit**, delete `data/sessions/{group}/agent-runner-src/`. The next start seeds it again, and the edits are lost. The WARN says the same.

---

## Message Flow

### Incoming Message Flow

```
1. User sends WhatsApp message
   │
   ▼
2. Baileys receives message via WhatsApp Web protocol
   │
   ▼
3. Message stored in SQLite (store/messages.db)
   │
   ▼
4. Message loop polls SQLite (every 2 seconds)
   │
   ▼
5. Router checks:
   ├── Is chat_jid in registered groups (SQLite)? → No: ignore
   └── Does message match trigger pattern? → No: store but don't process
   │
   ▼
6. Router catches up conversation:
   ├── Fetch all messages since last agent interaction
   ├── Format with timestamp and sender name
   └── Build prompt with full conversation context
   │
   ▼
7. Router invokes Claude Agent SDK:
   ├── cwd: groups/{group-name}/
   ├── prompt: conversation history + current message
   ├── resume: session_id (for continuity)
   └── mcpServers: paperclaw (scheduler)
   │
   ▼
8. Claude processes message:
   ├── Reads CLAUDE.md files for context
   └── Uses tools as needed (search, email, etc.)
   │
   ▼
9. Router prefixes response with assistant name and sends via WhatsApp
   │
   ▼
10. Router updates last agent timestamp and saves session ID
```

### Trigger Word Matching

Messages must start with the trigger pattern (default: `@Andy`):
- `@Andy what's the weather?` → ✅ Triggers Claude
- `@andy help me` → ✅ Triggers (case insensitive)
- `Hey @Andy` → ❌ Ignored (trigger not at start)
- `What's up?` → ❌ Ignored (no trigger)

### Conversation Catch-Up

When a triggered message arrives, the agent receives all messages since its last interaction in that chat. Each message is formatted with timestamp and sender name:

```
[Jan 31 2:32 PM] John: hey everyone, should we do pizza tonight?
[Jan 31 2:33 PM] Sarah: sounds good to me
[Jan 31 2:35 PM] John: @Andy what toppings do you recommend?
```

This allows the agent to understand the conversation context even if it wasn't mentioned in every message.

### Usage Limits

When the account behind the token runs out of quota, the CLI bundled with the agent SDK answers each request with a one-line refusal ("You've hit your limit · resets …"). The agent runner passes that on as a successful result. `src/usage-limit.ts` recognises it, and the host does not send it as a reply:

- The refused messages become pending again, and the group keeps its session, so a follow-up after the reset still has the earlier context.
- That session keeps the refused prompt too. Measured with the CLI the runner pins (`container/agent-runner/test/turn-attribution-probe.test.ts`): the request that resumes it after the reset carries the refused prompt and the copy sent again in one user message, with the refusal itself dropped, so the model reads the question twice. A delivery into that session therefore puts one short prefix before each message the refused turn took, `RESENT_PREFIX` in `src/router.ts`: `[Sent again: a usage limit stopped the earlier copy before any answer]`. The session and the ranges of those messages are kept in `router_state` (`usage_limit_refused_turn`) until a run ends with the cursor past them. Not marked: a message the window held that no turn took, a command sent bare (the CLI runs one only at the very start of a prompt), and anything once the group no longer keeps that session. A new session whose first turn was refused is never stored, so the next container starts another, with no copy in it.
- The window lives in `router_state` (`usage_limited_until`), so it survives a restart. While it is open, no container is started for messages or for scheduled runs; they wait.
- Held messages run two minutes after the reset, without a new message to trigger them, and follow the window when a scheduled run's refusal moves its reset. When the window closes, at the reset or on an answer, a group whose container is still running gets its held messages piped into that container, the way the message loop pipes a follow-up; only when it takes none does a new container start for them. A container already told to close (on idle, for a scheduled run waiting for it, or on a refusal) takes no pipe, from the message loop either, since it exits without reading new input; the messages wait for the group's next container. An answer releases the held messages only after it has settled its own prompt and let the container go idle, so they are neither settled by it nor piped into a container it closed. A container is not idle while its input dir holds a follow-up it has not read: a pipe can land while an answer is still being sent (another group's answer closing the window releases held messages then), and closing it for a waiting scheduled run would leave that follow-up to the run's container. It closes after the turn the follow-up starts. The message loop skips a group whose polled messages are already behind its cursor, because recovery piped them or a container took them at start-up between two polls. Queued for later instead, they would wait until the running container closed on idle, half an hour after its last answer.
- The group is told once per window when work will resume. A later refusal that moves the reset more than an hour past the announced time sends a corrected notice; one that moves it earlier sends none. After an answer closed the window (recorded, with the closed window's reset, in `usage_limit_closed`), a refusal within ten minutes is the retry a wrong close costs and reopens that window silently; a later one opens a new window and is announced, even while the announced reset is still ahead.
- A reset the text states, still ahead, sets the window, earlier or later than the one open. A reset the text does not state, or one already in the past, is a guess of an hour from now, and a guess never ends an open window sooner. The retry within ten minutes of a close counts the closed window as open, so a guess there reopens it to its reset instead of retrying the held work, refused again each time, every hour until then.
- An answer while the window is open closes it, and the held messages run at once instead of at the reset. Only a container already running can answer into an open window. If the close was wrong, the first held message is refused again and reopens the window without a second notice, as above.
- An answer is result text that is neither the refusal nor one of the error messages the CLI writes as a turn's result in place of one, which the runner forwards as successes. An error message is recognised only as a whole sentence the CLI makes, with the parts it fills in (the request's error, a model name, a size) left free, empty included, so an answer that merely starts with an error's words ("Request timed out on arXiv, so …") is still an answer. The one open prefix is `API Error:`, which the CLI always follows with a status or a reason, or with nothing for an error that has no message. Today the bundle's own `API Error: {message}` template covers every text that prefix does, so `src/usage-limit-cli.test.ts` checks the prefix with the templates taken away: it is the guard once a bundle drops them.

Scheduled runs are deferred instead; see [How Scheduling Works](#how-scheduling-works). Any other error result from the SDK is still sent as the reply. Which messages a refusal puts back is exact when the group's runner reports `turn`: the refused ones and any other no turn answered, not one a turn answered after them ([Turn Attribution](#turn-attribution)); without it, only while each result answers one prompt (#114).

The refusal and the error messages are worded by the CLI bundled with the agent SDK, which the SDK spawns from beside its own module, not by the global `claude-code` the image also installs. It runs non-interactive under the SDK (its stdout is a pipe), and words its errors for that mode. `container/agent-runner/package.json` pins the SDK to one exact version, and `src/usage-limit-cli-sentences.json` lists, read out of that version's bundle by `scripts/extract-usage-limit-sentences.ts`, every refusal it can write and every error message it can return as a result, the latter as templates whose filled-in parts are marked. `src/usage-limit.ts` builds its error check from those templates; `src/usage-limit-cli.test.ts` checks the classifier against every refusal and every error, and that no error is an answer. The test fails for any pin the list was not read from, so an SDK bump stays red until `npx tsx scripts/extract-usage-limit-sentences.ts <cli.js>` reads the list again from the new bundle; a reworded refusal then fails the classifier check instead of going out as a reply.

`API Error: Rate limit reached` is deliberately not treated as a usage limit. It is a 429 without the account's usage headers, a request-rate throttle rather than a quota window, and the hour's hold for an unknown reset would delay messages far longer than such a throttle. It stays an error sent as the reply.

### Turn Attribution

The runner reports which inputs each result answered, and the host settles exactly those messages (#114).

- **Input ids.** An IPC input file may carry `ref` beside `type` and `text`. Its id is that `ref`, or its file name when it has none. The stdin prompt's id is `ContainerInput.promptRef`, or `stdin`. Files already waiting when a container starts are drained into its first prompt, and that prompt's ids are `[promptRef, ...files]`. The host makes each id the message-cursor range its input covers, `<from>|<to>`: `promptRef` for the prompt a container starts with, `ref` for each follow-up it pipes. A range names itself, so a file an earlier container left, and the next one drained at start-up, is still recognised, and the host registers it as a prompt of that container (below).
- **`turn`.** Every output the runner makes from an SDK result carries `turn: {inputs, internal?}`, a textless one (`result: null`) included: the ids of the inputs that result's turn took, its prompt first, then what was merged into it. `internal: true` marks a turn that took something the host never sent: the runner's background-task reminder, or a message the CLI queued itself, such as a team member's message to the lead (agent teams are on in every group's seeded `settings.json`). A turn only those started has `inputs: []`. The session-update marker written after a query and the runner's error outputs carry no `turn`, which tells a textless answer from the marker.
- **How it is known.** A `result` names no input, so the runner starts the CLI with `--replay-user-messages`. Each push goes out under its own uuid and comes back once, as a replayed user message, when the CLI takes it, whether as a turn's prompt or merged into a running turn. The acknowledgements since the previous result belong to the next result. A follow-up sent while a tool runs is merged into that turn; one sent during the turn's last API call is the next turn's. The uuid must be fresh on every push, a retry included: the CLI acknowledges a uuid it has already seen at once, with the new text, but neither runs it nor sends it to the model, so a reused uuid is a false acknowledgement and the next result would claim an input nobody answered.
- **Slash commands.** A host prompt can start with `/`: when the newest message is a command, the router sends it bare (`src/router.ts`), and the CLI never acknowledges a command that starts a turn under the uuid it was pushed with. Sent bare, a batch would lose the messages before its command, so such a batch goes as two prompts (`firstPrompt`): the messages before the command, then the command, each command at the end of a batch in a prompt of its own. The cursor stops before the commands, and they are piped once a turn ends with no prompt the container was given left for a later turn: each is answered, or was taken by a turn that failed, since a command that reaches a running turn is merged into it as plain text. Results without `turn` cannot show that (a follow-up merged into a turn, or a turn answered only through `send_message`, never settles), so there the commands go when any turn ends, as before the hold. A container that closes first, for a task waiting on the group, leaves them to the group's next container, which the end of its run queues; a message that waits behind them marks the group for that container too, so an exit no run follows (a refusal's, once an answer has closed the window) leaves nothing stranded. A put-back range that ends in a command is such a batch too. In a group that needs the trigger, messages before the command that would not start the agent are context, not a request, and are not sent as a prompt of their own: the batch goes as the command alone, as before. The newest command held back is kept in `router_state` (`held_command`) until the cursor passes it. Until then the held commands go before anything newer, one per prompt, so a message that arrives while they wait goes after them instead of turning them into plain text, across a put-back or a restart too; in a group that needs the trigger they start the agent without it, as the batch they ended did. The cursor is a timestamp and cannot stop between two messages that share one, and WhatsApp stamps whole seconds: a command stored in the same second as the message before it is not held back, and the batch goes as the command alone, as before. It replays a skill expanded, and a local command's output, under uuids it makes itself, and replays nothing for an unknown command. The CLI takes one queued push per turn, oldest first, so the runner counts turn starts (`system/init`) to know which push each turn took, and a command is answered by the result of the turn that took it. A command sent while a tool runs is merged as plain text and acknowledged like any input; one sent during a turn's last API call runs as the next turn, whose result answers it. The runner keeps a copy of the router's pattern, and a test fails when the two differ. A turn the CLI starts itself can run ahead of a queued command: a team member's message that reached the lead during the previous turn is queued first. That turn's start is first counted as the command's, but the turn replays only what the CLI queued, a `<teammate-message>` block or the shutdown prompt it injects when its input closes with teammates up, never a command's expansion or output, so the runner leaves the command to the turn after it. Known limit: a turn the CLI starts itself that fails before it replays anything, or replays anything else, still takes the queued command. The command's own turn then reports `inputs: []` with `internal` (a skill or a local command), or, for an unknown command, turns attribution off for the rest of the container.
- **When it is missing or short.** Every turn replays something, the runner's push or a message the CLI made, except a rejected slash command. So a success result after no replayed message at all, in a turn no slash command started, means replay is not working: the runner drops `turn` for the rest of that container and logs it once, so a missing `turn` means unknown, never "answered nothing". From the output the guard trips on, every output the runner writes carries `attribution: 'off'` instead, the session marker and error outputs included: a textless result has no `turn` either way, and only this field tells it from the marker. A group whose runner copy predates this writes no `turn` at all (see [Per-Group Copies Are Seeded Once](#per-group-copies-are-seeded-once)). A turn the CLI fails before it yields any message (`error_during_execution`) acknowledges nothing, so its result has `inputs: []` although it took its input. A turn can also fail after its prompt was acknowledged: a scheduled run's turn that reaches the runner's `maxTurns` (`error_max_turns`), or a message container's turn whose API stream breaks after a tool call, and again on the CLI's retry (`error_during_execution`). Its result reports `inputs: []` too, and no later result answers that input, because the CLI does not send it to the model again. It stays in the history once, where its turn left it, closed by the CLI's own `(no content)` message after a broken stream. A later request carries it only the way it carries every earlier prompt, so a later turn answers only what it took, and a slash command that makes no API call answers only itself. When a query ends, the runner logs every input no result answered (`Input no result answered`): never acknowledged, acknowledged with no result after it, or `its turn failed`. A message container's exit puts them back (below). A scheduled run's are not put back: only a scheduled run has `maxTurns`, and the task scheduler reads no `turn`. A run that stops on the cap is logged like any other, and what it left undone waits for a later run of the task (for a paper batch, one the queue sweeper schedules).
- **How the host settles.** A message container's open prompts keep their ids, oldest first. An output with `turn`, with text or without, marks the prompts it names answered, and the answered ones at the front are settled: the end of the last is the cursor a usage-limit refusal puts back to. A refused turn's inputs stay unanswered. `inputs: []` settles nothing: the nudge, a team turn, or a failed turn. A file an earlier container left is a prompt of the container that drains it: before a container starts, the host reads the `ref` of each input file waiting for it, which the runner drains all into its first prompt, and registers each range as an open prompt, oldest first, ahead of the stdin prompt. The refusal rollback, the exit put-back and a full rollback after an error all start from the oldest of them. An id that is no open prompt settles nothing either, and is logged. The session marker carries no `turn` and settles nothing.
- **Without `turn`.** The first output with `attribution: 'off'` (the guard above), or the first result with text and no `turn` (a runner that predates attribution, or an edited group copy), puts the container on the old rule for the rest of its run: each result with text settles the oldest open prompt, whichever it answered (files drained at start-up settle with the stdin prompt they were pushed with), and a textless one settles nothing. Any other textless output without `turn` is the session marker and changes nothing.
- **At exit.** When a message container's results carried `turn` to the end, its exit puts back every prompt no result answered: one it took, whether its turn failed or never ended, and one whose input file it never drained. The cursor goes back to the start of the oldest such prompt, the answered prompts after it stay answered (below), the input files after it that are still on disk are deleted, since their messages are pending again, and the queue retries. That holds after a clean exit and after an error, a reply already sent or not: a duplicate beats a silent loss. An undrained file is not left for the group's next container, which would start only on the user's next message: it is deleted, and the retry delivers its messages once. That includes a follow-up piped while the container winds down, after its run returned. A container left idle for `IDLE_TIMEOUT` after its last turn is closed: an output with `turn` starts that wait again like a result with text, a failed turn's textless result included, so a container whose last turn failed closes then, not at its hard timeout, and its input goes back then. The session marker carries no `turn` and does not start the wait. A prompt goes back once: the cursor an exit put back to is kept per group in `router_state` (`turn_put_back`). When the oldest prompt the next exit finds unanswered starts at that cursor, the retry failed it the same way: the exit logs an error and gives that prompt up. A newer prompt no result answered, taken or still in its file, never went back, so the cursor goes back to its start instead, its files are deleted as above, and that cursor is kept. With no newer one, the exit ends as below. An exit that leaves nothing unanswered forgets the group. When attribution was off (the runner said so, or a result with text came without `turn`), no result carried `turn`, or an output callback was still running when the run returned (it waits for them for `OUTPUT_SETTLE_TIMEOUT` only, so the outputs behind a stuck send are unread), the exit does what it did before: nothing after a clean exit; after an error, a full rollback, to the oldest file drained at start-up if there was one, unless a reply went out.
- **Answered ranges.** A put-back or a refusal moves the cursor back to the oldest prompt no turn answered, and keeps the answered prompts after it in `router_state` (`answered_ahead`, per group `[[from, to], ...]`). The message loop, recovery and a new container all read what is pending through one helper, `pendingMessages`, which leaves those ranges out: "m1 unanswered, m2 answered" runs only m1 again, and a turn that failed early in a long session reruns only its own input. A delivery moves the cursor past the newest message it reaches, answered or not. A run that ends without a refusal drops the ranges the cursor has passed; a refusal keeps them for the run after the reset.
- **A restart.** A restart stops the running containers. While a message container runs, `router_state` keeps the start of its oldest prompt no turn answered (`turn_open_from`), taken or still in its file, updated when a follow-up is piped and when prompts settle, with the answered prompts past it already in `answered_ahead`; its exit clears it. At start-up, once the old containers are stopped, the cursor goes back there, and an input file whose messages that makes pending is deleted, so the next container does not get them twice; a file behind the cursor stays for that container's start-up drain. A container that turned attribution off keeps none, so a restart puts nothing of it back, as before.
- **What remains.** A message stored late, with a timestamp inside an answered range, is left out like one behind the cursor. A scheduled run also drains a file an earlier container left into its first prompt, and reads no `turn`, so what it took there is not put back. A command that arrives alone while a turn runs is still piped at once, and can be merged into that turn as plain text. A question and a command sent within the same second still go as the command alone, without the question. Tool calls made before a refusal or a failed turn run again with the retry.

`container/agent-runner/test/turn-attribution-probe.test.ts` checks these cases against a fixture recorded from the compiled runner, offline, with the CLI the runner pins, and the reused uuid against that CLI driven through the SDK directly, since the runner never reuses one. After each of the two turns that fail past their acknowledgement, a follow-up answers with the history its request carried, which shows the failed prompt once, where its turn left it. The one after `maxTurns` is piped into a scheduled run, which the host never does (`GroupQueue.sendMessage` refuses a task container): it is there only to show what the CLI sends after the cap. No case makes the CLI inject its shutdown prompt, so the probe reads that prompt out of the CLI bundle and the test checks the runner takes it for a team prompt. One container runs the CLI through a `node` that drops `--replay-user-messages`, so the guard trips there with no switch in the runner: each of its outputs carries `attribution: 'off'`, from the textless result the guard trips on to a session marker and an error output. Two more containers measure what a usage-limit refusal leaves in the session ([Usage Limits](#usage-limits)): they sign in with a subscription token, since the CLI words a refusal only for one. It fails for any other SDK version, and for any runner source or probe other than the ones the fixture was recorded from (a hash of both). After a change to the runner or the probe, run `scripts/probe-turn-attribution.sh` again; after an SDK bump, rebuild the image first.

---

## Commands

### Commands Available in Any Group

| Command | Example | Effect |
|---------|---------|--------|
| `@Assistant [message]` | `@Andy what's the weather?` | Talk to Claude |

### Commands Available in Main Channel Only

| Command | Example | Effect |
|---------|---------|--------|
| `@Assistant add group "Name"` | `@Andy add group "Family Chat"` | Register a new group |
| `@Assistant remove group "Name"` | `@Andy remove group "Work Team"` | Unregister a group |
| `@Assistant list groups` | `@Andy list groups` | Show registered groups |
| `@Assistant remember [fact]` | `@Andy remember I prefer dark mode` | Add to global memory |

---

## Scheduled Tasks

PaperClaw has a built-in scheduler that runs tasks as full agents in their group's context.

### How Scheduling Works

1. **Group Context**: Tasks created in a group run with that group's working directory and memory
2. **Full Agent Capabilities**: Scheduled tasks have access to all tools (WebSearch, file operations, etc.)
3. **Optional Messaging**: Tasks can send messages to their group using the `send_message` tool, or complete silently
4. **Main Channel Privileges**: The main channel can schedule tasks for any group and view all tasks
5. **One Run per Due Time**: A task's `next_run` advances only when its run finishes, so every 60-second scheduler poll during a long run finds it due again. The queue skips a task that is already running or queued. A queued task is re-read when its turn comes, and does not run if it was paused or cancelled while it waited.
6. **Usage Limits**: A run refused by the usage limit sends nothing to the group. If no turn of it ended with an answer, its `next_run` moves to five minutes after the reset for that run only, and a `once` task stays `active` so that it runs then. A run that answered on one turn and was refused on another, in either order, is not run again, since that would send the answer twice: its run is logged as an error naming the limit, `next_run` follows the schedule, and a `once` task completes. What counts as an answer is defined under [Usage Limits](#usage-limits); an error message the CLI returns is not one. Output sent only through `send_message` is not seen, so a run that sent its work that way and was then refused still runs again in full. While a window is open, a due run starts no container: its `next_run` moves the same way, and its run log says the window was open. That time stands even if an answer closes the window sooner.

### Schedule Types

| Type | Value Format | Example |
|------|--------------|---------|
| `cron` | Cron expression | `0 9 * * 1` (Mondays at 9am) |
| `interval` | Milliseconds | `3600000` (every hour) |
| `once` | ISO timestamp | `2024-12-25T09:00:00Z` |

### Creating a Task

```
User: @Andy remind me every Monday at 9am to review the weekly metrics

Claude: [calls mcp__paperclaw__schedule_task]
        {
          "prompt": "Send a reminder to review weekly metrics. Be encouraging!",
          "schedule_type": "cron",
          "schedule_value": "0 9 * * 1"
        }

Claude: Done! I'll remind you every Monday at 9am.
```

### One-Time Tasks

```
User: @Andy at 5pm today, send me a summary of today's emails

Claude: [calls mcp__paperclaw__schedule_task]
        {
          "prompt": "Search for today's emails, summarize the important ones, and send the summary to the group.",
          "schedule_type": "once",
          "schedule_value": "2024-01-31T17:00:00Z"
        }
```

### Managing Tasks

From any group:
- `@Andy list my scheduled tasks` - View tasks for this group
- `@Andy pause task [id]` - Pause a task
- `@Andy resume task [id]` - Resume a paused task
- `@Andy cancel task [id]` - Delete a task

From main channel:
- `@Andy list all tasks` - View tasks from all groups
- `@Andy schedule task for "Family Chat": [prompt]` - Schedule for another group

---

## MCP Servers

### PaperClaw MCP (built-in)

The `paperclaw` MCP server is created dynamically per agent call with the current group's context.

**Available Tools:**
| Tool | Purpose |
|------|---------|
| `schedule_task` | Schedule a recurring or one-time task |
| `list_tasks` | Show tasks (group's tasks, or all if main) |
| `get_task` | Get task details and run history |
| `update_task` | Modify task prompt or schedule |
| `pause_task` | Pause a task |
| `resume_task` | Resume a paused task |
| `cancel_task` | Delete a task |
| `send_message` | Send a WhatsApp message to the group |

---

## Deployment

PaperClaw runs as a single macOS launchd service.

### Startup Sequence

When PaperClaw starts, it:
1. **Ensures container runtime is running** - Automatically starts it if needed; kills orphaned PaperClaw containers from previous runs
2. Initializes the SQLite database (migrates from JSON files if they exist)
3. Loads state from SQLite (registered groups, sessions, router state), puts each group's cursor back to the oldest prompt a stopped container left unanswered, and deletes the input files whose messages are pending again (see [Turn Attribution](#turn-attribution), "A restart")
4. Connects to WhatsApp (on `connection.open`):
   - Starts the scheduler loop
   - Starts the IPC watcher for container messages
   - Sets up the per-group queue with `processGroupMessages`
   - Recovers any unprocessed messages from before shutdown
   - Starts the message polling loop

### Service: com.paperclaw

**launchd/com.paperclaw.plist:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.paperclaw</string>
    <key>ProgramArguments</key>
    <array>
        <string>{{NODE_PATH}}</string>
        <string>{{PROJECT_ROOT}}/dist/index.js</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{{PROJECT_ROOT}}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{{HOME}}/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>{{HOME}}</string>
        <key>ASSISTANT_NAME</key>
        <string>Andy</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{{PROJECT_ROOT}}/logs/paperclaw.log</string>
    <key>StandardErrorPath</key>
    <string>{{PROJECT_ROOT}}/logs/paperclaw.error.log</string>
</dict>
</plist>
```

### Managing the Service

```bash
# Install service
cp launchd/com.paperclaw.plist ~/Library/LaunchAgents/

# Start service
launchctl load ~/Library/LaunchAgents/com.paperclaw.plist

# Stop service
launchctl unload ~/Library/LaunchAgents/com.paperclaw.plist

# Check status
launchctl list | grep paperclaw

# View logs
tail -f logs/paperclaw.log
```

---

## Security Considerations

### Container Isolation

All agents run inside containers (lightweight Linux VMs), providing:
- **Filesystem isolation**: Agents can only access mounted directories
- **Safe Bash access**: Commands run inside the container, not on your Mac
- **Network isolation**: Can be configured per-container if needed
- **Process isolation**: Container processes can't affect the host
- **Non-root user**: Container runs as unprivileged `node` user (uid 1000)

### Prompt Injection Risk

WhatsApp messages could contain malicious instructions attempting to manipulate Claude's behavior.

**Mitigations:**
- Container isolation limits blast radius
- Only registered groups are processed
- Trigger word required (reduces accidental processing)
- Agents can only access their group's mounted directories
- Main can configure additional directories per group
- Claude's built-in safety training

**Recommendations:**
- Only register trusted groups
- Review additional directory mounts carefully
- Review scheduled tasks periodically
- Monitor logs for unusual activity

### Credential Storage

| Credential | Storage Location | Notes |
|------------|------------------|-------|
| Claude CLI Auth | data/sessions/{group}/.claude/ | Per-group isolation, mounted to /home/node/.claude/ |
| WhatsApp Session | store/auth/ | Auto-created, persists ~20 days |

### File Permissions

The groups/ folder contains personal memory and should be protected:
```bash
chmod 700 groups/
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| No response to messages | Service not running | Check `launchctl list | grep paperclaw` |
| "Claude Code process exited with code 1" | Container runtime failed to start | Check logs; PaperClaw auto-starts container runtime but may fail |
| "Claude Code process exited with code 1" | Session mount path wrong | Ensure mount is to `/home/node/.claude/` not `/root/.claude/` |
| Session not continuing | Session ID not saved | Check SQLite: `sqlite3 store/messages.db "SELECT * FROM sessions"` |
| Session not continuing | Mount path mismatch | Container user is `node` with HOME=/home/node; sessions must be at `/home/node/.claude/` |
| "QR code expired" | WhatsApp session expired | Delete store/auth/ and restart |
| "No groups registered" | Haven't added groups | Use `@Andy add group "Name"` in main |

### Log Location

- `logs/paperclaw.log` - stdout
- `logs/paperclaw.error.log` - stderr

### Debug Mode

Run manually for verbose output:
```bash
npm run dev
# or
node dist/index.js
```
