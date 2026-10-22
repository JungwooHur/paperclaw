import { spawnSync } from 'child_process';
import fs from 'fs';
import { createRequire } from 'module';
import os from 'os';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./logger.js', () => ({
  logger: { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() },
}));

import {
  staleBuildFiles,
  warnIfBuildStale,
  warnIfRunningStaleBuild,
} from './build-freshness.js';
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

describe('warnIfRunningStaleBuild (the check main() runs at startup)', () => {
  it('warns when the module runs from dist/ and src/ is newer', () => {
    write(path.join(src, 'index.ts'), 3000);
    write(path.join(dist, 'index.js'), 2000);

    warnIfRunningStaleBuild(pathToFileURL(path.join(dist, 'index.js')).href);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(vi.mocked(logger.warn).mock.calls[0][0]).toMatchObject({
      stale: ['index.ts'],
    });
  });

  it('names a runner source newer than the copy the build staged', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);
    const runner = path.join(root, 'container', 'agent-runner', 'src');
    write(path.join(runner, 'index.ts'), 3000);
    write(path.join(runner, 'ipc-mcp-stdio.ts'), 1000);
    write(path.join(dist, 'agent-runner-src', 'index.ts'), 2000);
    write(path.join(dist, 'agent-runner-src', 'ipc-mcp-stdio.ts'), 2000);

    warnIfRunningStaleBuild(pathToFileURL(path.join(dist, 'index.js')).href);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    const [fields, message] = vi.mocked(logger.warn).mock
      .calls[0] as unknown as [{ stale: string[]; count: number }, string];
    expect(fields).toMatchObject({ stale: ['index.ts'], count: 1 });
    expect(message).toContain('dist/agent-runner-src/');
  });

  it('names a runner source the build never staged', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);
    const runner = path.join(root, 'container', 'agent-runner', 'src');
    write(path.join(runner, 'index.ts'), 1000);

    warnIfRunningStaleBuild(pathToFileURL(path.join(dist, 'index.js')).href);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(vi.mocked(logger.warn).mock.calls[0][0]).toMatchObject({
      stale: ['index.ts'],
    });
  });

  it('stays quiet when the staged runner is as new as its sources', () => {
    write(path.join(src, 'index.ts'), 1000);
    write(path.join(dist, 'index.js'), 2000);
    const runner = path.join(root, 'container', 'agent-runner', 'src');
    write(path.join(runner, 'index.ts'), 1000);
    write(path.join(dist, 'agent-runner-src', 'index.ts'), 2000);

    warnIfRunningStaleBuild(pathToFileURL(path.join(dist, 'index.js')).href);

    expect(logger.warn).not.toHaveBeenCalled();
  });

  it('checks nothing when the module runs from src/ (npm run dev)', () => {
    write(path.join(src, 'index.ts'), 3000);
    write(path.join(dist, 'index.js'), 2000);

    warnIfRunningStaleBuild(pathToFileURL(path.join(src, 'index.ts')).href);

    expect(logger.warn).not.toHaveBeenCalled();
  });
});

/**
 * The unit lets a failed build through so the bot still starts. That is only
 * safe if a failed build leaves dist/ as it was: tsc emits on a type error by
 * default, which would put the new, failing code in dist/ with fresh mtimes, so
 * the bot would run it and the WARN would never fire. The project's tsconfig is
 * what decides, so this builds a one-file project that extends it.
 */
describe('a build with a type error', () => {
  const repoTsconfig = fileURLToPath(
    new URL('../tsconfig.json', import.meta.url),
  );
  const tsc = createRequire(import.meta.url).resolve('typescript/bin/tsc');

  it('leaves dist/ untouched, so the startup WARN still fires', () => {
    fs.writeFileSync(
      path.join(root, 'tsconfig.json'),
      JSON.stringify({
        extends: repoTsconfig,
        compilerOptions: { rootDir: './src', outDir: './dist', types: [] },
        include: ['src/**/*'],
      }),
    );
    fs.mkdirSync(dist, { recursive: true });
    fs.writeFileSync(path.join(dist, 'probe.js'), 'export const n = 1;\n');
    fs.utimesSync(path.join(dist, 'probe.js'), 2000, 2000);
    fs.mkdirSync(src, { recursive: true });
    fs.writeFileSync(
      path.join(src, 'probe.ts'),
      "export const n: number = 'not a number';\n",
    );

    const build = spawnSync(process.execPath, [tsc, '-p', root], {
      encoding: 'utf-8',
    });

    expect(build.stdout).toContain('error TS2322');
    expect(build.status).not.toBe(0);
    expect(fs.readFileSync(path.join(dist, 'probe.js'), 'utf-8')).toBe(
      'export const n = 1;\n',
    );
    warnIfBuildStale(src, dist);
    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(vi.mocked(logger.warn).mock.calls[0][0]).toMatchObject({
      stale: ['probe.ts'],
    });
  }, 30_000);
});
