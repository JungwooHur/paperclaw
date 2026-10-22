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
  startupInputIds,
} from '../src/turn-attribution.js';

const UUID_SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** A replay under a uuid the runner never pushed: the CLI made the message. */
const FOREIGN = '00000000-0000-4000-8000-000000000000';

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

describe('a slash command', () => {
  it('is answered by the turn it started, under no acknowledgement of its own', () => {
    // A skill: the CLI replays the expanded skill under its own uuid.
    const skill = new TurnAttribution();
    skill.push('/probe-skill x', ['r0']);
    skill.turnStarted();
    skill.ack(FOREIGN);
    expect(skill.result('success')).toEqual({ inputs: ['r0'] });

    // A local command: its output is replayed under the CLI's uuid, and the
    // result has no text.
    const local = new TurnAttribution();
    local.push('/cost', ['r0']);
    local.turnStarted();
    local.ack(FOREIGN);
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

  it('is answered even when its turn fails', () => {
    const attribution = new TurnAttribution();
    attribution.push('/probe-skill x', ['r0']);
    attribution.turnStarted();
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: ['r0'],
    });
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
    expect(startupInputIds('r0', ['f1', 'f2'])).toEqual(['r0', 'f1', 'f2']);
    expect(startupInputIds(undefined, ['f1'])).toEqual(['stdin', 'f1']);
    expect(startupInputIds(undefined, [])).toEqual(['stdin']);
  });

  it('name an IPC file by its ref, else by its file name', () => {
    expect(ipcInputId('1-ab.json', { ref: '10|12' })).toBe('10|12');
    expect(ipcInputId('1-ab.json', {})).toBe('1-ab.json');
    expect(ipcInputId('/workspace/ipc/input/1-ab.json', {})).toBe('1-ab.json');
    expect(ipcInputId('1-ab.json', { ref: 7 })).toBe('1-ab.json');
    expect(ipcInputId('1-ab.json', { ref: '' })).toBe('1-ab.json');
  });
});
