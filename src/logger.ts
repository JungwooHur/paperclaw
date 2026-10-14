import pino from 'pino';

import { readEnvFile } from './env.js';

// The environment first, then .env, like every other setting (config.ts).
const configuredLevel =
  process.env.LOG_LEVEL || readEnvFile(['LOG_LEVEL']).LOG_LEVEL;

// pino throws on a level it does not know, and it would throw here, at import,
// before anything could say why. A typo in .env must not take the bot down, so
// it logs at info and says so, as BAILEYS_LOG_LEVEL does (channels/whatsapp.ts).
const knownLevel = (level: string): boolean =>
  level === 'silent' || level in pino.levels.values;

export const LOG_LEVEL =
  configuredLevel && knownLevel(configuredLevel) ? configuredLevel : 'info';

export const logger = pino({
  level: LOG_LEVEL,
  transport: { target: 'pino-pretty', options: { colorize: true } },
});

if (configuredLevel && configuredLevel !== LOG_LEVEL) {
  logger.warn({ level: configuredLevel }, 'Unknown LOG_LEVEL, logging at info');
}

// Route uncaught errors through pino so they get timestamps in stderr
process.on('uncaughtException', (err) => {
  logger.fatal({ err }, 'Uncaught exception');
  process.exit(1);
});

process.on('unhandledRejection', (reason) => {
  logger.error({ err: reason }, 'Unhandled rejection');
});
