/**
 * Keeps Signal session keys out of the log.
 *
 * libsignal (the Signal implementation under baileys) logs whole session
 * objects through console.info and console.warn — "Closing session:",
 * "Opening session:", "Removing old closed session:" — and offers no logger
 * hook to turn that off. Node prints such an object with util.inspect, which
 * expands every Buffer in it: the ratchet's private key, the root key, the
 * chain and message keys. The service's stdout is appended to
 * logs/paperclaw.log, so every session change wrote live key material to disk.
 *
 * SessionEntry does define an inspect() method meant to prevent exactly this,
 * but Node stopped calling a plain inspect() long ago; only util.inspect.custom
 * counts. This module defines that on the SessionEntry prototype.
 *
 * Import it first in every entry point that opens a baileys socket. It depends
 * on nothing but Node itself, so it is safe to load before the app logger.
 */
import { createRequire } from 'module';
import util from 'util';

interface SessionEntryLike {
  indexInfo?: { closed?: number };
}

/**
 * Makes util.inspect print a libsignal SessionEntry as one line with no key
 * material. Returns false, patching nothing, when `libsignal` does not have
 * the shape this relies on.
 */
export function redactSessionEntries(libsignal: unknown): boolean {
  const record = (
    libsignal as { SessionRecord?: { createEntry?: unknown } } | undefined
  )?.SessionRecord;
  if (typeof record?.createEntry !== 'function') return false;
  const proto: unknown = Object.getPrototypeOf(record.createEntry());
  // A plain object back means there is no SessionEntry class to patch, and
  // Object.prototype must never be touched.
  if (!proto || proto === Object.prototype) return false;
  Object.defineProperty(proto, util.inspect.custom, {
    configurable: true,
    value(this: SessionEntryLike): string {
      // closed is a timestamp (-1 while open): useful, and not a secret.
      return `SessionEntry <redacted closed=${this.indexInfo?.closed}>`;
    },
  });
  return true;
}

// Resolve libsignal from baileys' own location: that is the copy baileys
// loads, whether or not npm hoisted it to the top of node_modules. A lookup
// that fails only warns, like an unrecognised shape: the service imports this
// before baileys, so a throw here would keep it from starting at all.
let patched = false;
try {
  const require = createRequire(import.meta.url);
  patched = redactSessionEntries(
    createRequire(require.resolve('@whiskeysockets/baileys'))('libsignal'),
  );
} catch {
  // baileys' exports or its libsignal dependency moved; warned below.
}
if (!patched) {
  console.warn(
    'libsignal-redact: SessionEntry not found; Signal session keys may reach the log',
  );
}
