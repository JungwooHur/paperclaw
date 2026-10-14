import { EventEmitter } from 'events';
import path from 'path';
import { PassThrough } from 'stream';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The paths the main group's project-root mount must hide. Each one exists on
// the host in this test (see the fs mock), so a missing mask is a real miss.
const MASKED = ['.env', 'store', 'logs'];

vi.mock('./config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./config.js')>()),
  DATA_DIR: '/tmp/paperclaw-test-data',
  GROUPS_DIR: '/tmp/paperclaw-test-groups',
}));

vi.mock('./logger.js', () => ({
  logger: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock('fs', async () => {
  const actual = await vi.importActual<typeof import('fs')>('fs');
  const present = new Set(
    ['.env', 'store', 'store/auth', 'logs'].map((p) =>
      path.join(process.cwd(), p),
    ),
  );
  return {
    ...actual,
    default: {
      ...actual,
      existsSync: vi.fn((p: string) => present.has(p)),
      mkdirSync: vi.fn(),
      writeFileSync: vi.fn(),
      readFileSync: vi.fn(() => ''),
      readdirSync: vi.fn(() => []),
      statSync: vi.fn(() => ({ isDirectory: () => false })),
      copyFileSync: vi.fn(),
    },
  };
});

vi.mock('./mount-security.js', () => ({
  validateAdditionalMounts: vi.fn(() => []),
}));

let fakeProc: EventEmitter & {
  stdin: PassThrough;
  stdout: PassThrough;
  stderr: PassThrough;
  kill: () => void;
};

vi.mock('child_process', async () => {
  const actual =
    await vi.importActual<typeof import('child_process')>('child_process');
  return { ...actual, spawn: vi.fn(() => fakeProc) };
});

import { spawn } from 'child_process';

import { runContainerAgent } from './container-runner.js';

/** Container path -> `host:container[:ro]` for every bind mount spawned. */
async function mainGroupMounts(): Promise<Map<string, string>> {
  const run = runContainerAgent(
    { name: 'Main', folder: 'main', trigger: '@Andy', added_at: '' },
    { prompt: 'hi', groupFolder: 'main', chatJid: 'main@g.us', isMain: true },
    () => {},
  );
  const args = vi.mocked(spawn).mock.calls[0][1] as string[];
  fakeProc.emit('close', 0);
  await run;

  const mounts = new Map<string, string>();
  args.forEach((arg, i) => {
    if (args[i - 1] === '-v') mounts.set(arg.split(':')[1], arg);
  });
  return mounts;
}

describe('main group project mount', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(spawn).mockClear();
    fakeProc = Object.assign(new EventEmitter(), {
      stdin: new PassThrough(),
      stdout: new PassThrough(),
      stderr: new PassThrough(),
      kill: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('is read-only', async () => {
    const mounts = await mainGroupMounts();
    expect(mounts.get('/workspace/project')).toBe(
      `${process.cwd()}:/workspace/project:ro`,
    );
  });

  it.each(MASKED)('hides %s behind an empty read-only mask', async (rel) => {
    const mounts = await mainGroupMounts();
    const containerPath = path.posix.join('/workspace/project', rel);
    const mount = mounts.get(containerPath);

    expect(mount, `${containerPath} is not masked`).toBeDefined();
    const [host, , mode] = mount!.split(':');
    expect(host.startsWith('/tmp/paperclaw-test-data/masks/')).toBe(true);
    expect(mode).toBe('ro');
  });

  // A mount inside a directory mask lands in the shared empty mask directory:
  // Docker has to create its mount point there, which a read-only mask refuses,
  // and which otherwise leaves the "empty" directory no longer empty.
  it('mounts nothing inside a masked directory', async () => {
    const mounts = await mainGroupMounts();
    const masked = MASKED.map((rel) =>
      path.posix.join('/workspace/project', rel),
    );
    const nested = [...mounts.keys()].filter((c) =>
      masked.some((m) => c.startsWith(`${m}/`)),
    );
    expect(nested).toEqual([]);
  });
});
