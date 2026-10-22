/**
 * The runner's record of which inputs each SDK result answered.
 *
 * Kept out of the runner's src/ on purpose: the container entrypoint compiles
 * all of /app/src under `set -e`, so a vitest import there would stop every
 * container before it starts.
 *
 * Each test feeds the calls in the order the runner makes them from the
 * stream: `turnStarted` on a `system/init` (one per turn), `ack` on a
 * replayed user message, `result` on a result.
 */
import { describe, expect, it, vi } from 'vitest';

import {
  SLASH_COMMAND as ROUTER_SLASH_COMMAND,
  formatMessages,
} from '../../../src/router.js';
import {
  SLASH_COMMAND,
  TurnAttribution,
  ipcInputId,
  isSlashCommand,
  isTeamPrompt,
  startupInputIds,
} from '../src/turn-attribution.js';

const UUID_SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** A replay under a uuid the runner never pushed: the CLI made the message. */
const FOREIGN = '00000000-0000-4000-8000-000000000000';
const FOREIGN_2 = '00000000-0000-4000-8000-000000000001';

// What the CLI replays, under its own uuid, for a turn it starts itself in an
// agent team (cli.js print mode): a team member's message to the lead...
const TEAMMATE_MESSAGE =
  '<teammate-message teammate_id="mate" color="blue">\n' +
  '{"type":"idle_notification","from":"mate","idleReason":"available"}\n' +
  '</teammate-message>';
// ...and the prompt it injects when its input closes while teammates are up.
const SHUTDOWN_PROMPT =
  '<system-reminder>\nYou are running in non-interactive mode and cannot ' +
  'return a response to the user until your team is shut down.\n\nYou MUST ' +
  'shut down your team before preparing your final response:\n</system-reminder>' +
  '\n\nShut down your team and prepare your final response for the user.';
// What it replays for a slash command's turn: a skill expanded...
const SKILL_REPLAY =
  '<command-message>probe-skill</command-message>\n' +
  '<command-name>/probe-skill</command-name>\n<command-args>x</command-args>';
// ...and a local command's output.
const LOCAL_REPLAY =
  '<local-command-stdout>Total cost: $0.0000</local-command-stdout>';

describe('a push', () => {
  it('goes out under a fresh uuid every time', () => {
    const attribution = new TurnAttribution();
    const first = attribution.push('a', ['a']);
    const second = attribution.push('a', ['a']);
    expect(first).toMatch(UUID_SHAPE);
    expect(second).toMatch(UUID_SHAPE);
    // The CLI acknowledges a uuid it has seen before without running it.
    expect(second).not.toBe(first);
  });
});

