import os from 'os';
import path from 'path';

import { readEnvFile } from './env.js';
import { logger } from './logger.js';

// Every setting is read from the environment first, then from .env. The service
// loads no env file into its own environment — .env holds the secrets as well,
// and the process environment reaches every child process — so a setting read
// from process.env alone is one that .env silently ignores. Secrets are NOT read
// here: they stay on disk and are loaded only where needed (container-runner.ts).
// src/env-example.test.ts checks every key .env.example documents.
const SETTING_KEYS = [
  'ASSISTANT_NAME',
  'ASSISTANT_HAS_OWN_NUMBER',
  'AGENT_MODEL',
  'CONTAINER_IMAGE',
  'CONTAINER_TIMEOUT',
  'CONTAINER_MAX_OUTPUT_SIZE',
  'IDLE_TIMEOUT',
  'MAX_CONCURRENT_CONTAINERS',
  'OUTPUT_LANGUAGE',
  'TZ',
] as const;
const envConfig = readEnvFile([...SETTING_KEYS]);

function setting(key: (typeof SETTING_KEYS)[number]): string | undefined {
  return process.env[key] || envConfig[key];
}

export const ASSISTANT_NAME =
  setting('ASSISTANT_NAME') || 'Claude Paper Reviewer';
export const ASSISTANT_HAS_OWN_NUMBER =
  setting('ASSISTANT_HAS_OWN_NUMBER') === 'true';

// The agent's model. Left unset, the CLI bundled with the agent SDK picks its
// own default, which an account can lose access to: a token for a newer
// account got "There's an issue with the selected model" on every request,
// and that sentence went out over WhatsApp as the reply.
export const AGENT_MODEL = setting('AGENT_MODEL') || 'claude-sonnet-5-5';
// The bundled CLI also resolves the sonnet/opus/haiku aliases (a subagent's
// `model`, `/model haiku`) to the models it shipped with, so map those too.
export const AGENT_MODEL_ALIASES: Record<string, string> = {
  SONNET: 'claude-sonnet-5-5',
  OPUS: 'claude-opus-5-5',
  HAIKU: 'claude-haiku-5-5',
};
export const POLL_INTERVAL = 2000;
export const SCHEDULER_POLL_INTERVAL = 60000;

// Absolute paths needed for container mounts
const PROJECT_ROOT = process.cwd();
const HOME_DIR = process.env.HOME || os.homedir();

// Mount security: allowlist stored OUTSIDE project root, never mounted into containers
export const MOUNT_ALLOWLIST_PATH = path.join(
  HOME_DIR,
  '.config',
  'paperclaw',
  'mount-allowlist.json',
);
export const STORE_DIR = path.resolve(PROJECT_ROOT, 'store');
export const GROUPS_DIR = path.resolve(PROJECT_ROOT, 'groups');
export const DATA_DIR = path.resolve(PROJECT_ROOT, 'data');
export const MAIN_GROUP_FOLDER = 'main';

export const CONTAINER_IMAGE =
  setting('CONTAINER_IMAGE') || 'paperclaw-agent:latest';
export const CONTAINER_TIMEOUT = parseInt(
  setting('CONTAINER_TIMEOUT') || '1800000',
  10,
);
export const CONTAINER_MAX_OUTPUT_SIZE = parseInt(
  setting('CONTAINER_MAX_OUTPUT_SIZE') || '10485760',
  10,
); // 10MB default
export const IPC_POLL_INTERVAL = 1000;
export const IDLE_TIMEOUT = parseInt(setting('IDLE_TIMEOUT') || '1800000', 10); // 30min default — how long to keep container alive after last result
export const MAX_CONCURRENT_CONTAINERS = Math.max(
  1,
  parseInt(setting('MAX_CONCURRENT_CONTAINERS') || '5', 10) || 5,
);

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export const TRIGGER_PATTERN = new RegExp(
  `^@${escapeRegex(ASSISTANT_NAME)}\\b`,
  'i',
);

/**
 * TZ from the environment or .env, else the system zone. A name Intl does not
 * know falls back to the system zone with a warning: cron-parser would throw on
 * it for every scheduled task, and the container would get it too.
 */
function resolveTimezone(): string {
  const system = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tz = setting('TZ');
  if (!tz) return system;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return tz;
  } catch {
    logger.warn({ tz, system }, 'Unknown TZ, using the system timezone');
    return system;
  }
}

// The one timezone for everything: cron schedules, the host's own notices, the
// container's TZ, and Node's local time. Node takes its local zone from
// process.env.TZ, so the value is put there too — otherwise a TZ set in .env
// would govern cron while `new Date('2026-01-01T09:00')` (a 'once' task) still
// parsed in the system zone. TZ is not a secret; children seeing it is fine.
export const TIMEZONE = resolveTimezone();
process.env.TZ = TIMEZONE;

// Language of the notices the host sends on its own (the agent gets the same
// value through the container's secrets). Korean unless .env says otherwise.
export const OUTPUT_LANGUAGE = setting('OUTPUT_LANGUAGE') || 'ko';
