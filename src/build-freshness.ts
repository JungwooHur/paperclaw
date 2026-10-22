/**
 * Says loudly when the code running is not the code checked out.
 *
 * The service runs dist/, and its unit rebuilds dist/ before every start. That
 * build is allowed to fail — a type error must not keep the bot down — so the
 * process may start on an older dist/. Without this check that is invisible: a
 * restart reports `active`, logs a clean startup, and runs the old code.
 *
 * It relies on a failed build emitting nothing (`noEmitOnError` in
 * tsconfig.json). tsc's default is to write every output despite a type error,
 * which would leave dist/ newer than src/, run the failing code, and never warn.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { logger } from './logger.js';
import { runnerSourceDir, stagedRunnerDir } from './stage-runner.js';

/**
 * Sources under `srcDir` whose output in `outDir` is missing or older.
 *
 * `outputFor` maps a source's relative path to its output's, or to null for a
 * source that has none. Paths are relative to `srcDir`.
 */
function staleOutputs(
  srcDir: string,
  outDir: string,
  outputFor: (relPath: string) => string | null,
): string[] {
  const stale: string[] = [];
  const walk = (rel: string): void => {
    const entries = fs.readdirSync(path.join(srcDir, rel), {
      withFileTypes: true,
    });
    for (const entry of entries) {
      const relPath = path.join(rel, entry.name);
      if (entry.isDirectory()) {
        walk(relPath);
        continue;
      }
      const outRel = outputFor(relPath);
      if (outRel === null) continue;
      const srcMtime = fs.statSync(path.join(srcDir, relPath)).mtimeMs;
      const out = path.join(outDir, outRel);
      let outMtime: number;
      try {
        outMtime = fs.statSync(out).mtimeMs;
      } catch {
        stale.push(relPath);
        continue;
      }
      if (srcMtime > outMtime) stale.push(relPath);
    }
  };
  walk('');
  return stale.sort();
}

/**
 * Sources under `srcDir` whose compiled `.js` in `distDir` is missing or older.
 *
 * Paired file by file rather than comparing the newest source with one output,
 * so a module that was merged but never built — no output at all — is caught,
 * and the answer stays right if the build ever becomes incremental. Paths are
 * relative to `srcDir`. Declaration files are skipped: they emit nothing.
 */
export function staleBuildFiles(srcDir: string, distDir: string): string[] {
  return staleOutputs(srcDir, distDir, (relPath) =>
    relPath.endsWith('.ts') && !relPath.endsWith('.d.ts')
      ? relPath.replace(/\.ts$/, '.js')
      : null,
  );
}

/**
 * Runner sources in `runnerDir` whose staged copy in `stagedDir` is missing or
 * older. The build copies them as they are, so each pairs with its own path.
 */
export function staleStagedRunnerFiles(
  runnerDir: string,
  stagedDir: string,
): string[] {
  return staleOutputs(runnerDir, stagedDir, (relPath) => relPath);
}

/**
 * The startup check, for the module at `moduleUrl` (main()'s import.meta.url).
 *
 * Only a run from dist/ can be stale; `npm run dev` runs src/ itself, so a
 * module anywhere else checks nothing. src/ is dist/'s sibling. The runner the
 * build staged is checked too, since groups are refreshed from it.
 */
export function warnIfRunningStaleBuild(moduleUrl: string): void {
  const here = path.dirname(fileURLToPath(moduleUrl));
  if (path.basename(here) !== 'dist') return;
  const root = path.join(here, '..');
  warnIfBuildStale(path.join(root, 'src'), here);
  warnIfStagedRunnerStale(runnerSourceDir(root), stagedRunnerDir(here));
}

/** Logs a WARN naming the stale files, if any. Never throws. */
export function warnIfBuildStale(srcDir: string, distDir: string): void {
  let stale: string[];
  try {
    stale = staleBuildFiles(srcDir, distDir);
  } catch (err) {
    logger.warn({ err, srcDir }, 'Could not compare dist/ with src/');
    return;
  }
  if (stale.length === 0) return;
  logger.warn(
    { stale: stale.slice(0, 10), count: stale.length },
    'dist/ is older than src/: this process is NOT running the checked-out code. ' +
      'The build before start failed — see the start of logs/paperclaw.log — ' +
      'fix it, then `npm run build` and restart.',
  );
}

/**
 * Logs a WARN naming the runner sources the staged runner is behind, if any.
 * A checkout without runner sources has nothing to stage. Never throws.
 */
export function warnIfStagedRunnerStale(
  runnerDir: string,
  stagedDir: string,
): void {
  if (!fs.existsSync(runnerDir)) return;
  let stale: string[];
  try {
    stale = staleStagedRunnerFiles(runnerDir, stagedDir);
  } catch (err) {
    logger.warn(
      { err, runnerDir },
      'Could not compare dist/agent-runner-src/ with the runner sources',
    );
    return;
  }
  if (stale.length === 0) return;
  logger.warn(
    { stale: stale.slice(0, 10), count: stale.length },
    'dist/agent-runner-src/ is older than container/agent-runner/src/: groups ' +
      'are NOT getting the checked-out agent runner. The build before start ' +
      'failed — see the start of logs/paperclaw.log — fix it, then ' +
      '`npm run build` and restart.',
  );
}
