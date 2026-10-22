/**
 * Keeps each group's copy of the agent runner in step with the bundled one.
 *
 * A group's container compiles `data/sessions/<group>/agent-runner-src/`, not
 * `container/agent-runner/src/`, so the agent can change its own runner without
 * touching any other group. Seeded once and never refreshed, that copy kept every
 * existing group on the runner it first started with: a runner fix reached new
 * groups only.
 *
 * Now the copy is checked at every container start against a marker that
 * records what it was seeded from. A copy that still matches the marker was
 * never edited, so it is replaced when the bundle changes. A copy that does not
 * match was edited in place and is kept: an agent's edit is never overwritten.
 *
 * The marker lives beside the copy, not inside it, because the copy is mounted
 * into the container writable. Outside the mount, no agent can edit it. The main
 * group can still read it, like all of `data/`, through its read-only mount of
 * the project root.
 *
 * The bundle is the runner the host's build staged (stage-runner.ts), so a
 * runner change reaches groups with the restart that builds it, as a host change
 * does.
 */
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';

import { logger } from './logger.js';

/** What a container start did to a group's runner copy. */
export type RunnerSeedOutcome =
  /** There was no copy, so the bundle was copied in. */
  | 'seeded'
  /** The copy is the bundle and the marker says so: nothing to do. */
  | 'current'
  /** The copy is the bundle but the marker was missing or stale: recorded. */
  | 'adopted'
  /** The copy was unedited and the bundle changed: replaced. */
  | 'refreshed'
  /** The copy differs from what was seeded, or nothing was recorded: kept. */
  | 'edited'
  /** No bundled runner to seed from: the copy was left as it was. */
  | 'no-bundle'
  /** Hashing, copying or swapping failed: the copy was left as it was. */
  | 'failed';

/** The marker that records what a copy was seeded from, beside the copy. */
export function seedMarkerPath(copyDir: string): string {
  return `${copyDir}.seed.json`;
}

function sha256(data: string | Buffer): string {
  return crypto.createHash('sha256').update(data).digest('hex');
}

/** Short enough to read in a log line, long enough not to collide here. */
function short(hash: string): string {
  return hash.slice(0, 12);
}

/**
 * A content hash of a directory tree.
 *
 * Built from the sorted relative paths and what each one holds, so it does not
 * depend on mtimes or on the order readdir returns entries in: two trees with
 * the same files hash the same however they were written. Directories count as
 * entries, so an added empty directory is an edit. A symlink is hashed by its
 * target, never followed. Anything else (a socket, a FIFO) throws, which the
 * caller reads as "cannot tell", never as "unedited".
 */
export function hashTree(dir: string): string {
  const entries: [string, string, string][] = [];
  const walk = (rel: string): void => {
    for (const entry of fs.readdirSync(path.join(dir, rel), {
      withFileTypes: true,
    })) {
      const relPath = rel ? `${rel}/${entry.name}` : entry.name;
      const full = path.join(dir, relPath);
      if (entry.isDirectory()) {
        entries.push([relPath, 'dir', '']);
        walk(relPath);
      } else if (entry.isFile()) {
        entries.push([relPath, 'file', sha256(fs.readFileSync(full))]);
      } else if (entry.isSymbolicLink()) {
        entries.push([relPath, 'link', fs.readlinkSync(full)]);
      } else {
        throw new Error(`not a file, directory or symlink: ${full}`);
      }
    }
  };
  walk('');
  entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  // JSON keeps every field apart, whatever characters a name holds.
  return sha256(JSON.stringify(entries));
}

/** The hash the marker records, or null when it is missing or unreadable. */
function readSeededHash(markerPath: string): string | null {
  try {
    const marker = JSON.parse(fs.readFileSync(markerPath, 'utf-8'));
    return typeof marker?.hash === 'string' ? marker.hash : null;
  } catch {
    return null;
  }
}

function writeMarker(markerPath: string, hash: string): void {
  const tmp = `${markerPath}.tmp`;
  fs.writeFileSync(
    tmp,
    JSON.stringify({ hash, seededAt: new Date().toISOString() }, null, 2) +
      '\n',
  );
  fs.renameSync(tmp, markerPath);
}

/**
 * Removes a sibling an install left behind, logging instead of throwing.
 *
 * It only ever holds a bundle or an unedited copy, since an edited copy is
 * never swapped out, so nothing is lost by removing it. When it cannot be
 * removed (a directory the container made read-only, say), the owner is told
 * and the install goes on without it: a leftover must never block a refresh.
 */
function removeLeftover(dir: string, group: string): boolean {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
    return true;
  } catch (err) {
    logger.warn(
      { group, path: dir, err },
      'Could not remove a leftover agent-runner copy. Nothing uses it, so ' +
        'remove it by hand; refreshes go on without it.',
    );
    return false;
  }
}

/** `base` emptied, or a fresh name beside it when `base` cannot be emptied. */
function freeSibling(base: string, group: string): string {
  return removeLeftover(base, group) ? base : `${base}-${Date.now()}`;
}

