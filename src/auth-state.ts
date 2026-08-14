import fs from 'fs';
import path from 'path';

import { STORE_DIR } from './config.js';
import { logger } from './logger.js';

/**
 * Exit code used when WhatsApp has revoked this linked device.
 *
 * 78 is sysexits' EX_CONFIG — "something is wrong with the configuration and a
 * human must fix it", which is exactly the situation. The systemd unit lists it
 * in `RestartPreventExitStatus`, so the process stops instead of being relaunched
 * forever: a logout is permanent, and reconnecting cannot undo it.
 */
export const AUTH_REQUIRED_EXIT_CODE = 78;

export const AUTH_REQUIRED_FILE = path.join(STORE_DIR, 'auth-required');

/**
 * Record that re-authentication is needed.
 *
 * The marker exists so the state is visible WITHOUT reading the log. In the
 * incident this comes from, a `device_removed` logout produced 221 restarts over
 * four hours and, from the outside, looked indistinguishable from "the bot is
 * quiet" — the only evidence was a `stream:error code 401` buried in a log the
 * restarts were busy appending to.
 */
export function markAuthRequired(reason?: unknown): void {
  try {
    fs.mkdirSync(STORE_DIR, { recursive: true });
    fs.writeFileSync(
      AUTH_REQUIRED_FILE,
      `${new Date().toISOString()} whatsapp logged out (reason=${String(reason ?? 'unknown')})\n` +
        're-authenticate: bash scripts/whatsapp-qr.sh\n',
    );
  } catch (err) {
    // Never let bookkeeping stop the process from reporting the logout.
    logger.warn({ err }, 'could not write the auth-required marker');
  }
}

/** Clear the marker once a connection succeeds. */
export function clearAuthRequired(): void {
  try {
    fs.rmSync(AUTH_REQUIRED_FILE, { force: true });
  } catch (err) {
    logger.warn({ err }, 'could not clear the auth-required marker');
  }
}

export function isAuthRequired(): boolean {
  return fs.existsSync(AUTH_REQUIRED_FILE);
}
