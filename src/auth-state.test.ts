import fs from 'fs';
import { afterEach, describe, expect, it } from 'vitest';

import {
  AUTH_REQUIRED_EXIT_CODE,
  AUTH_REQUIRED_FILE,
  clearAuthRequired,
  isAuthRequired,
  markAuthRequired,
} from './auth-state.js';

afterEach(() => fs.rmSync(AUTH_REQUIRED_FILE, { force: true }));

describe('auth-state', () => {
  it('uses an exit code the unit refuses to restart', () => {
    // Must match RestartPreventExitStatus in setup/service.ts, or the logout
    // loop comes straight back.
    expect(AUTH_REQUIRED_EXIT_CODE).toBe(78);
    const unit = fs.readFileSync('setup/service.ts', 'utf-8');
    expect(unit).toContain(
      `RestartPreventExitStatus=${AUTH_REQUIRED_EXIT_CODE}`,
    );
  });

  it('marks, reports and clears', () => {
    expect(isAuthRequired()).toBe(false);
    markAuthRequired(401);
    expect(isAuthRequired()).toBe(true);
    const body = fs.readFileSync(AUTH_REQUIRED_FILE, 'utf-8');
    expect(body).toContain('401');
    expect(body).toContain('whatsapp-qr.sh'); // tells the reader what to run
    clearAuthRequired();
    expect(isAuthRequired()).toBe(false);
  });

  it('clearing when nothing is marked is not an error', () => {
    expect(() => clearAuthRequired()).not.toThrow();
  });

  it('the watchdog refuses to restart while the marker exists', () => {
    const wd = fs.readFileSync('scripts/paperclaw-watchdog.sh', 'utf-8');
    expect(wd).toContain('auth-required');
    // The guard must come before any restart decision.
    expect(wd.indexOf('AUTH_MARKER')).toBeLessThan(
      wd.indexOf('is-active --quiet'),
    );
  });
});
