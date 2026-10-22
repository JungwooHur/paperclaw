import { spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./logger.js', () => ({
  logger: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { hashTree } from './runner-seed.js';
import {
  runnerBundleDir,
  runnerSourceDir,
  stagedRunnerDir,
  stageRunner,
} from './stage-runner.js';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));

let root: string;
let source: string;
let staged: string;

/** Writes `files` (relative path -> content) under `dir`. */
function write(dir: string, files: Record<string, string>): void {
  for (const [rel, content] of Object.entries(files)) {
    const file = path.join(dir, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'stage-runner-'));
  source = runnerSourceDir(root);
  staged = stagedRunnerDir(path.join(root, 'dist'));
  write(source, { 'index.ts': 'v1 index\n', 'tools/ipc.ts': 'v1 ipc\n' });
});

afterEach(() => {
  vi.restoreAllMocks();
  fs.rmSync(root, { recursive: true, force: true });
});

describe('runnerBundleDir', () => {
  const checkout = '/checkout/container/agent-runner/src';

  it('reads the copy the build staged when the host runs compiled from dist/', () => {
    const moduleUrl = pathToFileURL('/app/dist/container-runner.js').href;

    expect(runnerBundleDir(moduleUrl, checkout)).toBe(
      stagedRunnerDir('/app/dist'),
    );
  });

  it('reads the checkout when the host runs its sources (npm run dev)', () => {
    const moduleUrl = pathToFileURL('/app/src/container-runner.ts').href;

    expect(runnerBundleDir(moduleUrl, checkout)).toBe(checkout);
  });
});

describe('stageRunner', () => {
  it('stages an exact copy of the runner sources beside the compiled host', () => {
    expect(stageRunner(root)).toBe(staged);

    expect(hashTree(staged)).toBe(hashTree(source));
    expect(fs.readdirSync(path.join(root, 'dist'))).toEqual([
      'agent-runner-src',
    ]);
  });

  it('drops files from the staged copy that the sources no longer have', () => {
    stageRunner(root);
    fs.rmSync(path.join(source, 'tools', 'ipc.ts'));
    write(source, { 'index.ts': 'v2 index\n' });

    stageRunner(root);

    expect(fs.existsSync(path.join(staged, 'tools', 'ipc.ts'))).toBe(false);
    expect(hashTree(staged)).toBe(hashTree(source));
  });

  it('keeps the previous staged copy whole when copying the new one fails', () => {
    stageRunner(root);
    const before = hashTree(staged);
    write(source, { 'index.ts': 'v2 index\n' });
    vi.spyOn(fs, 'cpSync').mockImplementationOnce((_src, dst) => {
      write(String(dst), { 'index.ts': 'half' });
      throw new Error('ENOSPC: no space left on device');
    });

    expect(() => stageRunner(root)).toThrow('ENOSPC');

    expect(hashTree(staged)).toBe(before);
    expect(fs.readdirSync(path.join(root, 'dist'))).toEqual([
      'agent-runner-src',
    ]);
  });

  it('fails without runner sources, so the build that ran it fails', () => {
    fs.rmSync(source, { recursive: true });

    expect(() => stageRunner(root)).toThrow();
    expect(fs.existsSync(staged)).toBe(false);
  });
});

/**
 * `npm run build` itself, run on a one-module project that extends the
 * repository's tsconfig. Staging is only safe if it happens after tsc
 * succeeded: tsc writes nothing on a type error (`noEmitOnError`), and the
 * staged runner must stay as old as the compiled host it ships with.
 */
describe('npm run build', () => {
  const buildScript: string = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf-8'),
  ).scripts.build;

  /** A project in `dir` with this module, one host module, and runner sources. */
  function project(dir = root): string {
    if (dir !== root) {
      fs.cpSync(source, runnerSourceDir(dir), { recursive: true });
    }
    fs.writeFileSync(
      path.join(dir, 'package.json'),
      JSON.stringify({ type: 'module', scripts: { build: buildScript } }),
    );
    fs.writeFileSync(
      path.join(dir, 'tsconfig.json'),
      JSON.stringify({
        extends: path.join(repoRoot, 'tsconfig.json'),
        compilerOptions: { rootDir: './src', outDir: './dist' },
        include: ['src/**/*'],
      }),
    );
    fs.symlinkSync(
      path.join(repoRoot, 'node_modules'),
      path.join(dir, 'node_modules'),
    );
    fs.mkdirSync(path.join(dir, 'src'));
    fs.copyFileSync(
      path.join(repoRoot, 'src', 'stage-runner.ts'),
      path.join(dir, 'src', 'stage-runner.ts'),
    );
    write(path.join(dir, 'src'), { 'probe.ts': 'export const n = 1;\n' });
    return dir;
  }

  /** Runs the build script the way npm does: `sh -c`, local bins first. */
  function build(dir: string): ReturnType<typeof spawnSync> {
    const bin = path.join(repoRoot, 'node_modules', '.bin');
    return spawnSync('sh', ['-c', buildScript], {
      cwd: dir,
      encoding: 'utf-8',
      env: {
        ...process.env,
        PATH: `${bin}${path.delimiter}${process.env.PATH}`,
      },
    });
  }

  it('stages an exact copy of the runner, also from a clean dist/', () => {
    const dir = project();

    const result = build(dir);

    expect(result.status, `${result.stdout}${result.stderr}`).toBe(0);
    expect(hashTree(staged)).toBe(hashTree(source));
  }, 30_000);

  // A URL spells these characters escaped and a path does not, so a check
  // that compares the two skips staging, silently, with the build exiting 0.
  it.each(['pc#1', 'pc%41'])(
    'stages the runner from a checkout whose path holds %s',
    (name) => {
      const dir = project(path.join(root, name));

      const result = build(dir);

      expect(result.status, `${result.stdout}${result.stderr}`).toBe(0);
      expect(hashTree(stagedRunnerDir(path.join(dir, 'dist')))).toBe(
        hashTree(runnerSourceDir(dir)),
      );
    },
    30_000,
  );

  it('stages the runner when node is handed a symlinked path to the script', () => {
    const dir = project();
    expect(build(dir).status).toBe(0);
    write(source, { 'index.ts': 'v2 index\n' });
    const link = path.join(dir, 'via-link');
    fs.symlinkSync(dir, link);

    const result = spawnSync(
      process.execPath,
      [path.join(link, 'dist', 'stage-runner.js')],
      { encoding: 'utf-8' },
    );

    expect(result.status, `${result.stdout}${result.stderr}`).toBe(0);
    expect(hashTree(staged)).toBe(hashTree(source));
  }, 30_000);

  it('stages nothing new when tsc fails, so the runner stays as old as dist/', () => {
    const dir = project();
    expect(build(dir).status).toBe(0);
    const before = hashTree(staged);
    write(source, { 'index.ts': 'v2 index\n', 'new-tool.ts': 'new\n' });
    write(path.join(dir, 'src'), {
      'probe.ts': "export const n: number = 'not a number';\n",
    });

    const result = build(dir);

    expect(result.stdout).toContain('error TS2322');
    expect(result.status).not.toBe(0);
    expect(hashTree(staged)).toBe(before);
    expect(fs.existsSync(path.join(staged, 'new-tool.ts'))).toBe(false);
  }, 30_000);
});
