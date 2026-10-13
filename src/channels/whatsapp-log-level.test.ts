import { EventEmitter } from 'events';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// What reaches the log. The app logger is a real pino instance writing here, so
// the logger whatsapp.ts hands to baileys is whatever it really derives from it.
const { lines } = vi.hoisted(() => ({ lines: [] as string[] }));

vi.mock('../logger.js', async () => {
  const { default: pino } = await import('pino');
  return {
    logger: pino(
      { level: 'info' },
      { write: (line: string) => lines.push(line) },
    ),
  };
});

vi.mock('../config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../config.js')>()),
  STORE_DIR: '/tmp/paperclaw-test-store',
}));

vi.mock('../auth-state.js', () => ({
  AUTH_REQUIRED_EXIT_CODE: 78,
  markAuthRequired: vi.fn(),
  clearAuthRequired: vi.fn(),
}));

vi.mock('../db.js', () => ({
  getLastGroupSync: vi.fn(() => new Date().toISOString()),
  setLastGroupSync: vi.fn(),
  updateChatName: vi.fn(),
}));

vi.mock('fs', async () => {
  const actual = await vi.importActual<typeof import('fs')>('fs');
  return { ...actual, default: { ...actual, mkdirSync: vi.fn() } };
});

vi.mock('child_process', () => ({ exec: vi.fn() }));

const ev = new EventEmitter();
vi.mock('@whiskeysockets/baileys', () => ({
  default: vi.fn(() => ({
    ev: {
      on: (event: string, fn: (...a: unknown[]) => void) => ev.on(event, fn),
    },
    sendPresenceUpdate: vi.fn().mockResolvedValue(undefined),
    groupFetchAllParticipating: vi.fn().mockResolvedValue({}),
  })),
  Browsers: { macOS: vi.fn(() => ['macOS', 'Chrome', '']) },
  DisconnectReason: { loggedOut: 401 },
  fetchLatestWaWebVersion: vi.fn().mockResolvedValue({ version: [2, 3000, 0] }),
  makeCacheableSignalKeyStore: vi.fn((keys: unknown) => keys),
  useMultiFileAuthState: vi
    .fn()
    .mockResolvedValue({ state: { creds: {}, keys: {} }, saveCreds: vi.fn() }),
}));

import makeWASocket, {
  makeCacheableSignalKeyStore,
} from '@whiskeysockets/baileys';

import { WhatsAppChannel } from './whatsapp.js';

type Log = { info(msg: string): void; warn(msg: string): void };

/** Connects a channel and returns the logger it gave baileys. */
async function connect(): Promise<Log> {
  const channel = new WhatsAppChannel({
    onMessage: vi.fn(),
    onChatMetadata: vi.fn(),
    registeredGroups: vi.fn(() => ({})),
  });
  const connected = channel.connect();
  await new Promise((r) => setTimeout(r, 0));
  ev.emit('connection.update', { connection: 'open' });
  await connected;

  const { logger } = vi.mocked(makeWASocket).mock.calls[0][0];
  // The signal key store logs through baileys' logger too.
  expect(vi.mocked(makeCacheableSignalKeyStore).mock.calls[0][1]).toBe(logger);
  return logger as unknown as Log;
}

const logged = (msg: string) => lines.some((line) => line.includes(msg));

describe('baileys log level', () => {
  beforeEach(() => {
    lines.length = 0;
    ev.removeAllListeners();
    vi.mocked(makeWASocket).mockClear();
    vi.mocked(makeCacheableSignalKeyStore).mockClear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps baileys' info lines out, and the app's own in", async () => {
    vi.stubEnv('BAILEYS_LOG_LEVEL', '');
    const baileys = await connect();

    baileys.info('connection errored');
    baileys.warn('baileys warning');

    expect(logged('connection errored')).toBe(false);
    expect(logged('baileys warning')).toBe(true);
    // The watchdog reads this line; it comes from the app logger.
    expect(logged('Connected to WhatsApp')).toBe(true);
  });

  it('lets BAILEYS_LOG_LEVEL bring the info lines back', async () => {
    vi.stubEnv('BAILEYS_LOG_LEVEL', 'info');
    const baileys = await connect();

    baileys.info('connection errored');

    expect(logged('connection errored')).toBe(true);
  });
});
