import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./logger.js', () => ({
  logger: { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() },
}));

import { staleBuildFiles, warnIfBuildStale } from './build-freshness.js';
import { logger } from './logger.js';

let root: string;
let src: string;
let dist: string;

function write(file: string, mtimeSec: number): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '');
  fs.utimesSync(file, mtimeSec, mtimeSec);
}

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-build-'));
  src = path.join(root, 'src');
  dist = path.join(root, 'dist');
  vi.mocked(logger.warn).mockClear();
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe('staleBuildFiles', () => {
  it('is empty when every source has newer output', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(src, 'channels', 'whatsapp.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);
    write(path.join(dist, 'channels', 'whatsapp.js'), 2000);

    expect(staleBuildFiles(src, dist)).toEqual([]);
  });

  it('names a source edited after the build', () => {
    write(path.join(src, 'index.ts'), 3000);
    write(path.join(dist, 'index.js'), 2000);

    expect(staleBuildFiles(src, dist)).toEqual(['index.ts']);
  });

  it('names a source that has no output at all (a module merged, never built)', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(src, 'channels', 'new-module.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);

    expect(staleBuildFiles(src, dist)).toEqual([
      path.join('channels', 'new-module.ts'),
    ]);
  });

  it('ignores declaration files, which emit nothing', () => {
    write(path.join(src, 'globals.d.ts'), 3000);

    expect(staleBuildFiles(src, dist)).toEqual([]);
  });
});

describe('warnIfBuildStale', () => {
  it('warns, naming the stale files, when dist is behind src', () => {
    write(path.join(src, 'index.ts'), 3000);
    write(path.join(dist, 'index.js'), 2000);

    warnIfBuildStale(src, dist);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(vi.mocked(logger.warn).mock.calls[0][0]).toMatchObject({
      stale: ['index.ts'],
      count: 1,
    });
  });

  it('stays quiet when the build is current', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);

    warnIfBuildStale(src, dist);

    expect(logger.warn).not.toHaveBeenCalled();
  });

  it('never throws, so a missing src/ cannot stop startup', () => {
    expect(() => warnIfBuildStale(path.join(root, 'nope'), dist)).not.toThrow();
  });
});