describe('a result', () => {
  it('answers the inputs acknowledged since the previous result', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    // Pushed while a tool ran: merged into the running turn.
    const b = attribution.push('b', ['b']);
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['a', 'b'] });

    const c = attribution.push('c', ['c']);
    attribution.turnStarted();
    attribution.ack(c);
    expect(attribution.result('success')).toEqual({ inputs: ['c'] });
  });

  it('leaves a push acknowledged after it to the next result', () => {
    // Pushed during a turn's last API call: the CLI runs it as its own turn.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    const b = attribution.push('b', ['b']);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('carries every id of a push that delivered several inputs', () => {
    const attribution = new TurnAttribution();
    const start = attribution.push('r0\nf1\nf2', ['r0', 'f1', 'f2']);
    attribution.turnStarted();
    attribution.ack(start);
    expect(attribution.result('success')).toEqual({
      inputs: ['r0', 'f1', 'f2'],
    });
  });

  it('marks a turn only the nudge started as internal, answering no input', () => {
    const attribution = new TurnAttribution();
    const nudge = attribution.push('reminder', [], true);
    attribution.turnStarted();
    attribution.ack(nudge);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });
  });

  it('answers the file a nudge was merged with', () => {
    const attribution = new TurnAttribution();
    const nudge = attribution.push('reminder', [], true);
    attribution.turnStarted();
    const file = attribution.push('f', ['f']);
    attribution.ack(nudge);
    attribution.ack(file);
    expect(attribution.result('success')).toEqual({
      inputs: ['f'],
      internal: true,
    });
  });

  it('counts an input once however often it is acknowledged', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    // A late repeat of a uuid already answered is neither an input nor a
    // message the CLI made.
    attribution.ack(a);
    expect(attribution.result('error_max_turns')).toEqual({ inputs: [] });
  });

  it('answers nothing when an error result follows no acknowledgement', () => {
    // The CLI fails the turn before it yields any message.
    const attribution = new TurnAttribution();
    attribution.push('a', ['a']);
    attribution.turnStarted();
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: [],
    });
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('answers nothing when its turn fails after taking its input, and no later result answers it', () => {
    // The CLI acknowledged the prompt, then failed the turn (a stream that
    // broke after a tool call). It does not send that input again: it stays
    // in the history once, closed by the CLI's own "(no content)" message,
    // so a later turn answers only what it took.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: [],
    });
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
    expect(attribution.endQuery()).toEqual([
      { inputs: ['a'], nudge: false, acked: true, failed: true },
    ]);
  });

  it('credits no failed input to a slash command that makes no model call', () => {
    // A local command's turn after a failed one: it answers itself only.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    attribution.result('error_max_turns');
    attribution.push('/cost', ['r-cost']);
    attribution.turnStarted();
    attribution.ack(FOREIGN, LOCAL_REPLAY);
    expect(attribution.result('success')).toEqual({ inputs: ['r-cost'] });
  });

  it('reports the inputs of failed turns in a row when the query ends', () => {
    const attribution = new TurnAttribution();
    for (const id of ['a', 'b']) {
      const uuid = attribution.push(id, [id]);
      attribution.turnStarted();
      attribution.ack(uuid);
      expect(attribution.result('error_during_execution')).toEqual({
        inputs: [],
      });
    }
    const c = attribution.push('c', ['c']);
    attribution.turnStarted();
    attribution.ack(c);
    expect(attribution.result('success')).toEqual({ inputs: ['c'] });
    expect(attribution.endQuery()).toEqual([
      { inputs: ['a', 'b'], nudge: false, acked: true, failed: true },
    ]);
  });
});

describe('a turn the CLI started itself', () => {
  it('answers no input and is marked internal', () => {
    // An agent-team message: replayed under a uuid the CLI made.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });

    attribution.turnStarted();
    attribution.ack(FOREIGN);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });

    // Replay works, so attribution stays on.
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('is not internal when a pushed input was merged into it', () => {
    const attribution = new TurnAttribution();
    attribution.turnStarted();
    attribution.ack(FOREIGN);
    const b = attribution.push('b', ['b']);
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('leaves a push it did not take to the turn that does', () => {
    // A push was queued when the CLI started its own turn, so that turn
    // start was booked against the push: the push is returned to the queue,
    // or the slash command behind it would be credited one turn early.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.push('/probe-skill x', ['c']);
    attribution.turnStarted();
    attribution.ack(FOREIGN);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });
    attribution.turnStarted();
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.turnStarted();
    attribution.ack(FOREIGN);
    expect(attribution.result('success')).toEqual({ inputs: ['c'] });
  });
});

