import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./logger.js', () => ({
  logger: { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() },
}));

import { resolveTimezone } from './config.js';
import { logger } from './logger.js';

function isZone(tz: unknown): boolean {
  if (typeof tz !== 'string' || !tz) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

describe('resolveTimezone', () => {
  beforeEach(() => vi.mocked(logger.warn).mockClear());

  it('keeps a configured zone Intl knows', () => {
    expect(resolveTimezone('Pacific/Chatham', () => 'Asia/Seoul')).toBe(
      'Pacific/Chatham',
    );
    expect(logger.warn).not.toHaveBeenCalled();
  });

  it('uses the system zone when nothing is configured', () => {
    expect(resolveTimezone(undefined, () => 'Asia/Seoul')).toBe('Asia/Seoul');
  });

  it('falls back to the system zone, with a warning, on an unknown one', () => {
    expect(resolveTimezone('KST-9', () => 'Asia/Seoul')).toBe('Asia/Seoul');
    expect(logger.warn).toHaveBeenCalledTimes(1);
  });

  it('uses UTC, with a warning, when the system zone cannot be named', () => {
    expect(resolveTimezone('KST-9', () => undefined)).toBe('UTC');
    expect(resolveTimezone(undefined, () => undefined)).toBe('UTC');
    expect(logger.warn).toHaveBeenCalledTimes(2);
  });
});

/**
 * `TZ=:/etc/localtime` (glibc for "the system zone") and POSIX strings such as
 * `KST-9` are valid to libc but are not names Intl knows, and under them Intl
 * reports no zone at all. The fallback once stored that `undefined` in
 * process.env.TZ as the string "undefined", which silently made the process's
 * local time, cron and the container's TZ all UTC.
 */
describe('an environment TZ Intl does not recognise', () => {
  const saved = process.env.TZ;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    if (saved === undefined) delete process.env.TZ;
    else process.env.TZ = saved;
  });

  it.each([':/etc/localtime', 'KST-9'])(
    '%s still yields a real zone, in TIMEZONE and in process.env.TZ',
    async (tz) => {
      process.env.TZ = tz;
      const { TIMEZONE } = await import('./config.js');
      expect(isZone(TIMEZONE)).toBe(true);
      expect(process.env.TZ).toBe(TIMEZONE);
    },
  );
});
