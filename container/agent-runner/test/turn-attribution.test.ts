/**
 * The runner's record of which inputs each SDK result answered.
 *
 * Kept out of the runner's src/ on purpose: the container entrypoint compiles
 * all of /app/src under `set -e`, so a vitest import there would stop every
 * container before it starts.
 */
import { describe, expect, it, vi } from 'vitest';

import {
  TurnAttribution,
  ipcInputId,
  startupInputIds,
} from '../src/turn-attribution.js';

const UUID_SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe('a push', () => {
  it('goes out under a fresh uuid every time', () => {
    const attribution = new TurnAttribution();
    const first = attribution.push(['a']);
    const second = attribution.push(['a']);
    expect(first).toMatch(UUID_SHAPE);
    expect(second).toMatch(UUID_SHAPE);
    // The CLI drops a uuid it has seen before, unanswered and unacked.
    expect(second).not.toBe(first);
  });
});

describe('a result', () => {
  it('answers the inputs acknowledged since the previous result', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push(['a']);
    const b = attribution.push(['b']);
    attribution.ack(a);
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['a', 'b'] });

    const c = attribution.push(['c']);
    attribution.ack(c);
    expect(attribution.result('success')).toEqual({ inputs: ['c'] });
  });

  it('leaves a push acknowledged after it to the next result', () => {
    // Pushed during a turn's last API call: the CLI runs it as its own turn.
    const attribution = new TurnAttribution();
    const a = attribution.push(['a']);
    attribution.ack(a);
    const b = attribution.push(['b']);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.ack(b);
    expect(attribution.result('success')).toEqual({ inputs: ['b'] });
  });

  it('carries every id of a push that delivered several inputs', () => {
    const attribution = new TurnAttribution();
    attribution.ack(attribution.push(['r0', 'f1', 'f2']));
    expect(attribution.result('success')).toEqual({
      inputs: ['r0', 'f1', 'f2'],
    });
  });

  it('answers no input for a turn only the nudge started', () => {
    const attribution = new TurnAttribution();
    attribution.ack(attribution.push([], true));
    expect(attribution.result('success')).toEqual({ inputs: [], nudge: true });
  });

  it('answers the file a nudge was merged with', () => {
    const attribution = new TurnAttribution();
    const nudge = attribution.push([], true);
    const file = attribution.push(['f']);
    attribution.ack(nudge);
    attribution.ack(file);
    expect(attribution.result('success')).toEqual({
      inputs: ['f'],
      nudge: true,
    });
  });

  it('ignores an acknowledgement of a uuid it never pushed', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push(['a']);
    attribution.ack('00000000-0000-4000-8000-000000000000');
    attribution.ack(undefined);
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
  });

  it('counts an input once however often it is acknowledged', () => {
    const attribution = new TurnAttribution();
    const a = attribution.push(['a']);
    attribution.ack(a);
    attribution.ack(a);
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
    attribution.ack(a);
    expect(attribution.result('error_max_turns')).toEqual({ inputs: [] });
  });

  it('answers nothing when an error result follows no acknowledgement', () => {
    const attribution = new TurnAttribution();
    expect(attribution.result('error_during_execution')).toEqual({
      inputs: [],
    });
    attribution.ack(attribution.push(['a']));
    expect(attribution.result('success')).toEqual({ inputs: ['a'] });
  });
});

describe('a success result with nothing acknowledged', () => {
  it('turns attribution off for the rest of the container, logged once', () => {
    const log = vi.fn();
    const attribution = new TurnAttribution(log);
    attribution.push(['a']);
    expect(attribution.result('success')).toBeUndefined();
    expect(log).toHaveBeenCalledTimes(1);

    attribution.ack(attribution.push(['b']));
    expect(attribution.result('success')).toBeUndefined();
    expect(attribution.endQuery()).toEqual([]);

    // A new query is the same container: still off.
    attribution.ack(attribution.push(['c']));
    expect(attribution.result('success')).toBeUndefined();
    expect(log).toHaveBeenCalledTimes(1);
  });

  it('counts only acknowledgements of its own pushes', () => {
    const attribution = new TurnAttribution();
    attribution.push(['a']);
    attribution.ack('00000000-0000-4000-8000-000000000000');
    expect(attribution.result('success')).toBeUndefined();
  });
});

describe('the end of a query', () => {
  it('reports the inputs no result answered', () => {
    const attribution = new TurnAttribution();
    attribution.ack(attribution.push(['answered']));
    attribution.result('success');
    attribution.push(['never-taken']);
    attribution.ack(attribution.push(['taken-without-result']));
    expect(attribution.endQuery()).toEqual([
      { inputs: ['never-taken'], nudge: false, acked: false },
      { inputs: ['taken-without-result'], nudge: false, acked: true },
    ]);
  });

  it('starts the next query clean', () => {
    const attribution = new TurnAttribution();
    attribution.ack(attribution.push(['old']));
    attribution.endQuery();
    attribution.ack(attribution.push(['new']));
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
