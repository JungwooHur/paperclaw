/**
 * Ties turn attribution to the CLI that acknowledges the input.
 *
 * turn-attribution-probe.json is what the compiled runner wrote when
 * scripts/probe-turn-attribution.sh ran it in the agent image, with no network,
 * against a fake Messages API. It must come from the SDK the runner pins, so an
 * SDK bump fails here until the probe is run again, and a CLI that acknowledges
 * input differently then fails the cases below instead of misattributing
 * answers in production.
 */
import fs from 'fs';
import { fileURLToPath } from 'url';

import { describe, expect, it } from 'vitest';

import type { Turn } from '../src/turn-attribution.js';

interface ProbeOutput {
  case: string;
  status: string;
  result: string | null;
  turn: Turn | null;
  subtype: string | null;
}

function readJson<T>(relative: string): T {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  return JSON.parse(fs.readFileSync(file, 'utf-8')) as T;
}

const fixture = readJson<{
  sdkVersion: string;
  outputs: ProbeOutput[];
  runnerNotes: string[];
}>('./turn-attribution-probe.json');
const runnerPackage = readJson<{ dependencies: Record<string, string> }>(
  '../package.json',
);

describe('the turn attribution probe', () => {
  it('ran the SDK the runner pins', () => {
    // On an SDK bump: rebuild the image, then scripts/probe-turn-attribution.sh
    expect(fixture.sdkVersion).toBe(
      runnerPackage.dependencies['@anthropic-ai/claude-agent-sdk'],
    );
  });

  it('saw each result answer exactly the inputs its turn took', () => {
    const seen = fixture.outputs.map((o) => ({
      case: o.case,
      subtype: o.subtype,
      text: o.result !== null,
      turn: o.turn,
    }));
    expect(seen).toEqual([
      // The stdin prompt and the file waiting at start-up are one push.
      {
        case: 'start-up drain',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-start', 'r-leftover'] },
      },
      // A file with no ref is named by its file name.
      {
        case: 'plain',
        subtype: 'success',
        text: true,
        turn: { inputs: ['0001-plain.json'] },
      },
      // A second container resumes the session, as every later one does.
      {
        case: 'start-up drain, resumed session',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-resume', 'r-leftover-2'] },
      },
      // Sent while a tool ran: the CLI merged it into the running turn.
      {
        case: 'merged follow-up',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-tool', 'r-merged'] },
      },
      // Sent during a turn's last API call: that turn does not claim it...
      {
        case: 'turn running when a follow-up came',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-slow'] },
      },
      // ...the next one does.
      {
        case: 'follow-up as its own turn',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-own'] },
      },
      // No text is still an answer, and still names its input.
      {
        case: 'textless',
        subtype: 'success',
        text: false,
        turn: { inputs: ['r-empty'] },
      },
      // The CLI fails the turn before it yields any message, so it never
      // acknowledges the input it took: the result names none, and the input
      // is logged when the query ends (runnerNotes).
      {
        case: 'error before any message',
        subtype: 'error_during_execution',
        text: false,
        turn: { inputs: [] },
      },
      {
        case: 'background dispatch',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-bg'] },
      },
      // The runner's own reminder answers no input.
      {
        case: 'nudge-only',
        subtype: 'success',
        text: true,
        turn: { inputs: [], nudge: true },
      },
    ]);
  });

  it('logged only the input the failed turn took as unanswered', () => {
    expect(fixture.runnerNotes).toEqual([
      'Input no result answered: ["r-nothing"] (never acknowledged)',
    ]);
  });
});