/**
 * Puts a fresh copy of the bundle at `copyDir`, never leaving a half tree there.
 *
 * The bundle is copied into a sibling first and swapped in by rename, so a
 * failure while copying leaves the current copy untouched, and a failed swap
 * puts it back. Fixed sibling names are safe because only one container per
 * group runs at a time (`group-queue.ts`); a sibling left by a crash is removed
 * first.
 *
 * Returns the hash of the tree it installed, taken from the copy and not from
 * the bundle: the bundle can change while it is copied (a `git pull` writing the
 * runner files), and the marker has to describe what is actually in place, or
 * an unedited copy reads as edited from then on.
 */
function installBundle(
  bundleDir: string,
  copyDir: string,
  group: string,
): string {
  const next = freeSibling(`${copyDir}.next`, group);
  const prev = freeSibling(`${copyDir}.prev`, group);
  let installed: string;
  try {
    // verbatimSymlinks: by default a relative link is rewritten to an
    // absolute host path, which does not exist inside the container and
    // hashes unlike the bundle's link.
    fs.cpSync(bundleDir, next, { recursive: true, verbatimSymlinks: true });
    installed = hashTree(next);
  } catch (err) {
    removeLeftover(next, group);
    throw err;
  }
  const hadCopy = fs.existsSync(copyDir);
  if (hadCopy) fs.renameSync(copyDir, prev);
  try {
    fs.renameSync(next, copyDir);
  } catch (err) {
    if (hadCopy) fs.renameSync(prev, copyDir);
    removeLeftover(next, group);
    throw err;
  }
  removeLeftover(prev, group);
  return installed;
}

function refresh(
  bundleDir: string,
  copyDir: string,
  group: string,
): RunnerSeedOutcome {
  if (!fs.existsSync(bundleDir)) {
    logger.warn(
      { group, bundle: bundleDir },
      `No bundled agent runner at ${bundleDir}, so this group's copy is used ` +
        `as it is. A host running from dist/ reads the runner its build ` +
        `staged: run \`npm run build\` and restart.`,
    );
    return 'no-bundle';
  }
  const markerPath = seedMarkerPath(copyDir);
  const bundleHash = hashTree(bundleDir);

  if (!fs.existsSync(copyDir)) {
    const installed = installBundle(bundleDir, copyDir, group);
    writeMarker(markerPath, installed);
    logger.info(
      { group, hash: short(installed) },
      'Seeded the agent-runner copy from the bundled runner',
    );
    return 'seeded';
  }

  const copyHash = hashTree(copyDir);
  const seededHash = readSeededHash(markerPath);

  // Equal to the bundle: whatever the marker says, nothing is lost by tracking
  // the bundle from here. This also covers a copy seeded before markers
  // existed, and a marker write that failed after a swap.
  if (copyHash === bundleHash) {
    if (seededHash === bundleHash) return 'current';
    writeMarker(markerPath, bundleHash);
    logger.info(
      { group, hash: short(bundleHash) },
      'Agent-runner copy matches the bundled runner; recorded it as seeded',
    );
    return 'adopted';
  }

  if (copyHash === seededHash) {
    const installed = installBundle(bundleDir, copyDir, group);
    writeMarker(markerPath, installed);
    logger.info(
      { group, from: short(copyHash), to: short(installed) },
      'Refreshed the unedited agent-runner copy from the bundled runner',
    );
    return 'refreshed';
  }

  // Edited in place, or seeded before markers existed and different from the
  // bundle. Runtime cannot tell an old seed from an edit, so both are kept.
  // Warn only when the group is missing a bundle change: an edited copy whose
  // bundle has not moved since seeding is a customisation, and a warning on
  // every start for it would teach the reader to skip the warning.
  if (seededHash === bundleHash) {
    logger.debug(
      { group, copy: short(copyHash) },
      'Agent-runner copy was edited in place; keeping it',
    );
  } else {
    logger.warn(
      {
        group,
        copy: short(copyHash),
        seeded: seededHash ? short(seededHash) : null,
        bundle: short(bundleHash),
      },
      `Agent-runner copy differs from what was seeded, so it is kept and this ` +
        `group does not get the bundled runner. To take the bundled runner, ` +
        `delete ${copyDir}; the next start seeds it again and the edits are lost.`,
    );
  }
  return 'edited';
}

/**
 * Seeds or refreshes a group's runner copy before its container starts.
 *
 * Never throws: a failure to hash, copy or swap is logged and the container
 * starts with the copy as it is, which is what it did before this check existed.
 */
export function refreshRunnerCopy(
  bundleDir: string,
  copyDir: string,
  group: string,
): RunnerSeedOutcome {
  try {
    return refresh(bundleDir, copyDir, group);
  } catch (err) {
    logger.error(
      { group, copyDir, err },
      'Could not check the agent-runner copy against the bundled runner; ' +
        'starting with the copy as it is',
    );
    return 'failed';
  }
}
