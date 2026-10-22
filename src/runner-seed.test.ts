import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./logger.js', () => ({
  logger: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { logger } from './logger.js';
import { hashTree, refreshRunnerCopy, seedMarkerPath } from './runner-seed.js';

const GROUP = 'some-group';

let root: string;
let bundle: string;
let copy: string;

/** Writes `files` (relative path -> content) under `dir`. */
function write(dir: string, files: Record<string, string>): void {
  for (const [rel, content] of Object.entries(files)) {
    const file = path.join(dir, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
  }
}

/** Every file under `dir`, as relative path -> content. */
function read(dir: string): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (rel: string): void => {
    for (const entry of fs.readdirSync(path.join(dir, rel), {
      withFileTypes: true,
    })) {
      const relPath = path.join(rel, entry.name);
      if (entry.isDirectory()) walk(relPath);
      else out[relPath] = fs.readFileSync(path.join(dir, relPath), 'utf-8');
    }
  };
  walk('');
  return out;
}

function markerHash(): string | undefined {
  const file = seedMarkerPath(copy);
  if (!fs.existsSync(file)) return undefined;
  return JSON.parse(fs.readFileSync(file, 'utf-8')).hash;
}

/** What sits beside the copy: the copy, its marker, and nothing half-done. */
function siblings(): string[] {
  return fs.readdirSync(path.dirname(copy)).sort();
}

beforeEach(() => {
  vi.clearAllMocks();
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'runner-seed-'));
  bundle = path.join(root, 'bundle');
  copy = path.join(root, 'sessions', GROUP, 'agent-runner-src');
  write(bundle, { 'index.ts': 'v1 index\n', 'ipc.ts': 'v1 ipc\n' });
  fs.mkdirSync(path.dirname(copy), { recursive: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(root, { recursive: true, force: true });
});

describe('hashTree', () => {
  it('does not depend on the order files were written, their mtimes, or readdir order', () => {
    const a = path.join(root, 'a');
    const b = path.join(root, 'b');
    write(a, { 'x.ts': 'x', 'sub/y.ts': 'y' });
    write(b, { 'sub/y.ts': 'y', 'x.ts': 'x' });
    const old = new Date('2001-01-01T00:00:00Z');
    fs.utimesSync(path.join(b, 'x.ts'), old, old);
    fs.utimesSync(path.join(b, 'sub', 'y.ts'), old, old);
    const expected = hashTree(a);

    const realReaddir = fs.readdirSync.bind(fs) as (
      ...args: unknown[]
    ) => unknown[];
    vi.spyOn(fs, 'readdirSync').mockImplementation(((...args: unknown[]) =>
      [...realReaddir(...args)].reverse()) as unknown as typeof fs.readdirSync);

    expect(hashTree(b)).toBe(expected);
  });

  it('changes when a file changes', () => {
    const before = hashTree(bundle);
    write(bundle, { 'ipc.ts': 'v1 ipc, edited\n' });
    expect(hashTree(bundle)).not.toBe(before);
  });

  it('changes when a file is renamed', () => {
    const before = hashTree(bundle);
    fs.renameSync(path.join(bundle, 'ipc.ts'), path.join(bundle, 'ipc2.ts'));
    expect(hashTree(bundle)).not.toBe(before);
  });

  it('does not run one file into the next', () => {
    const a = path.join(root, 'a');
    const b = path.join(root, 'b');
    write(a, { 'p.ts': 'xy', 'q.ts': '' });
    write(b, { 'p.ts': 'x', 'q.ts': 'y' });
    expect(hashTree(a)).not.toBe(hashTree(b));
  });
});

