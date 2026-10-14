import { logger } from './logger.js';

/**
 * The lines scripts/paperclaw-watchdog.sh reads to decide whether the bot is
 * alive, kept in one place so log-output.test.ts can hold them to the
 * watchdog's own grep patterns.
 *
 * The watchdog restarts the bot when the log stops moving, when it shows a
 * reconnect loop, or when the heartbeats say WhatsApp is down. The bot is
 * legitimately quiet for an hour at a time, so the heartbeat is what keeps the
 * log moving; without it the watchdog restarted a healthy bot about hourly,
 * and each restart detached the running agents and orphaned the paper queue.
 *
 * So these are written whatever LOG_LEVEL says, through a child pinned at
 * info. With LOG_LEVEL=warn in .env and these at plain info, the log would go
 * silent and the watchdog would be back to restarting a healthy bot.
 */
let pinned: ReturnType<typeof logger.child> | undefined;
const liveness = () => (pinned ??= logger.child({}, { level: 'info' }));

/** Every HEARTBEAT_MS while the event loop is alive (index.ts). */
export function logHeartbeat(whatsappUp: boolean): void {
  liveness().info(`heartbeat whatsapp=${whatsappUp ? 'up' : 'down'}`);
}

export function logConnected(): void {
  liveness().info('Connected to WhatsApp');
}

export function logConnectionClosed(details: Record<string, unknown>): void {
  liveness().info(details, 'Connection closed');
}