describe('a team turn the CLI runs ahead of a queued slash command', () => {
  // A team member's message reached the lead during a host turn and was
  // queued; the command landed behind it. The CLI runs the team message
  // first, and that turn start is booked against the command, the oldest push.
  function queueBehindTeamMessage(
    command: string,
    log: (message: string) => void = () => {},
  ): TurnAttribution {
    const attribution = new TurnAttribution(log);
    const slow = attribution.push('slow', ['r-slow']);
    attribution.turnStarted();
    attribution.ack(slow);
    attribution.push(command, ['r-cmd']);
    expect(attribution.result('success')).toEqual({ inputs: ['r-slow'] });
    return attribution;
  }

  it('leaves an unknown command to its own turn, and attribution on', () => {
    const log = vi.fn();
    const attribution = queueBehindTeamMessage('/nonexistent-cmd x', log);
    attribution.turnStarted();
    attribution.ack(FOREIGN, TEAMMATE_MESSAGE);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });
    // The command's own turn replays nothing.
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['r-cmd'] });
    const after = attribution.push('after', ['r-after']);
    attribution.turnStarted();
    attribution.ack(after);
    expect(attribution.result('success')).toEqual({ inputs: ['r-after'] });
    expect(log).not.toHaveBeenCalled();
  });

  it('leaves a skill to its own turn, which is the host input it is', () => {
    const log = vi.fn();
    const attribution = queueBehindTeamMessage('/probe-skill x', log);
    attribution.turnStarted();
    attribution.ack(FOREIGN, TEAMMATE_MESSAGE);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });
    attribution.turnStarted();
    attribution.ack(FOREIGN_2, SKILL_REPLAY);
    expect(attribution.result('success')).toEqual({ inputs: ['r-cmd'] });
    expect(log).not.toHaveBeenCalled();
  });

  it('does the same for the shutdown prompt the CLI injects', () => {
    const attribution = queueBehindTeamMessage('/cost');
    attribution.turnStarted();
    attribution.ack(FOREIGN, [{ type: 'text', text: SHUTDOWN_PROMPT }]);
    expect(attribution.result('success')).toEqual({
      inputs: [],
      internal: true,
    });
    attribution.turnStarted();
    attribution.ack(FOREIGN_2, LOCAL_REPLAY);
    expect(attribution.result('success')).toEqual({ inputs: ['r-cmd'] });
  });

  it('leaves the command queued when the team turn fails', () => {
    const log = vi.fn();
    const attribution = queueBehindTeamMessage('/nonexistent-cmd x', log);
    attribution.turnStarted();
    attribution.ack(FOREIGN, TEAMMATE_MESSAGE);
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: [],
      internal: true,
    });
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['r-cmd'] });
    expect(log).not.toHaveBeenCalled();
  });

  it('keeps the command on a turn that also replayed the command', () => {
    // A team message merged into the skill's own turn does not make that
    // turn the CLI's: the skill's expansion shows the command ran.
    const attribution = queueBehindTeamMessage('/probe-skill x');
    attribution.turnStarted();
    attribution.ack(FOREIGN, SKILL_REPLAY);
    attribution.ack(FOREIGN_2, TEAMMATE_MESSAGE);
    expect(attribution.result('success')).toEqual({ inputs: ['r-cmd'] });
  });
});

describe('a team prompt', () => {
  it('is a team member message or the injected shutdown prompt', () => {
    expect(isTeamPrompt(TEAMMATE_MESSAGE)).toBe(true);
    // Several queued messages are joined into one prompt.
    expect(isTeamPrompt(`${TEAMMATE_MESSAGE}\n\n${TEAMMATE_MESSAGE}`)).toBe(
      true,
    );
    expect(isTeamPrompt(SHUTDOWN_PROMPT)).toBe(true);
  });

  it('is not what a command, the host or the runner sends', () => {
    expect(isTeamPrompt(SKILL_REPLAY)).toBe(false);
    expect(isTeamPrompt(LOCAL_REPLAY)).toBe(false);
    expect(
      isTeamPrompt(
        '<local-command-stderr>Error: no such thing</local-command-stderr>',
      ),
    ).toBe(false);
    expect(isTeamPrompt('<messages>\n<message>hi</message>\n</messages>')).toBe(
      false,
    );
    expect(isTeamPrompt(' <teammate-message teammate_id="x">')).toBe(false);
    // A skill whose body quotes the shutdown prompt is still a skill.
    expect(isTeamPrompt(`${SKILL_REPLAY}\n${SHUTDOWN_PROMPT}`)).toBe(false);
    expect(isTeamPrompt('')).toBe(false);
  });
});

