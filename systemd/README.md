# Service units

`paperclaw.service` runs the bot; `paperclaw-watchdog.{service,timer}` restart it
when its WhatsApp connection dies. The paper healer's units are in
`groups/main/research-papers/systemd/`.

## Install

`paperclaw.service` is symlinked into `~/.config/systemd/user/` by setup and
`/update` for the usual `~/paperclaw` checkout; any other path, or root, gets a copy
setup renders with the real paths, since the template's are all `%h/paperclaw`. The
watchdog units, `paperclaw-watchdog.{service,timer}`, are installed as SYMLINKS so
they can't drift; `/update` links them for that checkout only. To change a setting,
edit `.env` and restart; never edit the installed unit, which is the tracked file.

No systemd PATH has the right node, so `scripts/paperclaw-node.sh` resolves it:
`PAPERCLAW_NODE` first, which setup writes into a drop-in beside the unit
(`paperclaw.service.d/node.conf`, the node setup ran with, so the one that built
the native modules), then nvm's default, then PATH. A drop-in keeps the linked
unit a link.

The unit has no `EnvironmentFile=` on purpose: `.env` holds the secrets, and the
process environment reaches every child, the container runtime included. The
code reads its settings from `.env` itself (`src/config.ts`), and
`src/env-example.test.ts` fails for any key `.env.example` documents that does
not take effect that way.

## A restart rebuilds `dist/` first — and says so when it could not

The service runs `node dist/index.js`, and its `ExecStartPre=-… npm run build`
rebuilds `dist/` on every start; without it, a restart after a merge would relaunch
the OLD code while reporting `active` and a clean startup. The `-` lets a failed
build through, so the bot still comes up on the previous `dist/` — and
`src/build-freshness.ts` then logs **`dist/ is older than src/`** at startup,
naming the stale files. **That WARN means the running code is not the checked-out
code**: the build error is just above it in `logs/paperclaw.log`. Right after a
restart, read the build's own result instead, since node writes the WARN a moment
later: `systemctl --user show paperclaw -p ExecStartPre` must say `status=0`. The
WARN depends on `noEmitOnError` in `tsconfig.json`: by default tsc writes every
output despite a type error, so the bot would start on the NEW, failing code and
the WARN would never fire. How the same build stages the agent runner, and when
that copy goes live, is in [docs/SPEC.md](../docs/SPEC.md#per-group-copies-settings-and-runner).

## Timers

`paperclaw-watchdog.timer` and the healer's `paperclaw-qa-heal.timer` use wall-clock
`OnCalendar=` (`*:0/30`, `*:0/5`), where the next elapse is always computable and
`Persistent=true` actually means "catch up a missed run". Never combine
`OnBootSec=` + `OnUnitActiveSec=` with `Persistent=true`: `Persistent=` anchors on a
REALTIME stamp (`~/.local/share/systemd/timers/stamp-*.timer`) while those two
triggers are MONOTONIC (relative to the current boot). After a reboot systemd
cannot map the pre-reboot stamp onto the new boot's monotonic timeline, so the
timer sits with `NextElapseUSecMonotonic=infinity` — armed and dead. How to tell a
dead timer from a busy one is in `docs/DEBUG_CHECKLIST.md`.

## WhatsApp watchdog

The bot's WhatsApp socket (baileys) can drop with `Connection closed reason: 405` —
a connection failure, NOT a logout (the auth session is fine) — and get stuck in a
reconnect loop that eventually goes fully SILENT while systemd still reports it
`active (running)`, so nothing auto-recovers and WhatsApp just stops answering.
`systemctl --user restart paperclaw` reconnects instantly from the stored session.
So **`scripts/paperclaw-watchdog.sh` runs on a 30-minute timer** and restarts
paperclaw on three signals: (1) `logs/paperclaw.log` frozen ≥ 40 min, (2) a
reconnect loop (≥3 `Connection closed` with no `Connected to WhatsApp` after), or
(3) the last ≥3 heartbeats all report `whatsapp=down`. **Do NOT diagnose "no
WhatsApp reply" as a per-message problem — check the connection/service first**
(`systemctl --user status paperclaw`, tail `logs/paperclaw.log`). It does nothing while
`store/auth-required` exists: a logout needs a new QR, not a restart
(`docs/DEBUG_CHECKLIST.md`, "WhatsApp Auth Issues").

**A liveness check needs a signal the healthy process actively emits.** paperclaw
legitimately logs *nothing* for ~1h when idle, and also while a long paper batch's
subagent containers grind through NotebookLM, so log freshness alone restarts a
healthy service about hourly, and each restart detaches the batch's containers and
destroys the in-memory dispatcher loop that owns `papers_queue.json` (the
dispatcher pattern assumes the main agent stays alive for the whole batch and has
no restart-recovery of its own). `src/index.ts`
therefore emits an unconditional `heartbeat whatsapp=up|down` every 10 min, so the
log only goes stale when the event loop is *actually* hung; condition (3) reads the
heartbeat's WA status to still catch a dead-but-not-reconnecting socket (which
keeps the log fresh via heartbeats, so condition 1 can't see it). If a long batch
ever stalls, check whether a service restart orphaned it: `docker ps` (no paper
containers), `papers_queue.json` mtime old with `pending`/stuck-`in_progress`
entries, and `journalctl --user -u paperclaw-watchdog.service` for restart lines.

Those lines (the heartbeat, `Connected to WhatsApp`, `Connection closed`) are
written through `src/liveness-log.ts`, a child logger pinned at info, so a
`LOG_LEVEL=warn` in `.env` cannot mute them and bring the hourly restarts back.
`src/log-output.test.ts` runs them through pino-pretty at warn, error and silent
and checks them against the watchdog script's own grep patterns.
