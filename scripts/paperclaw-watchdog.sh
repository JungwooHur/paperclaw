#!/usr/bin/env bash
# Restart paperclaw if its WhatsApp connection has died.
#
# Why: the main service hung in a WhatsApp reconnect loop (repeated
# `Connection closed reason: 405`) and then went completely SILENT — no log for
# 13+ hours — while systemd still reported it `active (running)`. Nothing
# auto-recovered, so WhatsApp silently stopped answering until a human noticed.
#
# This watchdog (a 30-minute timer) catches the two shapes of that failure:
#   1. HUNG — the log file hasn't been written for STALE_MIN minutes (healthy
#      operation writes far more often; the 95th-percentile gap is ~10 min).
#   2. STUCK-DISCONNECTED — the recent log is a reconnect loop: several
#      "Connection closed" with no "Connected to WhatsApp" after them.
# Either way it runs `systemctl --user restart paperclaw`, which reconnects using
# the stored auth session (a 405 is a connection failure, not a logout).

LOG="${HOME}/paperclaw/logs/paperclaw.log"
STALE_MIN=40
TAIL_LINES=300

restart() {
    logger -t paperclaw-watchdog "restarting paperclaw: $1" 2>/dev/null || true
    echo "paperclaw-watchdog: restarting paperclaw ($1)"
    systemctl --user restart paperclaw
}

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

echo "paperclaw-watchdog: healthy (log ${age_min}m old, ${closes:-0} closes / ${conns:-0} reconnects)"
exit 0
