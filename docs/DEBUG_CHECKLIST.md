# PaperClaw Debug Checklist

## Known Issues (2026-02-08)

### 1. [FIXED] Resume branches from stale tree position
When agent teams spawns subagent CLI processes, they write to the same session JSONL. On subsequent `query()` resumes, the CLI reads the JSONL but may pick a stale branch tip (from before the subagent activity), causing the agent's response to land on a branch the host never receives a `result` for. **Fix**: pass `resumeSessionAt` with the last assistant message UUID to explicitly anchor each resume.

### 2. IDLE_TIMEOUT == CONTAINER_TIMEOUT (both 30 min)
Both timers fire at the same time, so containers always exit via hard SIGKILL (code 137) instead of graceful `_close` sentinel shutdown. The idle timeout should be shorter (e.g., 5 min) so containers wind down between messages, while container timeout stays at 30 min as a safety net for stuck agents.

### 3. Cursor advanced before agent succeeds
`processGroupMessages` advances `lastAgentTimestamp` before the agent runs. If the container times out, retries find no messages (cursor already past them). Messages are permanently lost on timeout.

## Quick Status Check

```bash
# 1. Is the service running?
launchctl list | grep paperclaw
# Expected: PID  0  com.paperclaw (PID = running, "-" = not running, non-zero exit = crashed)

# 2. Any running containers?
container ls --format '{{.Names}} {{.Status}}' 2>/dev/null | grep paperclaw

# 3. Any stopped/orphaned containers?
container ls -a --format '{{.Names}} {{.Status}}' 2>/dev/null | grep paperclaw

# 4. Recent errors in service log?
grep -E 'ERROR|WARN' logs/paperclaw.log | tail -20

# 5. Is WhatsApp connected? (look for last connection event)
# These lines are the app's own. baileys' lines ('connection errored' with its
# stack trace, 'logging in...') are logged only with BAILEYS_LOG_LEVEL=info.
grep -E 'Connected to WhatsApp|Connection closed|connection.*close' logs/paperclaw.log | tail -5

# 6. Are groups loaded?
grep 'groupCount' logs/paperclaw.log | tail -3
```

## Session Transcript Branching

```bash
# Check for concurrent CLI processes in session debug logs
ls -la data/sessions/<group>/.claude/debug/

# Count unique SDK processes that handled messages
# Each .txt file = one CLI subprocess. Multiple = concurrent queries.

# Check parentUuid branching in transcript
python3 -c "
import json, sys
lines = open('data/sessions/<group>/.claude/projects/-workspace-group/<session>.jsonl').read().strip().split('\n')
for i, line in enumerate(lines):
  try:
    d = json.loads(line)
    if d.get('type') == 'user' and d.get('message'):
      parent = d.get('parentUuid', 'ROOT')[:8]
      content = str(d['message'].get('content', ''))[:60]
      print(f'L{i+1} parent={parent} {content}')
  except: pass
"
```

## Container Timeout Investigation

```bash
# Check for recent timeouts
grep -E 'Container timeout|timed out' logs/paperclaw.log | tail -10

# Check container log files for the timed-out container
ls -lt groups/*/logs/container-*.log | head -10

# Read the most recent container log (replace path)
cat groups/<group>/logs/container-<timestamp>.log

# Check if retries were scheduled and what happened
grep -E 'Scheduling retry|retry|Max retries' logs/paperclaw.log | tail -10
```

## Agent Not Responding

