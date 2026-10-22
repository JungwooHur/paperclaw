/**
 * Ties the usage-limit classifier to the CLI that actually writes the refusal.
 *
 * The sentences come from the CLI bundled with the agent SDK the container
 * pins, read out of its code by scripts/extract-usage-limit-sentences.ts. The
 * pin is exact and must match the version the sentences were read from, so an
 * SDK bump fails here until they are read again from the new bundle — and a
 * reworded refusal then fails the classifier check instead of going out to
 * WhatsApp as a reply.
 */
import fs from 'fs';
import { fileURLToPath } from 'url';

import { describe, expect, it } from 'vitest';

import { classifyUsageLimit, isUsageLimit } from './usage-limit.js';

const SDK = '@anthropic-ai/claude-agent-sdk';
const HOUR = 60 * 60 * 1000;

interface Sentences {
  sdkVersion: string;
  zone: string;
  sentences: { text: string; now: string; resetsAt: string | null }[];
}

function readJson<T>(relative: string): T {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  return JSON.parse(fs.readFileSync(file, 'utf-8')) as T;
}

const fixture = readJson<Sentences>('./usage-limit-cli-sentences.json');
const runnerPackage = readJson<{ dependencies: Record<string, string> }>(
  '../container/agent-runner/package.json',
);
const runnerLock = readJson<{
  packages: Record<
    string,
    { version?: string; dependencies?: Record<string, string> }
  >;
}>('../container/agent-runner/package-lock.json');

describe('the CLI the container runs', () => {
  const pin = runnerPackage.dependencies[SDK];

  it('is pinned to one exact SDK version', () => {
    expect(pin).toMatch(/^\d+\.\d+\.\d+$/);
    expect(runnerLock.packages[''].dependencies?.[SDK]).toBe(pin);
    expect(runnerLock.packages[`node_modules/${SDK}`].version).toBe(pin);
  });

  it('is the one the refusal sentences were read from', () => {
    // On an SDK bump: npx tsx scripts/extract-usage-limit-sentences.ts <cli.js>
    expect(fixture.sdkVersion).toBe(pin);
  });
});

describe('every refusal sentence that CLI can write', () => {
  it('has a reset to read in most of them, and none in some', () => {
    expect(fixture.sentences.length).toBeGreaterThan(0);
    expect(fixture.sentences.some((s) => s.resetsAt === null)).toBe(true);
    expect(fixture.sentences.some((s) => s.resetsAt !== null)).toBe(true);
  });

  it('is recognised as a refusal', () => {
    for (const { text } of fixture.sentences) {
      expect(isUsageLimit(text), text).toBe(true);
    }
  });

  it('is read for the reset it states, in the zone it names', () => {
    for (const { text, now, resetsAt } of fixture.sentences) {
      // UTC as the fallback, so a zone the text names is the one used.
      const limit = classifyUsageLimit(text, new Date(now), 'UTC');
      if (resetsAt === null) {
        expect(limit?.stated, text).toBe(false);
        expect(limit?.resetsAt.getTime(), text).toBe(Date.parse(now) + HOUR);
      } else {
        expect(limit?.stated, text).toBe(true);
        expect(limit?.resetsAt.toISOString(), text).toBe(resetsAt);
      }
    }
  });
});
