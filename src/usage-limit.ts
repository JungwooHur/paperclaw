/**
 * Claude usage limits, as the agent's bundled CLI reports them.
 *
 * When the account behind the OAuth token runs out of quota, the CLI answers
 * every request with one sentence ("You've hit your limit · resets Oct 14, 10am
 * (Asia/Seoul)") and the in-container agent runner passes it on as a successful
 * result. Taken at face value, that sentence goes out as the reply and the work
 * counts as done. This module recognises it and keeps the window it opens in
 * router_state, so the message path and the scheduler can both hold their work
 * until the reset, across a restart, and the user is told once per window.
 */
import { OUTPUT_LANGUAGE, TIMEZONE } from './config.js';
import { getRouterState, setRouterState } from './db.js';

export interface UsageLimit {
  resetsAt: Date;
}

export interface UsageLimitHit extends UsageLimit {
  /** The notice to send, or null when this window was already announced. */
  notice: string | null;
}

// Anchored: the CLI's result is the sentence itself, while an answer that
// merely mentions it must not be swallowed. These are the CLI's sentences for
// a refused request, one per limit (plain when extra usage is also refused);
// its warnings ("You've used 90% of…") leave the request running and are not.
const LIMIT_PATTERN =
  /^\s*(?:you['’]ve hit your (?:(?:session|weekly|opus|sonnet|usage)\s+)?limit|you['’]re out of extra usage)\b/i;
// "resets Oct 14, 10am (Asia/Seoul)", "resets Jan 2, 2027, 1am (Asia/Seoul)",
// "resets 10am (Asia/Seoul)", "resets 10:30pm". The CLI adds the date only
// when the reset is more than a day ahead, and the year only when it differs.
const RESET_PATTERN =
  /\bresets\s+(?:([a-z]{3,9})\.?\s+(\d{1,2})(?:,\s*(\d{4}))?,?\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b(?:\s*\(([^)]+)\))?/i;
const MONTHS = [
  'jan',
  'feb',
  'mar',
  'apr',
  'may',
  'jun',
  'jul',
  'aug',
  'sep',
  'oct',
  'nov',
  'dec',
];

const MINUTE_MS = 60 * 1000;
/** How long to hold when the text gives no reset that can be trusted. */
const UNKNOWN_RESET_HOLD_MS = 60 * MINUTE_MS;
/**
 * A bare reset time this little in the past is the reset happening now: the
 * CLI drops the seconds, so a refusal just before a 10:00:45 reset reads
 * "10am", and the result takes a moment to arrive. Anything further past is
 * tomorrow's, because the CLI only prints a reset that is still ahead. A reset
 * just under a day ahead reads the same as one happening now; taking it as now
 * costs an hour's hold, where the other reading would cost a day.
 */
const JUST_PASSED_MS = 2 * MINUTE_MS;
/** Held messages are picked up again this long after the reset... */
const MESSAGE_RESUME_MARGIN_MS = 2 * MINUTE_MS;
/** ...and a deferred scheduled task runs a little after them. */
const TASK_RESUME_MARGIN_MS = 5 * MINUTE_MS;

// router_state keys: the open window's reset, and the reset of the window the
// user has already been told about.
const LIMITED_UNTIL_KEY = 'usage_limited_until';
const ANNOUNCED_KEY = 'usage_limit_announced';

export function isUsageLimit(text: string): boolean {
  return LIMIT_PATTERN.test(text);
}

/**
 * Recognises a usage-limit result and reads its reset time, in the zone the
 * text names (`tz` when it names none). A bare time is its next occurrence; a
 * reset that cannot be read is taken as an hour from `now`.
 */
export function classifyUsageLimit(
  text: string,
  now: Date,
  tz: string,
): UsageLimit | null {
  if (!isUsageLimit(text)) return null;
  const match = RESET_PATTERN.exec(text);
  const resetsAt = match ? parseReset(match, now, tz) : null;
  return {
    resetsAt: resetsAt ?? new Date(now.getTime() + UNKNOWN_RESET_HOLD_MS),
  };
}

/**
 * Records a usage-limit result, if `text` is one, and says whether to tell the
 * user. The window is persisted, so it holds work across a restart.
 */
export function recordUsageLimit(
  text: string,
  now: Date = new Date(),
  tz: string = TIMEZONE,
  lang: string = OUTPUT_LANGUAGE,
): UsageLimitHit | null {
  const limit = classifyUsageLimit(text, now, tz);
  if (!limit) return null;

  // A reset the CLI places in the past cannot be right — the request was just
  // refused — and trusting it would send the same work straight back into the
  // limit every couple of minutes.
  const resetsAt =
    limit.resetsAt.getTime() > now.getTime()
      ? limit.resetsAt
      : new Date(now.getTime() + UNKNOWN_RESET_HOLD_MS);
  setRouterState(LIMITED_UNTIL_KEY, resetsAt.toISOString());

  // One notice per window. A refusal that moves the reset more than an hour
  // past the announced one opens a new window, as far as the user can tell:
  // without a word they wait on a time that has passed. A smaller move is the
  // guessed hold starting again from a later refusal.
  const announced = readTime(ANNOUNCED_KEY);
  if (
    announced !== null &&
    now.getTime() < announced + MESSAGE_RESUME_MARGIN_MS &&
    resetsAt.getTime() <= announced + UNKNOWN_RESET_HOLD_MS
  ) {
    return { resetsAt, notice: null };
  }
  setRouterState(ANNOUNCED_KEY, resetsAt.toISOString());
  return { resetsAt, notice: usageLimitNotice(resetsAt, tz, lang) };
}

