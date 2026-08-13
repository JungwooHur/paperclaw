#!/usr/bin/env bash
# Restart paperclaw if its WhatsApp connection has died.
#
# Why: the main service hung in a WhatsApp reconnect loop (repeated
# `Connection closed reason: 405`) and then went completely SILENT — no log for
# 13+ hours — while systemd still reported it `active (running)`. Nothing
# auto-recovered, so WhatsApp silently stopped answering until a human noticed.
#
# LIVENESS SIGNAL — heartbeat, not raw log freshness. paperclaw now emits
# `heartbeat whatsapp=up|down` every ~10 min (src/index.ts) whenever its event loop
# is alive. This exists because the ORIGINAL "log frozen for STALE_MIN" check had a
# false-positive: paperclaw legitimately writes nothing for ~1h when idle, and also
# while a long paper batch's subagent containers grind through NotebookLM — so the
# watchdog kept restarting a HEALTHY service ~hourly, and each restart detached the
# batch's containers and orphaned papers_queue.json (a 27-paper batch stalled with
# 12 papers never dispatched). With the heartbeat, a frozen log now reliably means an
# actually-hung event loop, and a dead-but-not-reconnecting socket shows up as
# `whatsapp=down` across heartbeats.
#
# This watchdog (a 30-minute timer) restarts on three shapes of failure:
#   1. HUNG — the log (kept fresh by the ~10-min heartbeat) hasn't been written for
#      STALE_MIN minutes → the event loop itself is stuck.
#   2. STUCK-DISCONNECTED — the recent log is a reconnect loop: several
#      "Connection closed" with no "Connected to WhatsApp" after them.
#   3. WA-DOWN — the last few heartbeats all report `whatsapp=down` (socket dead but
#      not even trying to reconnect, so #2's "Connection closed" lines never appear).
# Each runs `systemctl --user restart paperclaw`, which reconnects using the stored
# auth session (a 405 is a connection failure, not a logout).

LOG="${HOME}/paperclaw/logs/paperclaw.log"
# 40 min = 4 missed 10-min heartbeats — comfortably past any healthy gap now that the
# heartbeat keeps the log fresh, so this fires only on a genuinely hung event loop.
STALE_MIN=40
TAIL_LINES=300

restart() {
    logger -t paperclaw-watchdog "restarting paperclaw: $1" 2>/dev/null || true
    echo "paperclaw-watchdog: restarting paperclaw ($1)"
    systemctl --user restart paperclaw
}

# A revoked device needs a human with a phone, not a restart. Without this the
# watchdog kept calling `systemctl restart` every 30 minutes against a session
# WhatsApp had already thrown away, adding noise to the very log you have to read
# to find out what happened.
AUTH_MARKER="${HOME}/paperclaw/store/auth-required"
if [ -f "$AUTH_MARKER" ]; then
    logger -t paperclaw-watchdog "not restarting: WhatsApp needs re-authentication" 2>/dev/null || true
    echo "paperclaw-watchdog: WhatsApp logged out — re-authenticate with 'bash scripts/whatsapp-qr.sh'"
    exit 0
fi

# Only act when the unit is meant to be up and the log exists.
systemctl --user is-active --quiet paperclaw || exit 0
[ -f "$LOG" ] || exit 0

# 1) hung: log frozen for too long
now=$(date +%s)
mtime=$(stat -c %Y "$LOG")
age_min=$(( (now - mtime) / 60 ))
if [ "$age_min" -ge "$STALE_MIN" ]; then
    restart "log frozen ${age_min}m (>= ${STALE_MIN}m)"
    exit 0
fi

# 2) stuck disconnected: recent reconnect loop with no successful connect
recent=$(tail -n "$TAIL_LINES" "$LOG" 2>/dev/null)
closes=$(printf '%s\n' "$recent" | grep -c "Connection closed" 2>/dev/null || true)
conns=$(printf '%s\n' "$recent" | grep -c "Connected to WhatsApp" 2>/dev/null || true)
if [ "${closes:-0}" -ge 3 ] && [ "${conns:-0}" -eq 0 ]; then
    restart "stuck disconnected (${closes} closes, 0 reconnects in last ${TAIL_LINES} lines)"
    exit 0
fi

# 3) whatsapp down across the recent heartbeats: the event loop is alive (log is
# fresh, so #1 can't see it) but the socket is dead and not reconnecting (so #2's
# "Connection closed" lines never appear). Fire only when the last >=3 heartbeats are
# ALL `down`, so a brief reconnect blip (1-2 down) doesn't trip a needless restart.
hb=$(printf '%s\n' "$recent" | grep -oE "heartbeat whatsapp=(up|down)" | tail -4)
hb_count=$(printf '%s\n' "$hb" | grep -c "heartbeat whatsapp=" 2>/dev/null || true)
hb_up=$(printf '%s\n' "$hb" | grep -c "whatsapp=up" 2>/dev/null || true)
if [ "${hb_count:-0}" -ge 3 ] && [ "${hb_up:-0}" -eq 0 ]; then
    restart "whatsapp down across last ${hb_count} heartbeats"
    exit 0
fi

echo "paperclaw-watchdog: healthy (log ${age_min}m old, ${closes:-0} closes / ${conns:-0} reconnects, hb_up=${hb_up:-0}/${hb_count:-0})"
exit 0
