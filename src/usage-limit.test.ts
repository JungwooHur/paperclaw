import { beforeEach, describe, expect, it } from 'vitest';

import { _initTestDatabase } from './db.js';
import {
  classifyUsageLimit,
  closeUsageLimitOnAnswer,
  isAgentAnswer,
  recordUsageLimit,
  usageLimitHeldUntil,
  usageLimitResetsAt,
} from './usage-limit.js';

// What the bundled CLI actually sends back when the account is out of quota.
const WITH_DATE = "You've hit your limit · resets Oct 14, 10am (Asia/Seoul)";
const WITHOUT_DATE = "You've hit your limit · resets 10am (Asia/Seoul)";

const SEOUL = 'Asia/Seoul';
const HOUR = 60 * 60 * 1000;

function resetOf(text: string, nowIso: string, tz = SEOUL): string | null {
  const limit = classifyUsageLimit(text, new Date(nowIso), tz);
  return limit ? limit.resetsAt.toISOString() : null;
}

describe('classifyUsageLimit', () => {
  it('reads a dated reset in the zone the text names', () => {
    // 21:00 on Oct 13 in Seoul.
    expect(resetOf(WITH_DATE, '2026-10-13T12:00:00.000Z')).toBe(
      '2026-10-14T01:00:00.000Z',
    );
  });

  it('reads a bare time as its next occurrence today', () => {
    // 07:00 on Oct 14 in Seoul: 10am is still ahead.
    expect(resetOf(WITHOUT_DATE, '2026-10-13T22:00:00.000Z')).toBe(
      '2026-10-14T01:00:00.000Z',
    );
  });

  it('rolls a bare time that has passed over to the next day', () => {
    // Noon on Oct 14 in Seoul: the next 10am is tomorrow's.
    expect(resetOf(WITHOUT_DATE, '2026-10-14T03:00:00.000Z')).toBe(
      '2026-10-15T01:00:00.000Z',
    );
  });

  it('does not push a reset that has only just passed out by a day', () => {
    // Twenty seconds after 10am: the reset is now, not tomorrow.
    expect(resetOf(WITHOUT_DATE, '2026-10-14T01:00:20.000Z')).toBe(
      '2026-10-14T01:00:00.000Z',
    );
  });

  it('reads a bare time minutes past as tomorrow, since the CLI only prints a future reset', () => {
    // 10:05 in Seoul. The CLI drops the date only when the reset is at most a
    // day ahead, so "10am" here is tomorrow's 10am, not five minutes ago.
    expect(resetOf(WITHOUT_DATE, '2026-10-14T01:05:00.000Z')).toBe(
      '2026-10-15T01:00:00.000Z',
    );
  });

  it('reads minutes and the 12 o’clock hours', () => {
    const now = '2026-10-13T10:00:00.000Z'; // 19:00 in Seoul
    expect(
      resetOf("You've hit your limit · resets 10:30pm (Asia/Seoul)", now),
    ).toBe('2026-10-13T13:30:00.000Z');
    expect(
      resetOf("You've hit your limit · resets 12am (Asia/Seoul)", now),
    ).toBe('2026-10-13T15:00:00.000Z');
    expect(
      resetOf("You've hit your limit · resets 12pm (Asia/Seoul)", now),
    ).toBe('2026-10-14T03:00:00.000Z');
  });

  it('reads the year the CLI adds to a reset in the next year', () => {
    // 12:00 on Dec 28 in Seoul, a weekly reset five days on.
    const now = '2026-12-28T03:00:00.000Z';
    expect(
      resetOf(
        "You've hit your limit · resets Jan 2, 2027, 1am (Asia/Seoul)",
        now,
      ),
    ).toBe('2027-01-01T16:00:00.000Z');
    expect(
      resetOf(
        "You've hit your weekly limit · resets Jan 2, 2027, 1:30am (Asia/Seoul)",
        now,
      ),
    ).toBe('2027-01-01T16:30:00.000Z');
  });

  it('prefers the zone in the text over the fallback', () => {
    // 08:00 in New York (EDT, UTC-4).
    expect(
      resetOf(
        "You've hit your limit · resets 10am (America/New_York)",
        '2026-10-13T12:00:00.000Z',
      ),
    ).toBe('2026-10-13T14:00:00.000Z');
  });

  it('falls back to the given zone when the text names none or a bad one', () => {
    const now = '2026-10-13T22:00:00.000Z';
    expect(resetOf("You've hit your limit · resets 10am", now)).toBe(
      '2026-10-14T01:00:00.000Z',
    );
    expect(
      resetOf("You've hit your limit · resets 10am (Nowhere/Atlantis)", now),
    ).toBe('2026-10-14T01:00:00.000Z');
  });

  it('holds for an hour when the reset time cannot be read', () => {
    const now = '2026-10-13T12:00:00.000Z';
    const anHourLater = new Date(Date.parse(now) + HOUR).toISOString();
    expect(resetOf("You've hit your limit", now)).toBe(anHourLater);
    expect(resetOf("You've hit your limit · resets soon", now)).toBe(
      anHourLater,
    );
  });

  it('recognises every refusal the CLI words, not only the plain one', () => {
    // 21:00 on Oct 13 in Seoul. These are the CLI's rejected-request sentences
    // for the session, weekly and per-model limits and for spent extra usage.
    const now = '2026-10-13T12:00:00.000Z';
    const tonight = '2026-10-13T14:00:00.000Z';
    const inThreeDays = '2026-10-16T14:00:00.000Z';
    for (const [text, reset] of [
      ["You've hit your session limit · resets 11pm (Asia/Seoul)", tonight],
      [
        "You've hit your weekly limit · resets Oct 16, 11pm (Asia/Seoul)",
        inThreeDays,
      ],
      [
        "You've hit your Opus limit · resets Oct 16, 11pm (Asia/Seoul)",
        inThreeDays,
      ],
      [
        "You've hit your Sonnet limit · resets Oct 16, 11pm (Asia/Seoul)",
        inThreeDays,
      ],
      ["You've hit your usage limit · resets 11pm (Asia/Seoul)", tonight],
      ["You're out of extra usage · resets 11pm (Asia/Seoul)", tonight],
    ]) {
      expect(resetOf(text, now), text).toBe(reset);
    }
  });

  it('leaves the CLI warnings alone: the request behind them still runs', () => {
    const now = '2026-10-13T12:00:00.000Z';
    for (const text of [
      "You've used 90% of your session limit · resets 11pm (Asia/Seoul)",
      "You're close to your extra usage spending limit",
      "You're now using extra usage · Your session limit resets 11pm (Asia/Seoul)",
    ]) {
      expect(resetOf(text, now), text).toBeNull();
    }
  });

  it('leaves ordinary answers alone, including ones that quote the phrase', () => {
    const now = '2026-10-13T12:00:00.000Z';
    expect(resetOf('Section 3 is translated and uploaded.', now)).toBeNull();
    expect(
      resetOf(
        'The CLI prints "You\'ve hit your limit" when the quota runs out.',
        now,
      ),
    ).toBeNull();
    expect(resetOf('', now)).toBeNull();
  });
});

