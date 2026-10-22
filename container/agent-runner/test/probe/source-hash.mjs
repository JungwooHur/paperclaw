// Ties the probe fixture to the code that wrote it. The probe hashes the runner
// source it compiled and itself into the fixture, and
// turn-attribution-probe.test.ts hashes the same directories of the checkout:
// a change to the runner, an output path that skips the stamp included, fails
// there until scripts/probe-turn-attribution.sh runs again.
import { createHash } from 'crypto';
import fs from 'fs';
import path from 'path';

/**
 * sha256 over every file under each directory, by its name in `dirs` plus its
 * path inside it, and its bytes.
 * @param {Record<string, string>} dirs name -> directory
 * @returns {string}
 */
export function sourceHash(dirs) {
  const hash = createHash('sha256');
  for (const name of Object.keys(dirs).sort()) {
    for (const file of listFiles(dirs[name], '')) {
      hash.update(`${name}/${file}\0`);
      hash.update(fs.readFileSync(path.join(dirs[name], file)));
      hash.update('\0');
    }
  }
  return hash.digest('hex');
}

/** Every file under `dir`, by its path from there, in a fixed order. */
function listFiles(dir, prefix) {
  return fs
    .readdirSync(path.join(dir, prefix), { withFileTypes: true })
    .map((entry) => entry.name)
    .sort()
    .flatMap((name) => {
      const relative = path.posix.join(prefix, name);
      return fs.statSync(path.join(dir, relative)).isDirectory()
        ? listFiles(dir, relative)
        : [relative];
    });
}
