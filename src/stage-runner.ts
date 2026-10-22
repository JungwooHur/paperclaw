/**
 * Ships the agent runner with the build, so the runner groups get is as old as
 * the host that hands it to them.
 *
 * A group's runner copy is refreshed from a bundle at every container start
 * (runner-seed.ts). Read straight from `container/agent-runner/src`, that bundle
 * moved with the checkout and not with the build: a `git pull`, a half-resolved
 * merge, or a runner change that needs a package the image lacks reached every
 * unedited group at its next container start, with no restart, beside a host
 * still running the old `dist/`.
 *
 * So the last step of `npm run build` stages a copy of the runner sources in
 * `dist/agent-runner-src/`, and a host running from `dist/` refreshes groups from
 * that copy. The build script runs this only after tsc succeeded, and a failed
 * tsc writes nothing (`noEmitOnError`), so a failed build leaves the previous
 * host and the previous runner in place together. `npm run dev` runs the
 * sources and keeps reading the checkout.
 *
 * Run as `node dist/stage-runner.js` by the build script. It imports nothing
 * from this project, so it runs in a build with nothing else to load.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

/** The runner sources in the checkout at `projectRoot`. */
export function runnerSourceDir(projectRoot: string): string {
  return path.join(projectRoot, 'container', 'agent-runner', 'src');
}

/** The runner the build staged beside the compiled host in `distDir`. */
export function stagedRunnerDir(distDir: string): string {
  return path.join(distDir, 'agent-runner-src');
}

/**
 * Where a host module at `moduleUrl` reads the bundled runner from.
 *
 * Decided by where the module lives, as build-freshness.ts decides what to
 * check: compiled into `dist/`, it reads what that build staged; anywhere else
 * (`npm run dev` runs `src/` itself) it reads `checkoutDir`. Never falls back
 * from one to the other: a compiled host with nothing staged has to say so,
 * not quietly hand out the checkout.
 */
export function runnerBundleDir(
  moduleUrl: string,
  checkoutDir: string,
): string {
  const here = path.dirname(fileURLToPath(moduleUrl));
  return path.basename(here) === 'dist' ? stagedRunnerDir(here) : checkoutDir;
}

/**
 * Copies the runner sources of `projectRoot` to its staged runner, whole.
 *
 * The copy goes to a sibling and is swapped in by rename, so a host starting a
 * container meanwhile reads either the old copy or the new one, never half of
 * one, and a failed copy leaves the old one as it was. Throws when the sources
 * are missing or the copy fails, so the build fails and says why. Returns the
 * staged directory.
 */
export function stageRunner(projectRoot: string): string {
  const source = runnerSourceDir(projectRoot);
  const staged = stagedRunnerDir(path.join(projectRoot, 'dist'));
  if (!fs.statSync(source).isDirectory()) {
    throw new Error(`not a directory: ${source}`);
  }
  const next = `${staged}.next`;
  const prev = `${staged}.prev`;
  fs.rmSync(next, { recursive: true, force: true });
  fs.rmSync(prev, { recursive: true, force: true });
  try {
    // As runner-seed.ts copies it on: a relative link stays relative.
    fs.cpSync(source, next, { recursive: true, verbatimSymlinks: true });
  } catch (err) {
    fs.rmSync(next, { recursive: true, force: true });
    throw err;
  }
  const hadStaged = fs.existsSync(staged);
  if (hadStaged) fs.renameSync(staged, prev);
  try {
    fs.renameSync(next, staged);
  } catch (err) {
    if (hadStaged) fs.renameSync(prev, staged);
    throw err;
  }
  fs.rmSync(prev, { recursive: true, force: true });
  return staged;
}

/**
 * Whether node was asked to run this file, rather than a host module or a test
 * importing it.
 *
 * Compared as real paths: `import.meta.url` escapes `#` and `%` where argv
 * does not, and names the file behind a symlink where argv names the link.
 * Compared as URLs, either made a build skip staging and still exit 0.
 */
function isDirectRun(): boolean {
  if (!process.argv[1]) return false;
  try {
    return fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}

if (isDirectRun()) {
  const projectRoot = path.join(
    path.dirname(fileURLToPath(import.meta.url)),
    '..',
  );
  try {
    const staged = stageRunner(projectRoot);
    console.log(
      `Staged the agent runner in ${path.relative(projectRoot, staged)}`,
    );
  } catch (err) {
    console.error('Could not stage the agent runner:', err);
    process.exitCode = 1;
  }
}
