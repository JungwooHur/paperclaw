import fs from 'fs';
import { fileURLToPath } from 'url';

import { describe, expect, it } from 'vitest';

/** Byte budget of each file an agent loads on every call, by repo path. */
const BUDGETS: Record<string, number> = {
  'CLAUDE.md': 7_700,
  'groups/main/CLAUDE.md': 109_000,
  'groups/global/CLAUDE.md': 2_400,
};

/** An ISO calendar date; a timestamp (date then `T`) does not match. */
const DATE = /(?<!\d)\d{4}-\d{2}-\d{2}(?![\dT])/;

/** The Notion API version header, any quoting; a version name, not a date. */
const API_VERSION = /Notion-Version['"]?:\s*['"]?\d{4}-\d{2}-\d{2}/gi;

describe('always-loaded instruction files', () => {
  for (const [file, budget] of Object.entries(BUDGETS)) {
    const text = fs.readFileSync(
      fileURLToPath(new URL(`../${file}`, import.meta.url)),
      'utf-8',
    );

    it(`${file} is at most ${budget} bytes`, () => {
      expect(Buffer.byteLength(text, 'utf-8'), file).toBeLessThanOrEqual(
        budget,
      );
    });

    it(`${file} contains no date`, () => {
      const dated = text
        .split('\n')
        .map((line, i) => ({ line, at: `${file}:${i + 1}` }))
        .filter(({ line }) => DATE.test(line.replace(API_VERSION, '')))
        .map(({ line, at }) => `${at}: ${line.trim()}`);
      expect(dated).toEqual([]);
    });
  }
});
