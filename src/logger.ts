import pino from 'pino';

import { readEnvFile } from './env.js';

// The environment first, then .env, like every other setting (config.ts).
export const LOG_LEVEL =
  process.env.LOG_LEVEL || readEnvFile(['LOG_LEVEL']).LOG_LEVEL || 'info';

export const logger = pino({
  level: LOG_LEVEL,
  transport: { target: 'pino-pretty', options: { colorize: true } },
});

// Route uncaught errors through pino so they get timestamps in stderr
process.on('uncaughtException', (err) => {
  logger.fatal({ err }, 'Uncaught exception');
  process.exit(1);
});

process.on('unhandledRejection', (reason) => {
  logger.error({ err: reason }, 'Unhandled rejection');
});
