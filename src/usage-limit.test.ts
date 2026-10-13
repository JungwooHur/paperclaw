import { beforeEach, describe, expect, it } from 'vitest';

import { _initTestDatabase } from './db.js';
import {
  classifyUsageLimit,
  recordUsageLimit,
  usageLimitHeldUntil,
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