/**
 * When held messages may run again, or null when no window is open. The window
 * closes a little after the reset.
 */
export function usageLimitHeldUntil(now: Date = new Date()): Date | null {
  const reset = readTime(LIMITED_UNTIL_KEY);
  if (reset === null) return null;
  const until = reset + MESSAGE_RESUME_MARGIN_MS;
  return now.getTime() < until ? new Date(until) : null;
}

/** When a scheduled run refused by the limit should run instead. */
export function deferredTaskRun(resetsAt: Date): Date {
  return new Date(resetsAt.getTime() + TASK_RESUME_MARGIN_MS);
}

function usageLimitNotice(resetsAt: Date, tz: string, lang: string): string {
  const korean = lang === 'ko';
  const when = new Intl.DateTimeFormat(korean ? 'ko-KR' : 'en-US', {
    timeZone: tz,
    month: korean ? 'long' : 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(resetsAt);
  return korean
    ? `Claude 사용 한도에 도달했습니다. 대기 중인 작업은 한도가 초기화되는 ${when} (${tz}) 이후에 자동으로 처리됩니다.`
    : `Claude usage limit reached. Pending work will run automatically after the limit resets at ${when} (${tz}).`;
}

function readTime(key: string): number | null {
  const value = getRouterState(key);
  const time = value ? Date.parse(value) : NaN;
  return Number.isNaN(time) ? null : time;
}

function parseReset(
  match: RegExpExecArray,
  now: Date,
  fallbackTz: string,
): Date | null {
  const [
    ,
    monthName,
    dayText,
    yearText,
    hourText,
    minuteText,
    meridiem,
    zoneText,
  ] = match;
  const hour12 = Number(hourText);
  const minute = minuteText ? Number(minuteText) : 0;
  if (hour12 < 1 || hour12 > 12 || minute > 59) return null;
  const hour = (hour12 % 12) + (meridiem.toLowerCase() === 'pm' ? 12 : 0);
  const zone =
    zoneText && isTimeZone(zoneText.trim()) ? zoneText.trim() : fallbackTz;
  const today = wallClock(now, zone);

  if (monthName) {
    const month = MONTHS.indexOf(monthName.slice(0, 3).toLowerCase());
    const day = Number(dayText);
    if (month < 0 || day < 1 || day > 31) return null;
    if (yearText) {
      return zonedTime(Number(yearText), month, day, hour, minute, zone);
    }
    // No year in the text: take the one that puts the reset nearest to now,
    // so "Jan 1" on New Year's Eve is tomorrow, not eleven months ago.
    return [today.year - 1, today.year, today.year + 1]
      .map((year) => zonedTime(year, month, day, hour, minute, zone))
      .reduce((best, candidate) =>
        Math.abs(candidate.getTime() - now.getTime()) <
        Math.abs(best.getTime() - now.getTime())
          ? candidate
          : best,
      );
  }

  const reset = zonedTime(
    today.year,
    today.month,
    today.day,
    hour,
    minute,
    zone,
  );
  if (reset.getTime() >= now.getTime() - JUST_PASSED_MS) return reset;
  return zonedTime(today.year, today.month, today.day + 1, hour, minute, zone);
}

function isTimeZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

interface WallClock {
  year: number;
  month: number; // 0-based, like Date
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function wallClock(date: Date, zone: string): WallClock {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((p) => p.type === type)?.value);
  return {
    year: part('year'),
    month: part('month') - 1,
    day: part('day'),
    hour: part('hour'),
    minute: part('minute'),
    second: part('second'),
  };
}

/** The instant at which the wall clock in `zone` reads the given time. */
function zonedTime(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  zone: string,
): Date {
  // Date.UTC also normalises an overflowing day (the 32nd is the 1st of next month).
  const asUtc = Date.UTC(year, month, day, hour, minute);
  // The zone's offset is read twice so a time across a DST change settles.
  const first = asUtc - offsetMs(asUtc, zone);
  return new Date(asUtc - offsetMs(first, zone));
}

function offsetMs(instant: number, zone: string): number {
  const c = wallClock(new Date(instant), zone);
  const wallAsUtc = Date.UTC(
    c.year,
    c.month,
    c.day,
    c.hour,
    c.minute,
    c.second,
  );
  return wallAsUtc - Math.floor(instant / 1000) * 1000;
}
