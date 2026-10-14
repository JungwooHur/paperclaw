import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Every key .env.example documents must do what it says when it is set in .env.
 *
 * The service loads no env file into its environment (on purpose: .env holds
 * the secrets, and the process environment reaches every child process), so a
 * knob read only from process.env is a knob that .env silently ignores. Most of
 * the runtime knobs were exactly that once: TZ, LOG_LEVEL and the container
 * timeouts were documented here and had no effect.
 *
 * The list of keys comes from .env.example itself, so a key added there without
 * either being wired up (KNOBS) or explained (CONSUMED_ELSEWHERE) fails here.
 */

const EXAMPLE = fileURLToPath(new URL('../.env.example', import.meta.url));

/** Keys .env.example documents, commented-out ones included. */
function documentedKeys(): string[] {
  const keys = new Set<string>();
  for (const line of fs.readFileSync(EXAMPLE, 'utf-8').split('\n')) {
    const m = line.match(/^#?\s*([A-Z][A-Z0-9_]*)=/);
    if (m) keys.add(m[1]);
  }
  return [...keys];
}

interface Knob {
  /** The value written to the temp .env. */
  value: string;
  /** What the code must then report; defaults to `value`. */
  expected?: unknown;
  /** Reads the effective setting from a freshly imported module. */
  read: () => Promise<unknown>;
}

/** Keys that take effect on the host process when set only in .env. */
const KNOBS: Record<string, Knob> = {
  TZ: {
    value: 'Pacific/Chatham',
    // One value for everything: the exported zone (cron, notices, the
    // container's TZ) and Node's own local time (Date parsing, formatting).
    expected: ['Pacific/Chatham', 'Pacific/Chatham'],
    read: async () => [
      (await import('./config.js')).TIMEZONE,
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    ],
  },
  LOG_LEVEL: {
    value: 'debug',
    expected: ['debug', 'debug'],
    read: async () => {
      const m = await import('./logger.js');
      return [m.LOG_LEVEL, m.logger.level];
    },
  },
  BAILEYS_LOG_LEVEL: {
    value: 'error',
    read: async () =>
      (await import('./channels/whatsapp.js')).baileysLogLevel(),
  },
  CONTAINER_TIMEOUT: {
    value: '1234567',
    expected: 1234567,
    read: async () => (await import('./config.js')).CONTAINER_TIMEOUT,
  },
  IDLE_TIMEOUT: {
    value: '7654321',
    expected: 7654321,
    read: async () => (await import('./config.js')).IDLE_TIMEOUT,
  },
  MAX_CONCURRENT_CONTAINERS: {
    value: '9',
    expected: 9,
    read: async () => (await import('./config.js')).MAX_CONCURRENT_CONTAINERS,
  },
  CONTAINER_IMAGE: {
    value: 'agent-under-test:probe',
    read: async () => (await import('./config.js')).CONTAINER_IMAGE,
  },
  OUTPUT_LANGUAGE: {
    value: 'ja',
    read: async () => (await import('./config.js')).OUTPUT_LANGUAGE,
  },
  ASSISTANT_NAME: {
    value: 'ProbeBot',
    read: async () => (await import('./config.js')).ASSISTANT_NAME,
  },
  ASSISTANT_HAS_OWN_NUMBER: {
    value: 'true',
    expected: true,
    read: async () => (await import('./config.js')).ASSISTANT_HAS_OWN_NUMBER,
  },
  AGENT_MODEL: {
    value: 'model-under-test',
    read: async () => (await import('./config.js')).AGENT_MODEL,
  },
  EXTRA_SKILLS_DIRS: {
    value: '/srv/skills-a:/srv/skills-b',
    read: async () =>
      (await import('./skill-sync.js')).extraSkillsDirsSetting(),
  },
};

/**
 * Keys the host process deliberately does not act on itself, and why. A
 * 'container secret' is checked against the list the container is really
 * handed, so the reason cannot quietly stop being true.
 */
const CONSUMED_ELSEWHERE: Record<string, { reason: string; secret?: true }> = {
  CLAUDE_CODE_OAUTH_TOKEN: {
    reason: 'container secret: passed to the agent on stdin',
    secret: true,
  },
  ANTHROPIC_API_KEY: {
    reason: 'container secret: passed to the agent on stdin',
    secret: true,
  },
  NOTION_TOKEN: {
    reason: 'container secret: passed to the agent on stdin',
    secret: true,
  },
  NOTION_RESEARCH_DB: {
    reason:
      'container secret: passed to the agent on stdin; setup/bootstrap-notion.ts writes it',
    secret: true,
  },
  CHAT_JID: {
    reason:
      'setup only: setup/create-research-task.ts and setup/trigger-research-now.ts',
  },
};

describe('.env.example', () => {
  it('documents no key that is neither a knob nor consumed elsewhere', () => {
    const unclassified = documentedKeys().filter(
      (k) => !(k in KNOBS) && !(k in CONSUMED_ELSEWHERE),
    );
    expect(unclassified).toEqual([]);
  });

  it('has no stale entries in either map', () => {
    const documented = new Set(documentedKeys());
    const stale = [
      ...Object.keys(KNOBS),
      ...Object.keys(CONSUMED_ELSEWHERE),
    ].filter((k) => !documented.has(k));
    expect(stale).toEqual([]);
  });

  it('hands every key it calls a container secret to the container', async () => {
    const { CONTAINER_SECRET_KEYS } = await import('./container-runner.js');
    const secrets = Object.entries(CONSUMED_ELSEWHERE)
      .filter(([, v]) => v.secret)
      .map(([k]) => k);
    expect(CONTAINER_SECRET_KEYS).toEqual(expect.arrayContaining(secrets));
  });
});

describe('a knob set only in .env takes effect', () => {
  let tmp: string;
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-env-'));
    // The service's own path: <cwd>/.env, with no override. The suite points
    // PAPERCLAW_ENV_FILE at nothing (vitest.setup.ts); this gate must not.
    saved.PAPERCLAW_ENV_FILE = process.env.PAPERCLAW_ENV_FILE;
    delete process.env.PAPERCLAW_ENV_FILE;
    vi.spyOn(process, 'cwd').mockReturnValue(tmp);
    vi.resetModules();
  });

  afterEach(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
      delete saved[key];
    }
    vi.restoreAllMocks();
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it.each(Object.keys(KNOBS))('%s', async (key) => {
    const knob = KNOBS[key];
    saved[key] = process.env[key];
    delete process.env[key];
    fs.writeFileSync(path.join(tmp, '.env'), `${key}=${knob.value}\n`);

    expect(await knob.read()).toEqual(knob.expected ?? knob.value);
  });
});