describe('refreshRunnerCopy', () => {
  it('seeds a group that has no copy, and records what it seeded beside it', () => {
    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('seeded');

    expect(read(copy)).toEqual(read(bundle));
    expect(markerHash()).toBe(hashTree(bundle));
    // Beside the copy, not inside it: the copy is mounted into the container
    // writable, and no agent may edit the marker.
    expect(path.relative(copy, seedMarkerPath(copy))).toMatch(/^\.\./);
    expect(siblings()).toEqual([
      'agent-runner-src',
      'agent-runner-src.seed.json',
    ]);
  });

  it('leaves a current copy alone on the next start', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const marker = fs.readFileSync(seedMarkerPath(copy), 'utf-8');
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('current');

    expect(read(copy)).toEqual(read(bundle));
    expect(fs.readFileSync(seedMarkerPath(copy), 'utf-8')).toBe(marker);
    expect(logger.warn).not.toHaveBeenCalled();
    expect(logger.info).not.toHaveBeenCalled();
  });

  it('replaces an unedited copy when the bundled runner changed', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const oldHash = hashTree(bundle);
    write(bundle, { 'index.ts': 'v2 index\n' });
    const newHash = hashTree(bundle);
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');

    expect(read(copy)).toEqual(read(bundle));
    expect(markerHash()).toBe(newHash);
    expect(siblings()).toEqual([
      'agent-runner-src',
      'agent-runner-src.seed.json',
    ]);
    const [fields] = vi.mocked(logger.info).mock.calls[0] as unknown as [
      { group: string; from: string; to: string },
    ];
    expect(fields.group).toBe(GROUP);
    expect(oldHash.startsWith(fields.from)).toBe(true);
    expect(newHash.startsWith(fields.to)).toBe(true);
    expect(fields.from).not.toBe(fields.to);
  });

  it('records what it installed, so a bundle that moved during the copy is refreshed next start', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    write(bundle, { 'index.ts': 'v2 index\n' });
    // A `git pull` lands one runner file after the bundle was hashed and
    // before it was copied, and another after the copy: what gets installed
    // is a tree nobody edited and that matches neither hash taken so far.
    const realCp = fs.cpSync.bind(fs);
    vi.spyOn(fs, 'cpSync').mockImplementationOnce((src, dst, opts) => {
      write(bundle, { 'index.ts': 'v3 index\n' });
      realCp(src, dst, opts);
      write(bundle, { 'ipc.ts': 'v3 ipc\n' });
    });
    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');
    expect(read(copy)).toEqual({
      'index.ts': 'v3 index\n',
      'ipc.ts': 'v1 ipc\n',
    });
    expect(markerHash()).toBe(hashTree(copy));
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');

    expect(read(copy)).toEqual(read(bundle));
    expect(logger.warn).not.toHaveBeenCalled();
  });

  it('copies a relative symlink in the bundle as it is', () => {
    write(bundle, { 'lib/a.ts': 'a\n' });
    fs.symlinkSync('lib/a.ts', path.join(bundle, 'alias.ts'));

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('seeded');

    // Relative still, so it resolves inside the container's mount too.
    expect(fs.readlinkSync(path.join(copy, 'alias.ts'))).toBe('lib/a.ts');
    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('current');
    expect(logger.warn).not.toHaveBeenCalled();
  });

  // Root ignores directory modes, so the leftover could always be removed.
  it.skipIf(process.getuid?.() === 0)(
    'logs a replaced copy it cannot remove, and refreshes past it later',
    () => {
      write(bundle, { 'lib/a.ts': 'a\n' });
      refreshRunnerCopy(bundle, copy, GROUP);
      // Modes are not hashed, so this copy is still unedited and is swapped
      // out, but nothing under lib/ can be deleted any more.
      fs.chmodSync(path.join(copy, 'lib'), 0o555);
      try {
        write(bundle, { 'index.ts': 'v2 index\n' });
        vi.clearAllMocks();

        expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');
        expect(read(copy)).toEqual(read(bundle));
        const leftover = `${copy}.prev`;
        expect(fs.existsSync(leftover)).toBe(true);
        expect(logger.warn).toHaveBeenCalledWith(
          expect.objectContaining({ group: GROUP, path: leftover }),
          expect.any(String),
        );

        write(bundle, { 'index.ts': 'v3 index\n' });
        expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');
        expect(read(copy)).toEqual(read(bundle));
        expect(logger.error).not.toHaveBeenCalled();
      } finally {
        for (const name of fs.readdirSync(path.dirname(copy))) {
          const lib = path.join(path.dirname(copy), name, 'lib');
          if (fs.existsSync(lib)) fs.chmodSync(lib, 0o755);
        }
      }
    },
  );

  it('drops files from a replaced copy that the bundle no longer has', () => {
    write(bundle, { 'old-tool.ts': 'gone soon\n' });
    refreshRunnerCopy(bundle, copy, GROUP);
    fs.rmSync(path.join(bundle, 'old-tool.ts'));

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');

    expect(fs.existsSync(path.join(copy, 'old-tool.ts'))).toBe(false);
    expect(read(copy)).toEqual(read(bundle));
  });

  it('keeps a copy edited in place, and warns once the bundle moved on', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const seeded = markerHash();
    write(copy, { 'my-tool.ts': 'the agent added this\n' });
    write(bundle, { 'index.ts': 'v2 index\n' });
    const edited = read(copy);
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('edited');

    expect(read(copy)).toEqual(edited);
    expect(markerHash()).toBe(seeded);
    expect(logger.warn).toHaveBeenCalledTimes(1);
    const [fields, message] = vi.mocked(logger.warn).mock
      .calls[0] as unknown as [{ group: string }, string];
    expect(fields.group).toBe(GROUP);
    // It has to say how to take the bundled runner.
    expect(message).toContain('delete');
    expect(message).toContain(copy);
  });

  it('keeps an edited copy without a warning while the bundle is what it was seeded from', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    write(copy, { 'my-tool.ts': 'the agent added this\n' });
    const edited = read(copy);
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('edited');

    expect(read(copy)).toEqual(edited);
    expect(logger.warn).not.toHaveBeenCalled();
  });

  it('adopts a copy seeded before markers existed when it matches the bundle', () => {
    fs.cpSync(bundle, copy, { recursive: true });

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('adopted');
    expect(markerHash()).toBe(hashTree(bundle));

    // Adopted, it is refreshed like any unedited copy.
    write(bundle, { 'index.ts': 'v2 index\n' });
    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');
    expect(read(copy)).toEqual(read(bundle));
  });

  it('keeps a copy seeded before markers existed that differs from the bundle, and warns', () => {
    write(copy, { 'index.ts': 'some older or edited index\n' });
    const before = read(copy);

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('edited');

    expect(read(copy)).toEqual(before);
    // No marker is invented for it: it stays unknown, so it is never replaced.
    expect(markerHash()).toBeUndefined();
    expect(logger.warn).toHaveBeenCalledTimes(1);
  });

  it('reads an unreadable marker as no marker', () => {
    fs.cpSync(bundle, copy, { recursive: true });
    fs.writeFileSync(seedMarkerPath(copy), 'not json');

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('adopted');
    expect(markerHash()).toBe(hashTree(bundle));
  });

  it('leaves the old copy usable when copying the new one fails midway', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const old = read(copy);
    const seeded = markerHash();
    write(bundle, { 'index.ts': 'v2 index\n' });
    const realCp = fs.cpSync.bind(fs);
    const cp = vi.spyOn(fs, 'cpSync').mockImplementationOnce((_src, dst) => {
      write(String(dst), { 'index.ts': 'half' });
      throw new Error('ENOSPC: no space left on device');
    });

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('failed');

    expect(read(copy)).toEqual(old);
    expect(markerHash()).toBe(seeded);
    expect(siblings()).toEqual([
      'agent-runner-src',
      'agent-runner-src.seed.json',
    ]);
    expect(logger.error).toHaveBeenCalledTimes(1);

    cp.mockImplementation(realCp);
    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('refreshed');
    expect(read(copy)).toEqual(read(bundle));
  });

  it('puts the old copy back when the swap fails', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const old = read(copy);
    const seeded = markerHash();
    write(bundle, { 'index.ts': 'v2 index\n' });
    const realRename = fs.renameSync.bind(fs);
    vi.spyOn(fs, 'renameSync').mockImplementation((src, dst) => {
      if (dst === copy && String(src).endsWith('.next')) {
        throw new Error('EBUSY: resource busy');
      }
      realRename(src, dst);
    });

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('failed');

    expect(read(copy)).toEqual(old);
    expect(markerHash()).toBe(seeded);
    expect(siblings()).toEqual([
      'agent-runner-src',
      'agent-runner-src.seed.json',
    ]);
  });

  it('leaves no half copy when the first seed fails', () => {
    vi.spyOn(fs, 'cpSync').mockImplementationOnce((_src, dst) => {
      write(String(dst), { 'index.ts': 'half' });
      throw new Error('ENOSPC: no space left on device');
    });

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('failed');

    // Nothing there, so the next start seeds it again.
    expect(siblings()).toEqual([]);
  });

  it('starts with the copy as it is when the copy cannot be read', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    write(bundle, { 'index.ts': 'v2 index\n' });
    const old = read(copy);
    const realRead = fs.readFileSync.bind(fs) as (
      ...args: unknown[]
    ) => unknown;
    vi.spyOn(fs, 'readFileSync').mockImplementation(((...args: unknown[]) => {
      if (String(args[0]).startsWith(copy + path.sep)) {
        throw new Error('EIO: i/o error');
      }
      return realRead(...args);
    }) as unknown as typeof fs.readFileSync);

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('failed');

    vi.restoreAllMocks();
    expect(read(copy)).toEqual(old);
  });

  it('keeps the copy as it is, and warns, when there is no bundled runner', () => {
    refreshRunnerCopy(bundle, copy, GROUP);
    const old = read(copy);
    const seeded = markerHash();
    fs.rmSync(bundle, { recursive: true });
    vi.clearAllMocks();

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('no-bundle');

    expect(read(copy)).toEqual(old);
    expect(markerHash()).toBe(seeded);
    expect(logger.warn).toHaveBeenCalledTimes(1);
    const [fields, message] = vi.mocked(logger.warn).mock
      .calls[0] as unknown as [{ group: string }, string];
    expect(fields.group).toBe(GROUP);
    // A compiled host reads the bundle its build staged: say where it looked
    // and how it gets there.
    expect(message).toContain(bundle);
    expect(message).toContain('npm run build');
  });

  it('seeds nothing without a bundled runner', () => {
    fs.rmSync(bundle, { recursive: true });

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('no-bundle');

    expect(siblings()).toEqual([]);
    expect(logger.warn).toHaveBeenCalledTimes(1);
  });

  it('seeds an empty copy, which is what a start without a bundle leaves', () => {
    // The container runtime creates a missing bind-mount source as an empty
    // directory, so a group that started once without a bundle has one. Read
    // as an edit, it would never be seeded.
    fs.mkdirSync(copy);

    expect(refreshRunnerCopy(bundle, copy, GROUP)).toBe('seeded');

    expect(read(copy)).toEqual(read(bundle));
    expect(markerHash()).toBe(hashTree(bundle));
  });
});