describe('a slash command', () => {
  it('is answered by the turn it started, under no acknowledgement of its own', () => {
    // A skill: the CLI replays the expanded skill under its own uuid.
    const skill = new TurnAttribution();
    skill.push('/probe-skill x', ['r0']);
    skill.turnStarted();
    skill.ack(FOREIGN, SKILL_REPLAY);
    expect(skill.result('success')).toEqual({ inputs: ['r0'] });

    // A local command: its output is replayed under the CLI's uuid, and the
    // result has no text.
    const local = new TurnAttribution();
    local.push('/cost', ['r0']);
    local.turnStarted();
    local.ack(FOREIGN, LOCAL_REPLAY);
    expect(local.result('success')).toEqual({ inputs: ['r0'] });
  });

  it('that the CLI rejects answers it without any replay, and keeps attribution on', () => {
    const log = vi.fn();
    const attribution = new TurnAttribution(log);
    attribution.push('/nonexistent-cmd x', ['r0']);
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['r0'] });
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
    expect(log).not.toHaveBeenCalled();
  });

  it('pushed during a turn is answered by its own turn, not the running one', () => {
    // Sent during the last API call: the CLI runs it after the running turn.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.push('/nonexistent-cmd x', ['c']);
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['c'] });
  });

  it('merged into a running turn is acknowledged like any input', () => {
    // Sent while a tool ran: the CLI merges it as plain text, unexpanded.
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    const c = attribution.push('/cost', ['c']);
    attribution.ack(c);
    expect(attribution.result('success')).toEqual({ inputs: ['a', 'c'] });
    // Nothing is left for a later turn to claim.
    expect(attribution.endQuery()).toEqual([]);
  });

  it('waits behind the pushes queued before it', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    const b = attribution.push('b', ['b']);
    attribution.push('/x', ['x']);
    attribution.push('/y', ['y']);
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['x'] });
    attribution.turnStarted();
    expect(attribution.result('success')).toEqual({ inputs: ['y'] });
  });

  it('is left unanswered when its turn fails', () => {
    const attribution = new TurnAttribution();
    attribution.push('/probe-skill x', ['r0']);
    attribution.turnStarted();
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: [],
    });
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
    expect(attribution.endQuery()).toEqual([
      { inputs: ['r0'], nudge: false, acked: true, failed: true },
    ]);
  });

  it('has the shape the router sends bare', () => {
    // The router sends the newest message as a bare command when it is one
    // (src/router.ts). The runner's copy of the pattern must be the same.
    expect(SLASH_COMMAND.source).toBe(ROUTER_SLASH_COMMAND.source);
    expect(SLASH_COMMAND.flags).toBe(ROUTER_SLASH_COMMAND.flags);
    for (const content of [
      '/probe-skill x',
      '/cost',
      '/Upper_case-1 args\nmore',
      '/home/jw is my home',
      '/ not a command',
      '/1abc',
      'plain text',
      ' /cost',
    ]) {
      const prompt = formatMessages([
        {
          id: '1',
          chat_jid: 'chat',
          sender: 'user',
          sender_name: 'User',
          content,
          timestamp: '2026-01-01T00:00:00.000Z',
        },
      ]);
      const bare = !prompt.startsWith('<messages>');
      expect(isSlashCommand(prompt), content).toBe(bare);
    }
  });
});

