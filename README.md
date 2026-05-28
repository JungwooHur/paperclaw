# PaperClaw

A WhatsApp-based personal research assistant that grabs papers, translates them, files them into Notion, and answers questions about them — all from chat.

Built on the [NanoClaw](https://github.com/qwibitai/nanoclaw) framework (Claude Agent SDK + container isolation + WhatsApp transport). PaperClaw is NanoClaw with a research-papers brain.

```
You (WhatsApp)  →  PaperClaw  →  Claude Agent (container)  →  Notion
                       ↓                  ↓
                  scheduler         Semantic Scholar
                                    NotebookLM (translation)
                                    ar5iv (paper HTML + figures)
```

---

## What it does

- **Nightly collection** (default: 11:30 PM in your timezone) — fetches the latest papers from 60+ researchers you follow on Semantic Scholar, classifies them by field/lab/venue, translates Abstract + Method to your language, and posts the day's summary to your WhatsApp.
- **On-demand requests** — send a message like `add this paper: https://arxiv.org/abs/2501.12345` and the agent dispatches a background subagent that creates the Notion page, extracts figures, and translates the paper.
- **Parallel processing** — send multiple papers in one message (or one after another). The agent processes up to 3 in parallel.
- **Q&A** — ask follow-up questions about a paper; the answer is saved back to the paper's Notion page as a toggle callout so you can revisit later.
- **Researcher discovery** — `"what papers has Professor Marco Hutter put out recently?"` triggers a Semantic Scholar lookup and offers to add the papers.

---

## Prerequisites

Before you start, you need accounts on:

| Service | Why | Cost |
|---|---|---|
| **Anthropic** (Claude) | The agent's brain | Pay-per-use OR Claude Code subscription |
| **Notion** | Where papers are filed | Free tier is fine |
| **Google** (for NotebookLM) | Translation (saves Claude tokens) | Free |
| **WhatsApp** | The chat interface | Free |

And on your machine:

- **Node.js 22+** (the setup script can install via nvm/brew if missing)
- **Docker** (or Apple Container on macOS) for agent isolation
- **Python 3.10+** for the paper collection scripts
- ~2 GB free disk for the container image + session data

---

## Setup

> One-time setup takes ~30 min if you have the prerequisite accounts ready, ~90 min if you're creating Notion/Google accounts from scratch.

### 1. Clone and install

```bash
git clone https://github.com/JungwooHur/paperclaw.git
cd paperclaw
./setup.sh         # installs Node deps, validates native modules, builds the container image
```

If you'd rather drive the setup interactively from inside Claude Code, run `/setup` instead — it walks you through every step with prompts.

### 2. Copy and edit `.env`

```bash
cp .env.example .env
$EDITOR .env
```

You'll fill in the values in the following steps.

### 3. Authenticate Claude

Pick one:

```bash
# Option A — long-lived OAuth token (recommended if you have a Claude Code subscription)
claude setup-token
# paste the printed token into CLAUDE_CODE_OAUTH_TOKEN in .env

# Option B — pay-per-use API key
# Get one at https://console.anthropic.com → API Keys
# Set ANTHROPIC_API_KEY in .env
```

### 3.5 Pick an output language

PaperClaw uses NotebookLM to process each paper section. The behavior depends on `OUTPUT_LANGUAGE` in your `.env`:

| `OUTPUT_LANGUAGE` | What happens to paper sections | Notion column names |
|---|---|---|
| `ko` *(default)* | Translated into Korean | Korean — see `groups/main/CLAUDE.md` for the exact strings |
| `en` | **No translation.** Reformatted into Notion-friendly English: headings, bullets, equations preserved as plain text, reference citations / page furniture stripped. Use this if you read papers natively in English. | `Field`, `Lab/Institution` |
| `ja`, `zh-CN`, `de`, `fr`, `es`, ... | Translated into that language | `Field`, `Lab/Institution` |

Set this in `.env` *before* you run the Notion bootstrap step below — the bootstrap script picks the column-name set based on `OUTPUT_LANGUAGE` (Korean for `ko`, English for everything else). You can change it later by renaming columns in Notion and updating the value in `.env`.

### 4. Set up Notion

**4a. Create an integration token.**

Go to https://www.notion.so/my-integrations → "New integration" → give it a name (e.g. "PaperClaw") → submit → copy the "Internal Integration Token". Paste into `NOTION_TOKEN` in `.env`.

**4b. Pick or create a parent Notion page** that will hold the papers database. Open it, click "•••" (top right) → "Connections" → add your PaperClaw integration. Copy the page ID from the URL — it's the 32-char hex string at the end (`notion.so/<workspace>/Page-Name-<THIS-PART>`).

**4c. Create the papers database.**

```bash
NOTION_PARENT_PAGE_ID=<page-id> npx tsx setup/bootstrap-notion.ts
```

This creates a database with the exact schema PaperClaw expects (see [Notion DB Schema](#notion-db-schema) below) and writes `NOTION_RESEARCH_DB=<new-id>` into your `.env` automatically.

> Prefer to do it by hand? Skip the script and create the database in Notion's UI with the columns listed in the [Notion DB Schema](#notion-db-schema) section, then paste the DB ID into `NOTION_RESEARCH_DB`.

### 5. Authenticate NotebookLM (Google)

PaperClaw uses NotebookLM for whole-paper translation because it's much cheaper in tokens than asking Claude to do it. The login is a one-time browser flow:

```bash
notebooklm login         # opens a browser, sign in with your Google account
# Auth state is stored in ~/.notebooklm/storage_state.json
```

If the `notebooklm` CLI isn't installed yet, install it from [notebooklm-py](https://github.com/teng-lin/notebooklm-py) (a tiny Python wrapper around the NotebookLM web app).

> Want to skip NotebookLM? You can — the agent falls back to translating with Claude directly. It just costs more tokens.

### 6. Authenticate WhatsApp

```bash
npx tsx setup/index.ts --step whatsapp-auth
```

Scan the printed QR with your phone (WhatsApp → Settings → Linked devices → Link a device), or use the pairing-code flow.

### 7. Find your chat JID and register the main group

```bash
npx tsx setup/index.ts --step groups       # lists available chats
```

Pick the chat that should be your PaperClaw inbox and paste its JID into `CHAT_JID` in `.env`. For an individual it looks like `821012345678@s.whatsapp.net`; for a group, `120363xxxxxxxxx@g.us`.

> **Use a 1-on-1 self-chat for `CHAT_JID`.** The main group runs every incoming message through the agent without a trigger word, and the agent has `$NOTION_TOKEN` plus Bash + network access. Anyone who can send a message to the main chat can drive it — so a shared WhatsApp group is unsafe. Other people you trust can be added later as non-main groups (they require an `@AssistantName` trigger and have no admin privileges). See `docs/SECURITY.md` for the full trust model.

Then register the group:

```bash
npx tsx setup/index.ts --step register
```

### 8. Start the service

```bash
npx tsx setup/index.ts --step service      # installs systemd unit / launchd plist
systemctl --user start paperclaw           # Linux
# launchctl load ~/Library/LaunchAgents/com.paperclaw.plist   # macOS
```

Verify it's up:

```bash
systemctl --user status paperclaw          # Linux
launchctl list | grep paperclaw            # macOS
tail -f logs/paperclaw.log
```

Send any message to your registered chat. The agent should reply within a few seconds.

### 9. Schedule the nightly collection

```bash
npx tsx setup/create-research-task.ts      # registers the 11:30 PM cron job
npx tsx setup/trigger-research-now.ts      # optional: run it once now to test
```

---

## Notion DB Schema

If you use `bootstrap-notion.ts`, this schema is created for you. If you create the database by hand, use **exactly these column names and types** — the agent looks them up by name.

| Column | Type | Notes |
|---|---|---|
| `Paper Pages` | Title | The paper title |
| `Paper URL` | URL | arXiv / DOI link (used for dedup) |
| `Authors` | Rich text | Comma-separated |
| `Year` | Number | Publication year |
| `Field` | Multi-select | Field tags (RL, VLA, Control, ...) |
| `Lab/Institution` | Multi-select | Lab / institution |
| `Journal, Conference` | Select | TRO, ICRA, NeurIPS, ... (empty for arXiv-only) |

The names above apply when `OUTPUT_LANGUAGE` is set to anything other than `ko`. With `OUTPUT_LANGUAGE=ko` (the default), the bootstrap script creates the two multi-select columns under Korean names instead — see `groups/main/CLAUDE.md` for the exact strings. If you rename columns after creation, also update `groups/main/CLAUDE.md` (the schema reference) and `groups/main/research-papers/collect_papers.py` (the dedup queries) to match.

---

## Configuring researchers and topics

The list of researchers PaperClaw follows lives at `groups/main/research-papers/config.json`:

```json
{
  "researchers": ["Marco Hutter", "Sergey Levine", "..."],
  "s2AuthorIds": { "Marco Hutter": "1234567" },
  "researcherLabMap": { "Marco Hutter": "ETH RSL" },
  "topics": [
    { "name": "RL",  "query": "(legged robot) AND (reinforcement learning OR RL)" },
    { "name": "VLA", "query": "(robot) AND (vision language action OR VLA)" }
  ]
}
```

**To add a researcher:** append their name to `researchers`. On the next nightly run, if `s2AuthorIds` doesn't have an entry for them, the collector queries Semantic Scholar for the author and caches the ID. Add the lab affiliation to `researcherLabMap` if you want their papers auto-tagged.

**To change topic queries:** edit the `topics` array. Queries use Semantic Scholar's bulk-search syntax.

---

## Adapting to a different field or language

PaperClaw ships with robotics defaults and Korean translation. If your situation differs:

**Different language:** set `OUTPUT_LANGUAGE` in `.env` (see [step 3.5](#35-pick-an-output-language)). Run `bootstrap-notion.ts` *after* setting it so the column names match. No code changes needed.

**Different field (NLP, biology, physics, etc.):**

1. **`groups/main/research-papers/config.json`** — replace `researchers`, `topics`, and `researcherLabMap` with your field's authors, queries, and labs.
2. **`groups/main/CLAUDE.md`** — the "Classification Guidelines" section lists field tags and venue abbreviations. Swap robotics venues (TRO, ICRA, CoRL) for yours (ACL, EMNLP for NLP; Nature, Cell for bio; PRL for physics; etc.).
3. **Notion DB tag values** — the `Field` column is multi-select; just use your own tag values (you don't need to recreate the column).
4. **Nightly task prompt** — `setup/create-research-task.ts` has a long prompt template with field examples. Adjust the example tags to your field.

Nothing in `src/` or the container code is robotics- or language-specific. The domain knowledge lives entirely in `groups/main/CLAUDE.md` and `groups/main/research-papers/config.json`.

---

## Day-to-day use

Send any of these to your registered WhatsApp chat:

| You send | What happens |
|---|---|
| `add this paper: https://arxiv.org/abs/2501.12345` | Single paper added to Notion with translation + figures |
| 3 URLs in one message | All 3 processed in parallel (subagents) |
| `what papers has Professor Marco Hutter put out recently?` | S2 search, prints list, asks if you want to add |
| `how did [paper title] design the reward?` | NotebookLM Q&A; answer saved as a toggle on the paper's Notion page |
| A PDF attachment with a caption | Treated as a paper to add |

The nightly job fires at 11:30 PM (your `TZ`) and posts a summary message with the day's haul broken down by field and lab.

---

## Updating from upstream

PaperClaw tracks upstream NanoClaw. To pull the latest framework changes while keeping your customizations:

```bash
# In Claude Code:
/update
# Or manually:
git fetch upstream main
git merge upstream/main          # resolve conflicts in your customized files
npm install && ./container/build.sh
```

`/update` is recommended — it rebases your skill customizations on top of upstream changes and runs migrations.

---

## Customizing further

Use `/customize` inside Claude Code to add channels (Telegram, Slack, email input), swap WhatsApp for another transport, change the trigger pattern, etc. The skill walks you through the change and writes the code.

For ad-hoc tweaks, the key files to look at are:

| File | What it controls |
|---|---|
| `groups/main/CLAUDE.md` | Agent instructions for the paper workflow |
| `groups/main/research-papers/config.json` | Researchers, topics, lab map |
| `src/config.ts` | Container limits, timeouts, image tag |
| `setup/create-research-task.ts` | Nightly cron, prompt template |
| `container/agent-runner/src/index.ts` | Agent runtime, tool allowlist |

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Agent doesn't reply | `tail -f logs/paperclaw.log`; `docker ps` (or `container list` on macOS) |
| `Notion 401` despite valid token | Often the integration isn't connected to the parent page or DB. Re-share via Notion → "•••" → Connections |
| Notion 429 / rate limit | Lower `PARALLEL_PAPER_CONCURRENCY` in `groups/main/CLAUDE.md` (default 3) |
| Container fails to spawn | `./container/build.sh` to rebuild; check `CONTAINER_IMAGE` matches your local image tag |
| Nightly runs but no papers | `cd groups/main/research-papers && python3 collect_papers.py --fetch-only` to test S2 fetching standalone |

`/debug` inside Claude Code is the most thorough troubleshooter — it knows the architecture and can read the logs for you.

---

## Architecture (one paragraph)

A single Node.js process (`src/index.ts`) holds the WhatsApp connection and an in-memory router. Inbound messages are written to a per-group IPC directory and the orchestrator either spawns a fresh agent container or pipes the message into an already-running one for that group (groups serialize; different groups run in parallel up to `MAX_CONCURRENT_CONTAINERS`). Each container runs a Claude Agent SDK loop with a per-group `CLAUDE.md` providing the system prompt. Agents can spawn background subagents via the `Task` tool — that's how parallel paper processing works. Outbound messages go back through IPC to the orchestrator, which sends them via Baileys (WhatsApp). The scheduler (`src/task-scheduler.ts`) reads cron entries from SQLite and triggers container runs at scheduled times.

More detail in [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).

---

## Credits

PaperClaw is built on top of the **[NanoClaw](https://github.com/qwibitai/nanoclaw)** framework by Gavriel. NanoClaw provides the container runtime, WhatsApp transport, skills engine, and update machinery; PaperClaw is the research-papers workload layered on top. Pulling improvements from upstream NanoClaw is supported via the `/update` skill.

## License

MIT — see [LICENSE](LICENSE). PaperClaw inherits NanoClaw's MIT license; please preserve the original copyright notice when forking.
