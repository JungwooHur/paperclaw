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

import { isTeamPrompt, type Turn } from '../src/turn-attribution.js';

interface ProbeOutput {
  case: string;
  status: string;
  result: string | null;
  turn: Turn | null;
  attribution?: string;
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
  reusedUuid?: { events: string[]; modelSaw: string[] };
  cliShutdownPrompt?: string | null;
}>('./turn-attribution-probe.json');
const runnerPackage = readJson<{ dependencies: Record<string, string> }>(
  '../package.json',
);

/** The container whose CLI ran without --replay-user-messages. */
const REPLAY_BROKEN = [
  'textless result with replay broken',
  'answer after attribution went off',
  'session marker after attribution went off',
  'answer in a later query after attribution went off',
  'error after attribution went off',
];

describe('the turn attribution probe', () => {
  it('ran the SDK the runner pins', () => {
    // On an SDK bump: rebuild the image, then scripts/probe-turn-attribution.sh
    expect(fixture.sdkVersion).toBe(
      runnerPackage.dependencies['@anthropic-ai/claude-agent-sdk'],
    );
  });

  it('saw each result answer exactly the inputs its turn took', () => {
    const seen = fixture.outputs
      .filter((o) => !REPLAY_BROKEN.includes(o.case))
      .map((o) => ({
        case: o.case,
        subtype: o.subtype,
        text: o.result !== null,
        turn: o.turn,
        // Present only once attribution is off: on none of these.
        ...('attribution' in o ? { attribution: o.attribution } : {}),
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
        turn: { inputs: [], internal: true },
      },
      // A slash command that starts a turn is never acknowledged under its
      // own uuid: a skill is replayed expanded and a local command's output
      // replayed, each under the CLI's uuid, and an unknown command replays
      // nothing. The turn that took it answers it all the same.
      {
        case: 'slash command: skill',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-skill'] },
      },
      {
        case: 'slash command: local',
        subtype: 'success',
        text: false,
        turn: { inputs: ['r-cost'] },
      },
      {
        case: 'slash command: unknown',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-unknown'] },
      },
      // Sent during a turn's last API call: the CLI runs the command after
      // that turn, so the running turn does not claim it...
      {
        case: 'turn running when a slash command came',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-cmdslow'] },
      },
      // ...its own turn does.
      {
        case: 'slash command as its own turn',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-late-command'] },
      },
      // None of them switched attribution off.
      {
        case: 'follow-up after the slash commands',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-after-slash'] },
      },
      {
        case: 'team created',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-team'] },
      },
      // A team member's message starts a lead turn the CLI queued itself,
      // replayed under its own uuid: no input, and marked internal.
      {
        case: 'agent-team message',
        subtype: 'success',
        text: true,
        turn: { inputs: [], internal: true },
      },
      {
        case: 'follow-up after the team message',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-after-team'] },
      },
      // A member's message reached the lead during a host turn, and a bare
      // command landed behind it. The CLI runs the team message first, and
      // that turn start is counted as the command's, the oldest push; the
      // turn replayed only the team message, so the command waits for the
      // next turn, which answers it.
      {
        case: 'team created, before an unknown command',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-team-2'] },
      },
      {
        case: 'turn running when a team message and an unknown command came',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-team-slow-2'] },
      },
      {
        case: 'team message ahead of an unknown command',
        subtype: 'success',
        text: true,
        turn: { inputs: [], internal: true },
      },
      // It replays nothing, and is not taken for broken replay.
      {
        case: 'unknown command behind a team message',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-unknown-behind-team'] },
      },
      {
        case: 'follow-up after the unknown command',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-after-unknown'] },
      },
      {
        case: 'team created, before a skill',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-team-3'] },
      },
      {
        case: 'turn running when a team message and a skill came',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-team-slow-3'] },
      },
      {
        case: 'team message ahead of a skill',
        subtype: 'success',
        text: true,
        turn: { inputs: [], internal: true },
      },
      // The host sent it, so it is not internal.
      {
        case: 'skill behind a team message',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-skill-behind-team'] },
      },
      {
        case: 'follow-up after the skill',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-after-skill'] },
      },
      // A scheduled run's turn hit the runner's maxTurns after the CLI had
      // acknowledged its prompt. The failed result answers nothing...
      {
        case: 'turn failed after its acknowledgement',
        subtype: 'error_max_turns',
        text: false,
        turn: { inputs: [] },
      },
      // ...and the next success of the same query answers that prompt first.
      {
        case: 'success after a failed turn',
        subtype: 'success',
        text: true,
        turn: { inputs: ['r-max-turns', 'r-after-fail'] },
      },
    ]);
  });

  it('saw every output say attribution is off from the one the guard tripped on', () => {
    // The CLI ran through a `node` that drops --replay-user-messages, so it
    // acknowledged nothing. The guard trips on the first result, a textless
    // one that has no `turn` either way, so only the field tells it from the
    // session marker of a container whose attribution is on. Every output
    // after it says so too: the session marker (the CLI ended cleanly, so
    // the query ended), the next query's result, and the error output (the
    // CLI was then killed).
    const seen = fixture.outputs
      .filter((o) => REPLAY_BROKEN.includes(o.case))
      .map((o) => ({
        case: o.case,
        status: o.status,
        subtype: o.subtype,
        text: o.result !== null,
        turn: o.turn,
        attribution: o.attribution,
      }));
    const off = { turn: null, attribution: 'off' };
    expect(seen).toEqual([
      {
        case: 'textless result with replay broken',
        status: 'success',
        subtype: 'success',
        text: false,
        ...off,
      },
      {
        case: 'answer after attribution went off',
        status: 'success',
        subtype: 'success',
        text: true,
        ...off,
      },
      {
        case: 'session marker after attribution went off',
        status: 'success',
        subtype: null,
        text: false,
        ...off,
      },
      {
        case: 'answer in a later query after attribution went off',
        status: 'success',
        subtype: 'success',
        text: true,
        ...off,
      },
      {
        case: 'error after attribution went off',
        status: 'error',
        subtype: null,
        text: false,
        ...off,
      },
    ]);
  });

  it('saw the CLI run the bare commands as commands and the team turn itself', () => {
    // The fake API answers these turns by what reached the model, so a CLI
    // that took a command as plain text, or folded the team member's message
    // into another turn, fails here.
    const answer = (name: string) =>
      fixture.outputs.find((o) => o.case === name)?.result;
    // Only an expanded skill carries the skill's body.
    expect(answer('slash command: skill')).toBe('ANSWER skill body');
    expect(answer('slash command: unknown')).toBe(
      'Unknown skill: nonexistent-cmd',
    );
    expect(answer('slash command as its own turn')).toBe(
      'Unknown skill: nonexistent-cmd',
    );
    expect(answer('agent-team message')).toBe('ANSWER team message');
    // The member's message did come first, and each command after it.
    for (const command of ['an unknown command', 'a skill']) {
      expect(
        answer(`turn running when a team message and ${command} came`),
      ).toBe('ANSWER PROBE-TEAMSLOW');
      expect(answer(`team message ahead of ${command}`)).toBe(
        'ANSWER team message',
      );
    }
    expect(answer('unknown command behind a team message')).toBe(
      'Unknown skill: nonexistent-cmd',
    );
    expect(answer('skill behind a team message')).toBe('ANSWER skill body');
    // The request the success after a failed turn made carried that turn's
    // prompt, which is why that success answers it.
    expect(answer('success after a failed turn')).toBe(
      'ANSWER PROBE-AFTERFAIL saw PROBE-MAXTURNS',
    );
  });

  it('turned attribution off only where replay was broken, and logged only the unacknowledged input as unanswered', () => {
    expect(fixture.runnerNotes).toEqual([
      'Input no result answered: ["r-nothing"] (never acknowledged)',
      'Turn attribution off for this container: a success result followed no replayed input, so replay is not working',
    ]);
  });

  it('read the shutdown prompt the CLI injects as a team prompt', () => {
    // No case here makes the CLI send it, so the probe reads it out of the
    // bundle, as the constant passed at its injection site. A
    // reworded one fails here: unrecognised, a shutdown turn run ahead of a
    // queued command would be taken for that command's turn.
    expect(fixture.cliShutdownPrompt).toEqual(expect.any(String));
    expect(isTeamPrompt(fixture.cliShutdownPrompt!)).toBe(true);
  });

  it('saw the CLI acknowledge a reused uuid without running it', () => {
    // Why every push needs a fresh uuid. Driven through the SDK directly, as
    // the runner never reuses one: a uuid the CLI has already seen comes back
    // at once as an acknowledgement carrying the new text, but the CLI
    // neither runs it nor sends it to the model. The next result would claim
    // an input nobody answered, so a reused uuid is a false acknowledgement.
    expect(fixture.reusedUuid).toEqual({
      events: [
        'push PROBE-FIRST as u1',
        'ack PROBE-FIRST as u1',
        'result ANSWER PROBE-FIRST',
        'push PROBE-REUSED as u1',
        'ack PROBE-REUSED as u1',
        'push PROBE-FRESH as u2',
        'ack PROBE-FRESH as u2',
        'result ANSWER PROBE-FRESH',
      ],
      modelSaw: ['PROBE-FIRST', 'PROBE-FRESH'],
    });
  });
});