describe('isAgentAnswer', () => {
  it('takes text the agent wrote, internal or not, as an answer', () => {
    expect(isAgentAnswer('Section 3 is translated and uploaded.')).toBe(true);
    expect(isAgentAnswer('<internal>acked, dispatched</internal>')).toBe(true);
    // Quoting an error is not being one.
    expect(isAgentAnswer('The run failed with "API Error: 500".')).toBe(true);
  });

  it('does not take the refusal, an empty result or an API error as one', () => {
    // The runner forwards is_error results as successes, so these arrive as
    // result text. None of them shows the limit let a request through. Every
    // other error the CLI can write is in usage-limit-cli.test.ts.
    for (const text of [
      WITH_DATE,
      "You're out of extra usage · resets 11pm (Asia/Seoul)",
      '',
      '   ',
      'API Error: 500 {"type":"error","error":{"type":"api_error"}}',
      'API Error: Rate limit reached',
      '  Request timed out\n',
    ]) {
      expect(isAgentAnswer(text), text).toBe(false);
    }
    expect(isAgentAnswer(null)).toBe(false);
  });
});

describe('recordUsageLimit', () => {
  beforeEach(() => {
    _initTestDatabase();
  });

  const now = new Date('2026-10-13T12:00:00.000Z'); // 21:00 in Seoul
  const reset = Date.parse('2026-10-14T01:00:00.000Z');

  it('ignores a result that is not a usage limit', () => {
    expect(recordUsageLimit('Done.', now, SEOUL, 'ko')).toBeNull();
    expect(usageLimitHeldUntil(now)).toBeNull();
  });

  it('holds messages until shortly after the reset', () => {
    const hit = recordUsageLimit(WITH_DATE, now, SEOUL, 'ko');
    expect(hit?.resetsAt.getTime()).toBe(reset);

    const heldUntil = usageLimitHeldUntil(now);
    expect(heldUntil).not.toBeNull();
    expect(heldUntil!.getTime()).toBeGreaterThan(reset);
    expect(heldUntil!.getTime()).toBeLessThanOrEqual(reset + 5 * 60_000);
    expect(usageLimitHeldUntil(new Date(reset - 1))).not.toBeNull();
    expect(usageLimitHeldUntil(heldUntil!)).toBeNull();
  });

  it('announces a window once, and the next window again', () => {
    const first = recordUsageLimit(WITH_DATE, now, SEOUL, 'ko');
    const second = recordUsageLimit(
      WITH_DATE,
      new Date(now.getTime() + 60_000),
      SEOUL,
      'ko',
    );
    expect(first?.notice).toBeTruthy();
    expect(second).not.toBeNull();
    expect(second?.notice).toBeNull();

    // The next day, after that window has closed.
    const nextDay = new Date('2026-10-14T12:00:00.000Z');
    expect(usageLimitHeldUntil(nextDay)).toBeNull();
    const third = recordUsageLimit(
      WITH_DATE.replace('14', '15'),
      nextDay,
      SEOUL,
      'ko',
    );
    expect(third?.notice).toBeTruthy();
  });

  it('announces the right reset once when refused minutes after a bare time', () => {
    // 10:05 in Seoul, "resets 10am": the window ends tomorrow at 10am, and a
    // refusal an hour later is the same window, so it says nothing new.
    const early = new Date('2026-10-14T01:05:00.000Z');
    const first = recordUsageLimit(WITHOUT_DATE, early, SEOUL, 'en');
    expect(first?.resetsAt.toISOString()).toBe('2026-10-15T01:00:00.000Z');
    expect(first?.notice).toContain('Oct 15');

    const later = new Date(early.getTime() + HOUR + 2 * 60_000);
    expect(
      recordUsageLimit(WITHOUT_DATE, later, SEOUL, 'en')?.notice,
    ).toBeNull();
  });

  it('announces again when a later refusal moves the reset past the one announced', () => {
    // 10:01 in Seoul, "resets 10am": read as the reset happening now, which
    // the refusal itself disproves, so the hold is the guessed hour.
    const early = new Date('2026-10-14T01:01:00.000Z');
    const first = recordUsageLimit(WITHOUT_DATE, early, SEOUL, 'en');
    expect(first?.notice).toContain('11:01');

    // At 10:30 the same text can only be tomorrow's 10am. Without a second
    // notice the user waits on 11:01 and hears nothing until tomorrow.
    const later = new Date('2026-10-14T01:30:00.000Z');
    const second = recordUsageLimit(WITHOUT_DATE, later, SEOUL, 'en');
    expect(second?.resetsAt.toISOString()).toBe('2026-10-15T01:00:00.000Z');
    expect(second?.notice).toContain('Oct 15');

    // That corrected window is announced once, like any other.
    const retry = new Date(later.getTime() + HOUR);
    expect(
      recordUsageLimit(WITHOUT_DATE, retry, SEOUL, 'en')?.notice,
    ).toBeNull();
  });

  it('does not announce a guessed hold again because a later refusal restarted it', () => {
    const text = "You've hit your limit"; // no reset to read: an hour's hold
    expect(recordUsageLimit(text, now, SEOUL, 'en')?.notice).toBeTruthy();
    const retry = new Date(now.getTime() + 40 * 60_000);
    expect(recordUsageLimit(text, retry, SEOUL, 'en')?.notice).toBeNull();
  });

  it('announces a next-year reset once, not every hour', () => {
    const now = new Date('2026-12-28T03:00:00.000Z');
    const text = "You've hit your limit · resets Jan 2, 2027, 1am (Asia/Seoul)";
    expect(recordUsageLimit(text, now, SEOUL, 'en')?.notice).toContain('Jan 2');
    for (let hour = 1; hour <= 3; hour++) {
      const retry = new Date(now.getTime() + hour * HOUR + 2 * 60_000);
      expect(recordUsageLimit(text, retry, SEOUL, 'en')?.notice).toBeNull();
    }
  });

  it('holds work even when the stated reset has already passed', () => {
    // The CLI says 10am, it is 10:20, and the request was still refused.
    const late = new Date('2026-10-14T01:20:00.000Z');
    recordUsageLimit(WITH_DATE, late, SEOUL, 'ko');
    expect(usageLimitHeldUntil(late)).not.toBeNull();
    // ...and not for a moment only, or held work would be sent straight back
    // into the limit every couple of minutes.
    expect(
      usageLimitHeldUntil(new Date(late.getTime() + 30 * 60_000)),
    ).not.toBeNull();
  });

  it('does not let a guessed reset shorten an open window', () => {
    // The window runs to 10am tomorrow. A later refusal that states no reset
    // knows less than that, and an hour's guess would resume work hours early.
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    const later = new Date(now.getTime() + 10 * 60_000);
    const guessed = recordUsageLimit(
      "You've hit your limit",
      later,
      SEOUL,
      'en',
    );
    expect(guessed?.resetsAt.getTime()).toBe(reset);
    expect(guessed?.notice).toBeNull();
    expect(usageLimitResetsAt(later)?.getTime()).toBe(reset);

    // Nor does a stated reset already in the past, which is a guess as well.
    const past = "You've hit your limit · resets Oct 13, 8pm (Asia/Seoul)";
    expect(recordUsageLimit(past, later, SEOUL, 'en')?.resetsAt.getTime()).toBe(
      reset,
    );
    expect(usageLimitResetsAt(later)?.getTime()).toBe(reset);
  });

  it('lets a guessed reset extend an open window that ends sooner', () => {
    const text = "You've hit your limit";
    recordUsageLimit(text, now, SEOUL, 'en');
    const later = new Date(now.getTime() + 40 * 60_000);
    recordUsageLimit(text, later, SEOUL, 'en');
    expect(usageLimitResetsAt(later)?.getTime()).toBe(later.getTime() + HOUR);
  });

  it('takes a stated reset as the window, earlier or later, and says nothing on an earlier one', () => {
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en'); // 10am tomorrow
    const later = new Date(now.getTime() + 60_000);
    const earlier = recordUsageLimit(
      "You've hit your session limit · resets 11pm (Asia/Seoul)",
      later,
      SEOUL,
      'en',
    );
    const elevenPm = Date.parse('2026-10-13T14:00:00.000Z');
    expect(earlier?.resetsAt.getTime()).toBe(elevenPm);
    expect(earlier?.notice).toBeNull();
    expect(usageLimitResetsAt(later)?.getTime()).toBe(elevenPm);

    const weekly = recordUsageLimit(
      "You've hit your weekly limit · resets Oct 16, 11pm (Asia/Seoul)",
      later,
      SEOUL,
      'en',
    );
    expect(usageLimitResetsAt(later)?.toISOString()).toBe(
      '2026-10-16T14:00:00.000Z',
    );
    expect(weekly?.notice).toContain('Oct 16');
  });

  it('writes the notice in Korean for ko, with the reset in the configured zone', () => {
    // 10am in New York is 11pm in Seoul.
    const hit = recordUsageLimit(
      "You've hit your limit · resets 10am (America/New_York)",
      now,
      SEOUL,
      'ko',
    );
    expect(hit?.notice).toMatch(/[가-힣]/);
    expect(hit?.notice).toContain('11:00');
    expect(hit?.notice).not.toContain("You've hit your limit");
  });

  it('writes the notice in English for any other language', () => {
    const hit = recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    expect(hit?.notice).not.toMatch(/[가-힣]/);
    expect(hit?.notice).toMatch(/10:00\sAM/);
    expect(hit?.notice).not.toContain("You've hit your limit");
  });
});

