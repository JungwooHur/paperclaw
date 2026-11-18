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

- **No WhatsApp reply at all:** start with the connection and the service, as
  `systemd/README.md` ("WhatsApp watchdog") says, then `store/auth-required` under
  WhatsApp Auth Issues.
- **The bot answers with an error sentence:** the agent runner forwards every SDK
  result as `status: 'success'`, including ones with `is_error: true`, so to the
  host an error looks like a reply. The usage-limit refusal is held and resumed
  after the reset (docs/SPEC.md, "Usage Limits"); **any other `is_error` result is
  still sent as the reply**, and an error sentence as the answer means it took this
  path. `There's an issue with the selected model (...)` means the CLI bundled with
  the SDK fell back to a default model the token cannot use; the model is pinned
  through `AGENT_MODEL` (docs/SPEC.md, "Claude Authentication").

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

**If WhatsApp is silent, check `store/auth-required` first** — if it is there, no
amount of restarting will help. `Connection closed reason: 401` with
`shouldReconnect: false` and a `<conflict type="device_removed"/>` stream error
means WhatsApp **revoked this linked device**. It is not the 405 disconnect the
watchdog restarts on: the stored session is dead server-side, and only a human
re-scanning a QR brings it back. On a logout the process writes
`store/auth-required` and exits **78**, the unit carries
`RestartPreventExitStatus=78` so it stops instead of looping, the watchdog
short-circuits while that marker exists, and a successful connection clears it.

Re-authenticate with **`bash scripts/whatsapp-qr.sh`**. Two traps it handles: a
revoked session still has `"registered": true` on disk, so `npm run auth` prints
"Already authenticated" and exits without ever showing a QR until the old state is
moved aside; and `qrcode-terminal` draws two characters per module, so a
version-11 WhatsApp QR needs ~130 columns and is silently truncated — unreadable —
in an 80-column terminal. The script moves the dead session aside (renames, never
deletes), renders the same code in half-blocks at 65x33, and re-renders on each
~20-30 s rotation.

```bash
# Check if QR code was requested (means auth expired)
grep 'QR\|authentication required\|qr' logs/paperclaw.log | tail -5

# Check auth files exist, and whether a logout left its marker
ls -la store/auth/
ls -la store/auth-required

# Re-authenticate if needed
bash scripts/whatsapp-qr.sh
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

## Healer or Watchdog Not Running

**When a healer "isn't applying", check `systemctl --user list-timers` FIRST** — a
blank `NEXT` can mean it has not been running at all, and no amount of debugging
the healer code will explain the symptom. A dead timer fails silently:
`systemctl --user is-active` says `active`, `is-enabled` says `enabled`, the unit
files are correctly symlinked, and nothing is logged. **Only `list-timers` shows
it — `NEXT` is `-`.** The cause, and why both timers use wall-clock `OnCalendar=`
(`*:0/5`, `*:0/30`), is in `systemd/README.md`, "Timers".

Read `NEXT` together with `LAST`, because a busy timer looks identical in that one
column. systemd will not start a second instance of a service that is already
running, so **while the triggered unit is active the timer has no armed next
elapse and `list-timers` prints `-` for NEXT**. `LAST` is what separates them: dead
means LAST is old or never, saturated means LAST is seconds ago.

```bash
systemctl --user list-timers 'paperclaw-*'     # NEXT must be a real timestamp, or LAST seconds ago
ls -la ~/.local/share/systemd/timers/          # stamp mtime = last real trigger
systemctl --user show <timer> -p LastTriggerUSec    # the direct read of LAST
systemctl --user show <service> -p ActiveState      # activating = mid-run
```

## CI Failures

**When CI fails on code nobody touched, compare the `Runner Image` lines of the
last green run and the red one** (`gh run view <id> --log | grep -A2 "Runner Image"`).
`ubuntu-latest` moves under the repo: on Ubuntu 26.04, diffutils 3.12 reads
`diff -ruN <dir> <file>` as the directory's same-named entry against the file —
exit 1 and a patch, where 3.10 exited 2 — and a test that leaned on the error went
red on every branch at once. Make the code decide for itself rather than read a
system tool's exit code.

**A git command run under a hook acts on the repository the hook runs in.** Git
exports `GIT_DIR`, `GIT_INDEX_FILE` and friends to every hook it launches, and
**they override `cwd`**, so a test's `execSync('git ...', { cwd: tempDir })` under
`.husky/pre-push` acts on the real repository: it rewrites that repository's
`user.name` and `user.email` to the fixture's placeholders, and a bare
`git init --bare` with no directory argument flips it to `core.bare=true`.
`vitest.setup.ts` deletes those variables before any test file loads, and
`tests/leak_helpers.py` runs the leak-gate tests' git without them. **The symptoms
do not look like a test problem.** A repository that has been flipped bare answers
`git status` with `fatal: this operation must be run in a work tree` while
`git branch --show-current` still works, the next commit is blocked by
`check-sensitive.sh` for an author email nobody typed, and the index can hold
hundreds of phantom staged deletions.
