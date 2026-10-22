import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { describe, expect, it } from 'vitest';

/**
 * docs/DEBUG_CHECKLIST.md explains each router_state key on a line of its
 * own, `#   \`key\`  what it holds`, beside the query that reads it. A key it
 * does not explain reads as one it does, and a key it explains that nothing
 * writes any more is one to go looking for.
 */
describe('the debug checklist', () => {
  const srcDir = fileURLToPath(new URL('.', import.meta.url));
  const checklist = fs.readFileSync(
    fileURLToPath(new URL('../docs/DEBUG_CHECKLIST.md', import.meta.url)),
    'utf-8',
  );
  // Every key the code uses: a literal passed to getRouterState or
  // setRouterState, or a `*_KEY` constant, which is how every other is named.
  const keys = new Set<string>();
  for (const file of fs.readdirSync(srcDir, { recursive: true }) as string[]) {
    if (!file.endsWith('.ts') || file.endsWith('.test.ts')) continue;
    const code = fs.readFileSync(path.join(srcDir, file), 'utf-8');
    for (const m of code.matchAll(
      /(?:get|set)RouterState\(\s*(['"])([a-z_]+)\1/g,
    )) {
      keys.add(m[2]);
    }
    for (const m of code.matchAll(/const [A-Z_]+_KEY = (['"])([a-z_]+)\1/g)) {
      keys.add(m[2]);
    }
  }
  const explained = [...checklist.matchAll(/^#\s+`([a-z_]+)`/gm)].map(
    (m) => m[1],
  );

  it('explains every router_state key the code uses', () => {
    // The cursors, the usage-limit window and turn attribution at least.
    expect(keys.size).toBeGreaterThanOrEqual(10);
    for (const key of keys) expect(explained, key).toContain(key);
  });

  it('explains no router_state key the code does not use', () => {
    for (const name of explained) expect(keys.has(name), name).toBe(true);
  });
});
