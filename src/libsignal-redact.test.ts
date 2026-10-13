import fs from 'fs';
import { createRequire } from 'module';
import path from 'path';
import { fileURLToPath } from 'url';
import util from 'util';

import { afterEach, describe, expect, it, vi } from 'vitest';

import { redactSessionEntries } from './libsignal-redact.js';

// The libsignal copy baileys itself loads — the one whose console.info calls
// reach logs/paperclaw.log.
const require = createRequire(import.meta.url);
const libsignal = createRequire(require.resolve('@whiskeysockets/baileys'))(
  'libsignal',
);
const { SessionRecord } = libsignal;

// Read before any test calls redactSessionEntries itself, so it reflects only
// what importing the module did.
const patchedOnImport = Object.getOwnPropertySymbols(
  Object.getPrototypeOf(SessionRecord.createEntry()),
).includes(util.inspect.custom);

// Distinct fill bytes, so a leak is recognisable in whatever encoding it
// takes: a Buffer dump prints hex, serialize() prints base64.
const PRIV_KEY = Buffer.alloc(32, 0xa1);
const ROOT_KEY = Buffer.alloc(32, 0xb2);
const CHAIN_KEY = Buffer.alloc(32, 0xc3);
const SECRETS = [PRIV_KEY, ROOT_KEY, CHAIN_KEY].flatMap((key) => [
  key.toString('hex'),
  key.toString('base64'),
  key.toString('hex').match(/../g)!.slice(0, 4).join(' '),
]);

function fakeEntry(id: number, closed: number) {
  const entry = SessionRecord.createEntry();
  entry.registrationId = 1234;
  entry.currentRatchet = {
    ephemeralKeyPair: { pubKey: Buffer.alloc(33, 0x05), privKey: PRIV_KEY },
    lastRemoteEphemeralKey: Buffer.alloc(33, 0x06),
    previousCounter: 0,
    rootKey: ROOT_KEY,
  };
  entry.indexInfo = {
    baseKey: Buffer.alloc(33, id),
    baseKeyType: 1,
    closed,
    used: 1000 + id,
    created: 1000 + id,
    remoteIdentityKey: Buffer.alloc(33, 0x07),
  };
  entry.addChain(Buffer.alloc(33, 0x08), {
    chainKey: { counter: 0, key: CHAIN_KEY },
    chainType: 1,
    messageKeys: { 0: CHAIN_KEY },
  });
  return entry;
}

/** What libsignal's console calls would have printed, as console prints it. */
function captureConsole(): () => string {
  const calls: unknown[][] = [];
  for (const method of ['info', 'warn'] as const) {
    vi.spyOn(console, method).mockImplementation((...args) => {
      calls.push(args);
    });
  }
  return () => calls.map((args) => util.format(...args)).join('\n');
}

function expectNoKeyMaterial(printed: string): void {
  expect(printed).not.toContain('<Buffer');
  expect(printed).not.toContain('privKey');
  expect(printed).not.toContain('rootKey');
  for (const secret of SECRETS) expect(printed).not.toContain(secret);
}

describe('libsignal session logging', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('is patched by importing the module', () => {
    expect(patchedOnImport).toBe(true);
  });

  it('prints no key material when a session is opened or closed', () => {
    const printed = captureConsole();
    const record = new SessionRecord();
    const entry = fakeEntry(1, -1);
    record.setSession(entry);

    record.closeSession(entry);
    record.closeSession(entry); // "Session already closed", via console.warn
    record.openSession(entry);

    const text = printed();
    expect(text).toContain('Closing session: SessionEntry');
    expect(text).toContain('Session already closed SessionEntry');
    expect(text).toContain('Opening session: SessionEntry');
    expectNoKeyMaterial(text);
  });

  it('prints no key material when old closed sessions are pruned', () => {
    const printed = captureConsole();
    const record = new SessionRecord();
    // libsignal keeps 40 closed sessions; the 41st makes it drop the oldest.
    for (let id = 1; id <= 41; id++) record.setSession(fakeEntry(id, id));

    record.removeOldSessions();

    const text = printed();
    expect(text).toContain('Removing old closed session: SessionEntry');
    expectNoKeyMaterial(text);
  });

  it('is loaded by every module that loads baileys', () => {
    // Each of these can be a process's entry point (the service, `npm run
    // auth`, the setup sync script), so each must load the patch itself.
    const root = path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      '..',
    );
    const importers = ['src', 'setup'].flatMap((dir) =>
      (fs.readdirSync(path.join(root, dir), { recursive: true }) as string[])
        .filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'))
        .map((f) => path.join(dir, f))
        .filter((f) =>
          /^import (?!type\b)[^;]*from '@whiskeysockets\/baileys'/m.test(
            fs.readFileSync(path.join(root, f), 'utf-8'),
          ),
        ),
    );

    expect(importers.length).toBeGreaterThanOrEqual(3);
    const missing = importers.filter(
      (f) =>
        !/^import '[./]+(dist\/)?libsignal-redact\.js';$/m.test(
          fs.readFileSync(path.join(root, f), 'utf-8'),
        ),
    );
    expect(missing).toEqual([]);
  });

  it('still recognises the libsignal that baileys ships', () => {
    // Fails when an upgrade changes the export shape the patch relies on.
    expect(redactSessionEntries(libsignal)).toBe(true);
  });

  it('reports, rather than throws, on a libsignal it does not recognise', () => {
    expect(redactSessionEntries({})).toBe(false);
    expect(redactSessionEntries({ SessionRecord: {} })).toBe(false);
    expect(
      redactSessionEntries({ SessionRecord: { createEntry: () => ({}) } }),
    ).toBe(false);
  });
});