describe('closeUsageLimitOnAnswer', () => {
  beforeEach(() => {
    _initTestDatabase();
  });

  const now = new Date('2026-10-13T12:00:00.000Z'); // 21:00 in Seoul
  const later = new Date(now.getTime() + 10 * 60_000);

  it('closes an open window when a request gets through', () => {
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    expect(closeUsageLimitOnAnswer('Section 3 is uploaded.', later)).toBe(true);
    expect(usageLimitHeldUntil(later)).toBeNull();
    expect(usageLimitResetsAt(later)).toBeNull();
  });

  it('leaves the window open on a refusal or an API error', () => {
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    expect(closeUsageLimitOnAnswer(WITH_DATE, later)).toBe(false);
    expect(
      closeUsageLimitOnAnswer('API Error: Rate limit reached', later),
    ).toBe(false);
    expect(closeUsageLimitOnAnswer(null, later)).toBe(false);
    expect(usageLimitHeldUntil(later)).not.toBeNull();
  });

  it('reports nothing closed when no window is open', () => {
    expect(closeUsageLimitOnAnswer('Section 3 is uploaded.', now)).toBe(false);
  });

  it('does not announce the window again when the next request is refused', () => {
    // A close on a stray answer costs one refused retry, and only that.
    expect(recordUsageLimit(WITH_DATE, now, SEOUL, 'en')?.notice).toBeTruthy();
    closeUsageLimitOnAnswer('Section 3 is uploaded.', later);
    const again = recordUsageLimit(WITH_DATE, later, SEOUL, 'en');
    expect(again?.notice).toBeNull();
    expect(usageLimitHeldUntil(later)).not.toBeNull();
  });

  it('stays silent within ten minutes of the close, and for the rest of that window', () => {
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    closeUsageLimitOnAnswer('Section 3 is uploaded.', later);
    const retry = new Date(later.getTime() + 9 * 60_000);
    expect(recordUsageLimit(WITH_DATE, retry, SEOUL, 'en')?.notice).toBeNull();
    // The window is open again, so a later refusal is still this window's.
    const after = new Date(retry.getTime() + HOUR);
    expect(recordUsageLimit(WITH_DATE, after, SEOUL, 'en')?.notice).toBeNull();
  });

  it('reopens the closed window, not a fresh hour, on a retry that states no reset', () => {
    // Otherwise the held work would be retried, and silently refused, every
    // hour until the reset the user was told.
    const reset = Date.parse('2026-10-14T01:00:00.000Z');
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en'); // 10am tomorrow
    closeUsageLimitOnAnswer('Section 3 is uploaded.', later);
    const retry = new Date(later.getTime() + 5 * 60_000);
    const hit = recordUsageLimit("You've hit your limit", retry, SEOUL, 'en');
    expect(hit?.notice).toBeNull();
    expect(hit?.resetsAt.getTime()).toBe(reset);
    expect(usageLimitResetsAt(retry)?.getTime()).toBe(reset);
  });

  it('guesses an hour for a window that opens well after the close', () => {
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en');
    closeUsageLimitOnAnswer('Section 3 is uploaded.', later);
    const reopened = new Date(later.getTime() + 11 * 60_000);
    const hit = recordUsageLimit(
      "You've hit your limit",
      reopened,
      SEOUL,
      'en',
    );
    expect(hit?.resetsAt.getTime()).toBe(reopened.getTime() + HOUR);
    expect(hit?.notice).toBeTruthy();
  });

  it('announces a window that opens well after an answer closed the last one', () => {
    // The user saw the bot answer again; without a word, a new window would
    // look like it had stopped. Its reset is still before the announced one.
    recordUsageLimit(WITH_DATE, now, SEOUL, 'en'); // 10am tomorrow
    closeUsageLimitOnAnswer('Section 3 is uploaded.', later);
    const reopened = new Date(later.getTime() + 11 * 60_000);
    const hit = recordUsageLimit(
      "You're out of extra usage · resets 1am (Asia/Seoul)",
      reopened,
      SEOUL,
      'en',
    );
    expect(hit?.resetsAt.toISOString()).toBe('2026-10-13T16:00:00.000Z');
    expect(hit?.notice).toContain('1:00');
  });
});
