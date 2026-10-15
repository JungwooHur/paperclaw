import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * A LOG_LEVEL typo in .env must not take the bot down. pino throws on an
 * unknown level when the logger is built, which is at import time, so the
 * process would die before it could log why, and Restart=always would loop it.
 */
describe('LOG_LEVEL', () => {
  let tmp: string;
  const saved = {
    LOG_LEVEL: process.env.LOG_LEVEL,
    PAPERCLAW_ENV_FILE: process.env.PAPERCLAW_ENV_FILE,
  };

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-loglevel-'));
    delete process.env.LOG_LEVEL;
    process.env.PAPERCLAW_ENV_FILE = path.join(tmp, '.env');
    vi.resetModules();
  });

  afterEach(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it.each(['warning', 'DEBUG', 'verbose'])(
    'falls back to info on an unknown level in .env (%s)',
    async (level) => {
      fs.writeFileSync(path.join(tmp, '.env'), `LOG_LEVEL=${level}\n`);
      const m = await import('./logger.js');
      expect(m.LOG_LEVEL).toBe('info');
      expect(m.logger.level).toBe('info');
    },
  );

  it('falls back to info on an unknown level in the environment', async () => {
    process.env.LOG_LEVEL = 'loud';
    const m = await import('./logger.js');
    expect(m.logger.level).toBe('info');
  });

  it.each(['trace', 'warn', 'silent'])(
    'keeps a level pino knows (%s)',
    async (level) => {
      fs.writeFileSync(path.join(tmp, '.env'), `LOG_LEVEL=${level}\n`);
      const m = await import('./logger.js');
      expect(m.logger.level).toBe(level);
    },
  );
});
