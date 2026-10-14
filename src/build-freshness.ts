/**
 * Says loudly when the code running is not the code checked out.
 *
 * The service runs dist/, and its unit rebuilds dist/ before every start. That
 * build is allowed to fail — a type error must not keep the bot down — so the
 * process may start on an older dist/. Without this check that is invisible: a
 * restart reports `active`, logs a clean startup, and runs the old code.
 */
import fs from 'fs';
import path from 'path';

import { logger } from './logger.js';

/**
 * Sources under `srcDir` whose compiled `.js` in `distDir` is missing or older.
 *
 * Paired file by file rather than comparing the newest source with one output,
 * so a module that was merged but never built — no output at all — is caught,
 * and the answer stays right if the build ever becomes incremental. Paths are
 * relative to `srcDir`. Declaration files are skipped: they emit nothing.
 */
export function staleBuildFiles(srcDir: string, distDir: string): string[] {
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
      if (!entry.name.endsWith('.ts') || entry.name.endsWith('.d.ts')) continue;
      const srcMtime = fs.statSync(path.join(srcDir, relPath)).mtimeMs;
      const out = path.join(distDir, relPath.replace(/\.ts$/, '.js'));
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