describe('a success result with no replay at all', () => {
  it('turns attribution off for the rest of the container, logged once', () => {
    const log = vi.fn();
    const attribution = new TurnAttribution(log);
    attribution.push('a', ['a']);
    attribution.turnStarted();
    expect(attribution.result('success')).toBeUndefined();
    expect(log).toHaveBeenCalledTimes(1);

    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toBeUndefined();
    expect(attribution.endQuery()).toEqual([]);

    // A new query is the same container: still off.
    const c = attribution.push('c', ['c']);
    attribution.turnStarted();
    attribution.ack(c);
    expect(attribution.result('success')).toBeUndefined();
    expect(log).toHaveBeenCalledTimes(1);
  });

  it('is not excused by a slash command still waiting for its turn', () => {
    const attribution = new TurnAttribution();
    attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.push('/probe-skill x', ['c']);
    expect(attribution.result('success')).toBeUndefined();
  });

  it('says so on the output it trips on and on every one after it, and on none before', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    const answered = attribution.result('success');
    expect(
      attribution.stamp({ status: 'success', result: 'x', turn: answered }),
    ).toEqual({ status: 'success', result: 'x', turn: { inputs: ['a'] } });
    expect(attribution.stamp({ status: 'success', result: null })).toEqual({
      status: 'success',
      result: null,
    });

    // A textless result: without the stamp, the host could not tell it from
    // the session marker of a container whose attribution is on.
    attribution.push('b', ['b']);
    attribution.turnStarted();
    expect(attribution.result('success')).toBeUndefined();
    expect(attribution.stamp({ status: 'success', result: null })).toEqual({
      status: 'success',
      result: null,
      attribution: 'off',
    });
    // The session marker, an error output, and the results of a later query.
    expect(attribution.stamp({ status: 'error', result: null })).toEqual({
      status: 'error',
      result: null,
      attribution: 'off',
    });
    attribution.endQuery();
    const c = attribution.push('c', ['c']);
    attribution.turnStarted();
    attribution.ack(c);
    expect(attribution.result('success')).toBeUndefined();
    expect(attribution.stamp({ status: 'success', result: 'y' })).toEqual({
      status: 'success',
      result: 'y',
      attribution: 'off',
    });
  });
});

describe('the end of a query', () => {
  it('reports the inputs no result answered', () => {
    const attribution = new TurnAttribution();
    const answered = attribution.push('answered', ['answered']);
    attribution.turnStarted();
    attribution.ack(answered);
    attribution.result('success');
    attribution.push('never-taken', ['never-taken']);
    attribution.ack(attribution.push('merged', ['taken-without-result']));
    expect(attribution.endQuery()).toEqual([
      { inputs: ['never-taken'], nudge: false, acked: false },
      { inputs: ['taken-without-result'], nudge: false, acked: true },
    ]);
  });

  it('reports the inputs of a failed turn, as its turn left them unanswered', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push('a', ['a']);
    attribution.turnStarted();
    attribution.ack(a);
    attribution.result('error_during_execution');
    expect(attribution.endQuery()).toEqual([
      { inputs: ['a'], nudge: false, acked: true, failed: true },
    ]);
    // The next query is a new CLI, which never saw them.
    const b = attribution.push('b', ['b']);
    attribution.turnStarted();
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('starts the next query clean', () => {
    const attribution = new TurnAttribution();
    attribution.push('/queued', ['queued']);
    attribution.turnStarted();
    attribution.ack(FOREIGN);
    attribution.endQuery();
    const fresh = attribution.push('new', ['new']);
    attribution.turnStarted();
    attribution.ack(fresh);
    expect(attribution.result('success')).toEqual({ inputs: ['new'] });
    expect(attribution.endQuery()).toEqual([]);
  });
});

describe('input ids', () => {
  it('name the stdin prompt by its ref, then the files drained into it', () => {
    expect(startupInputIds({ promptRef: 'r0' }, ['f1', 'f2'])).toEqual([
      'r0',
      'f1',
      'f2',
    ]);
    expect(startupInputIds({}, ['f1'])).toEqual(['stdin', 'f1']);
    expect(startupInputIds({}, [])).toEqual(['stdin']);
    expect(startupInputIds({ promptRef: '' }, [])).toEqual(['stdin']);
  });

  it('name an IPC file by its ref, else by its file name', () => {
    expect(ipcInputId('1-ab.json', { ref: '10|12' })).toBe('10|12');
    expect(ipcInputId('1-ab.json', {})).toBe('1-ab.json');
    expect(ipcInputId('/workspace/ipc/input/1-ab.json', {})).toBe('1-ab.json');
    expect(ipcInputId('1-ab.json', { ref: 7 })).toBe('1-ab.json');
    expect(ipcInputId('1-ab.json', { ref: '' })).toBe('1-ab.json');
  });
});
