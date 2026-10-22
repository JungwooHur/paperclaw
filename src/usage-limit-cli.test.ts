/**
 * Ties the usage-limit classifier to the CLI that actually writes the refusal
 * and the error messages it returns in place of an answer.
 *
 * Both come from the CLI bundled with the agent SDK the container pins, read
 * out of its code by scripts/extract-usage-limit-sentences.ts. The pin is
 * exact and must match the version they were read from, so an SDK bump fails
 * here until they are read again from the new bundle — and a reworded refusal
 * then fails the classifier check instead of going out to WhatsApp as a reply.
 */
import fs from 'fs';
import { fileURLToPath } from 'url';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  classifyUsageLimit,
  isAgentAnswer,
  isUsageLimit,
} from './usage-limit.js';

const SDK = '@anthropic-ai/claude-agent-sdk';
const HOUR = 60 * 60 * 1000;

interface Sentences {
  sdkVersion: string;
  zone: string;
  sentences: { text: string; now: string; resetsAt: string | null }[];
  /** Templates: {message} and {value} are the parts the CLI fills in. */
  errors: string[];
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

/**
 * An error template filled in the way the CLI fills it in. A part that opens
 * the template is a count ("3 images exceed the API limit …").
 */
function filledIn(template: string): string {
  return template
    .replace(/^\{(?:message|value)\}/, '3')
    .replaceAll(
      '{message}',
      '401 {"type":"error","error":{"type":"authentication_error","message":"Invalid bearer token"}}',
    )
    .replaceAll('{value}', '6.2 MB');
}

/** The same template with nothing to fill in: an Error with no message. */
function filledInEmpty(template: string): string {
  return template.replaceAll('{message}', '').replaceAll('{value}', '');
}

describe('every error message that CLI can write as a result', () => {
  it('is read out of it whole, with the parts it fills in marked', () => {
    expect(fixture.errors.length).toBeGreaterThan(0);
    expect(fixture.errors.some((t) => /\{(?:message|value)\}/.test(t))).toBe(
      true,
    );
    expect(fixture.errors.some((t) => !/\{(?:message|value)\}/.test(t))).toBe(
      true,
    );
  });

  it('is not taken for an answer', () => {
    for (const template of fixture.errors) {
      for (const text of [filledIn(template), filledInEmpty(template)]) {
        expect(isAgentAnswer(text), text).toBe(false);
      }
    }
  });

  it('is only the whole sentence: an answer may start with its words', () => {
    // Every text after "API Error: " is the CLI's, and so is whatever follows
    // a template's last filled-in part. Any other sentence ends where the
    // CLI ends it, so more text after it is the agent's.
    for (const template of fixture.errors) {
      if (template.startsWith('API Error: ')) continue;
      if (/\{(?:message|value)\}$/.test(template)) continue;
      const text = `${filledIn(template)} I used the PDF instead: section 3 is uploaded.`;
      expect(isAgentAnswer(text), text).toBe(true);
    }
    for (const text of [
      'Request timed out on arXiv twice, so I used the PDF instead. Summary: the method trains in two stages.',
      'Not logged in to Notion from this container; the summary is below.',
    ]) {
      expect(isAgentAnswer(text), text).toBe(true);
    }
  });

  it('is only the whole sentence: an answer may end by quoting it', () => {
    for (const template of fixture.errors) {
      for (const preamble of [
        'Note:',
        'Section 3 is uploaded. The figures failed:',
        '그림 업로드 중 오류가 났습니다:\n-',
      ]) {
        const text = `${preamble} ${filledIn(template)}`;
        expect(isAgentAnswer(text), text).toBe(true);
      }
    }
  });
});

describe('the "API Error:" head', () => {
  afterEach(() => {
    vi.doUnmock('./usage-limit-cli-sentences.json');
    vi.resetModules();
  });

  it('keeps an error from passing for an answer without the bundle templates', async () => {
    // Today the bundle's "API Error: {message}" covers every text the head
    // does. A bundle that drops it leaves the head as the guard, so the head
    // is checked here on its own.
    vi.resetModules();
    vi.doMock('./usage-limit-cli-sentences.json', () => ({
      default: { ...fixture, errors: [] },
    }));
    const { isAgentAnswer: headOnly } = await import('./usage-limit.js');
    for (const text of [
      'API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}',
      'API Error: ',
      'API Error:',
    ]) {
      expect(headOnly(text), text).toBe(false);
    }
    expect(headOnly('It describes the method.')).toBe(true);
  });
});
