import { describe, expect, it } from 'vitest';
import path from 'path';

import {
  LIKELY_ANSWER_CHARS,
  qaFollowupCommand,
  worthFilingCheck,
} from './qa-followup.js';

describe('worthFilingCheck', () => {
  // Only a pre-filter. The Python side makes the real decision, with a higher
  // floor, so this one is deliberately lower: it can waste a background run but
  // must never suppress one that would have saved something.
  it('lets a substantive answer through', () => {
    expect(worthFilingCheck('가'.repeat(LIKELY_ANSWER_CHARS))).toBe(true);
  });

  it('stops a short acknowledgement', () => {
    expect(worthFilingCheck('네, 처리했습니다.')).toBe(false);
  });

  it('stops an empty reply', () => {
    expect(worthFilingCheck('')).toBe(false);
  });

  it('is lower than the floor the backstop itself applies', () => {
    // The backstop only files an answer of 400 characters or more once it has
    // tied the pair to a paper. Anything at or below that would be silently
    // dropped here instead of merely arriving late.
    expect(LIKELY_ANSWER_CHARS).toBeLessThan(400);
  });
});

describe('qaFollowupCommand', () => {
  const opts = {
    projectRoot: '/srv/paperclaw',
    chatJid: '123@s.whatsapp.net',
    lockFile: '/tmp/qa.lock',
  };

  it('runs the backstop over the recent window only', () => {
    const { args } = qaFollowupCommand(opts);
    expect(args).toContain('--hours');
    expect(args).toContain('1');
  });

  it('scans only the chat that was just answered', () => {
    const { args } = qaFollowupCommand(opts);
    expect(args[args.indexOf('--chat') + 1]).toBe('123@s.whatsapp.net');
  });

  it('takes a lock so it cannot pile up behind the timer', () => {
    // The same script runs every five minutes. Two copies at once would file
    // the same answer twice.
    const { cmd, args } = qaFollowupCommand(opts);
    expect(cmd).toBe('flock');
    expect(args.slice(0, 3)).toEqual(['-n', '/tmp/qa.lock', 'python3']);
  });

  it('points at the script inside the project', () => {
    const { args } = qaFollowupCommand(opts);
    expect(args).toContain(
      path.join(
        '/srv/paperclaw',
        'groups',
        'main',
        'research-papers',
        'auto_save_qa.py',
      ),
    );
  });
});