```bash
# Check if messages are being received from WhatsApp
grep 'New messages' logs/paperclaw.log | tail -10

# Check if messages are being processed (container spawned)
grep -E 'Processing messages|Spawning container' logs/paperclaw.log | tail -10

# Check if messages are being piped to active container
grep -E 'Piped messages|sendMessage' logs/paperclaw.log | tail -10

# Check the queue state — any active containers?
grep -E 'Starting container|Container active|concurrency limit' logs/paperclaw.log | tail -10

# Is a Claude usage-limit window holding work? Times are UTC.
#   `usage_limited_until`        the window's reset: open while it holds a reset
#                                not yet two minutes past, when held messages
#                                resume. An answer that closes a window empties
#                                it; a window that runs out at its reset leaves
#                                that past reset in place
#   `usage_limit_announced`      the last reset the user was told about
#   `usage_limit_closed`         JSON {at, resetsAt}: when an answer last closed a
#                                window, and that window's reset (see docs/SPEC.md
#                                "Usage Limits"); cleared by the next refusal
#   `usage_limit_refused_input`  JSON per chat: input files a refusal put back;
#                                deleted, and the entry dropped, when that
#                                container exits or at the next startup
#   `usage_limit_refused_turn`   JSON per chat {session, ranges}: the session a
#                                refusal left its refused prompts in, and the
#                                message ranges they took; a delivery into that
#                                session marks those messages as sent again.
#                                Dropped once a run ends with the cursor past them
sqlite3 store/messages.db "SELECT key, value FROM router_state WHERE key LIKE 'usage_limit%';"
grep -E 'Usage limit|Task hit the usage limit' logs/paperclaw.log | tail -5

# Which messages are pending, and why (docs/SPEC.md "Turn Attribution").
# Timestamps are message cursors, and a range [from, to] holds the messages
# after its first cursor, up to its second. JSON per chat, except the first.
# A value of another shape (a hand edit) reads as missing, logged once.
#   `last_timestamp`             the newest message the message loop has seen
#   `last_agent_timestamp`       the message cursor: what is after it is pending
#   `answered_ahead`             ranges a turn already answered after the oldest
#                                unanswered prompt. Past the cursor after a
#                                put-back or a refusal: left out of what is
#                                pending. Behind it while a container runs
#                                (its pipes moved the cursor on): inert until a
#                                restart puts the cursor back. Dropped once a
#                                run ends with the cursor past them
#   `turn_open_from`             while a message container runs, the start of
#                                its oldest prompt no turn answered: a restart
#                                puts the cursor back there. Cleared at exit
#   `turn_put_back`              what an exit last put back, `[from, to]`: a
#                                prompt that starts at `from` and fails again
#                                is given up up to `to`, and what joined it
#                                after `to` goes back. A bare `from` (older)
#                                gives up the whole prompt
sqlite3 store/messages.db "SELECT key, value FROM router_state WHERE key IN ('last_timestamp', 'last_agent_timestamp', 'answered_ahead', 'turn_open_from', 'turn_put_back');"
grep -E 'putting it back|Restart stopped a container|not putting it back again|router_state value of the wrong shape' logs/paperclaw.log | tail -5

# Check lastAgentTimestamp vs latest message timestamp
sqlite3 store/messages.db "SELECT chat_jid, MAX(timestamp) as latest FROM messages GROUP BY chat_jid ORDER BY latest DESC LIMIT 5;"
```

## Container Mount Issues

```bash
# Check mount validation logs (shows on container spawn)
grep -E 'Mount validated|Mount.*REJECTED|mount' logs/paperclaw.log | tail -10

# Verify the mount allowlist is readable
cat ~/.config/paperclaw/mount-allowlist.json

# Check group's container_config in DB
sqlite3 store/messages.db "SELECT name, container_config FROM registered_groups;"

# Test-run a container to check mounts (dry run)
# Replace <group-folder> with the group's folder name
container run -i --rm --entrypoint ls paperclaw-agent:latest /workspace/extra/
```

## WhatsApp Auth Issues

```bash
# Check if QR code was requested (means auth expired)
grep 'QR\|authentication required\|qr' logs/paperclaw.log | tail -5

# Check auth files exist
ls -la store/auth/

# Re-authenticate if needed
npm run auth
```

## Service Management

```bash
# Restart the service
launchctl kickstart -k gui/$(id -u)/com.paperclaw

# View live logs
tail -f logs/paperclaw.log

# Stop the service (careful — running containers are detached, not killed)
launchctl bootout gui/$(id -u)/com.paperclaw

# Start the service
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.paperclaw.plist

# Rebuild after code changes
npm run build && launchctl kickstart -k gui/$(id -u)/com.paperclaw
```
