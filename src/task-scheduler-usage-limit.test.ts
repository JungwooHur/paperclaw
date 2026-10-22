import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./container-runner.js', () => ({
  runContainerAgent: vi.fn(),
  writeTasksSnapshot: vi.fn(),
}));

import { ContainerOutput, runContainerAgent } from './container-runner.js';
import { _initTestDatabase, createTask, getTaskById } from './db.js';
import {
  _resetSchedulerLoopForTests,
  startSchedulerLoop,
} from './task-scheduler.js';
import { ScheduledTask } from './types.js';
import { recordUsageLimit, usageLimitHeldUntil } from './usage-limit.js';

const LIMIT = "You've hit your limit · resets Oct 14, 10am (Asia/Seoul)";
const NOW = Date.parse('2026-10-13T12:00:00.000Z'); // 21:00 in Seoul
const RESET = Date.parse('2026-10-14T01:00:00.000Z');
const CHAT = 'main@g.us';

/** The run's turns end with these results, in order. */
function agentAnswers(...texts: string[]): void {
  vi.mocked(runContainerAgent).mockImplementation(
    async (_group, _input, _onProcess, onOutput) => {
      for (const text of texts) {
        const output: ContainerOutput = {
          status: 'success',
          result: text,
          newSessionId: 'session-1',
        };
        await onOutput?.(output);
      }
      return { status: 'success', result: null, newSessionId: 'session-1' };
    },
  );
}

function addDueTask(
  id: string,
  scheduleType: ScheduledTask['schedule_type'],
  scheduleValue: string,
): void {
  createTask({
    id,
    group_folder: 'main',
    chat_jid: CHAT,
    prompt: 'nightly batch',
    schedule_type: scheduleType,
    schedule_value: scheduleValue,
    context_mode: 'isolated',
    next_run: new Date(Date.now() - 60_000).toISOString(),
    status: 'active',
    created_at: '2026-10-01T00:00:00.000Z',
  });
}

async function runDueTasks(
  sendMessage: (jid: string, text: string) => Promise<void>,
  onUsageLimitLifted: () => void = () => {},
) {
  const queue = {
    enqueueTask: (_jid: string, _id: string, fn: () => Promise<void>) => {
      void fn();
    },
    closeStdin: vi.fn(),
    notifyIdle: vi.fn(),
  };
  _resetSchedulerLoopForTests();
  startSchedulerLoop({
    registeredGroups: () => ({
      [CHAT]: {
        name: 'Main',
        folder: 'main',
        trigger: '@Andy',
        added_at: '2026-10-01T00:00:00.000Z',
      },
    }),
    getSessions: () => ({}),
    queue: queue as any,
    onProcess: () => {},
    sendMessage,
    onUsageLimitLifted,
  });
  await vi.advanceTimersByTimeAsync(10);
  return queue;
}

function deferredPastReset(task: ScheduledTask | undefined): boolean {
  const nextRun = Date.parse(task?.next_run ?? '');
  return nextRun > RESET && nextRun <= RESET + 10 * 60_000;
}

describe('a scheduled run that hits the usage limit', () => {
  beforeEach(() => {
    _initTestDatabase();
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    vi.mocked(runContainerAgent).mockReset();
  });

  afterEach(() => {
    _resetSchedulerLoopForTests();
    vi.useRealTimers();
  });

  it('does not send the limit text, and runs again after the reset', async () => {
    addDueTask('nightly', 'cron', '30 23 * * *');
    agentAnswers(LIMIT);
    const sendMessage = vi.fn(async () => {});

    await runDueTasks(sendMessage);

    const sent = sendMessage.mock.calls.map((call) => String(call.at(1)));
    expect(sent.some((text) => text.includes("You've hit your limit"))).toBe(
      false,
    );
    const task = getTaskById('nightly');
    expect(task?.status).toBe('active');
    expect(deferredPastReset(task)).toBe(true);
  });

  it('keeps a once task active and defers it the same way', async () => {
    addDueTask('one-off', 'once', '2026-10-13T11:59:00.000Z');
    agentAnswers(LIMIT);

    await runDueTasks(vi.fn(async () => {}));

    const task = getTaskById('one-off');
    expect(task?.status).toBe('active');
    expect(deferredPastReset(task)).toBe(true);
  });

  it('tells the user once, and starts no other run into the window', async () => {
    addDueTask('first', 'cron', '30 23 * * *');
    addDueTask('second', 'interval', String(60 * 60_000));
    agentAnswers(LIMIT);
    const sendMessage = vi.fn(async () => {});

    await runDueTasks(sendMessage);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0].at(0)).toBe(CHAT);

    // A later run in the same window says nothing new, and does not start.
    vi.setSystemTime(NOW + 30 * 60_000);
    addDueTask('third', 'cron', '30 23 * * *');
    await runDueTasks(sendMessage);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
    for (const id of ['first', 'second', 'third']) {
      expect(deferredPastReset(getTaskById(id)), id).toBe(true);
    }
  });

  it('closes and idles nothing from a callback that runs after the run', async () => {
    // The runner stops waiting for a stuck callback; by the time it finishes,
    // the group's container can be a different one.
    addDueTask('nightly', 'cron', '30 23 * * *');
    let late: ((output: ContainerOutput) => Promise<void>) | undefined;
    vi.mocked(runContainerAgent).mockImplementation(
      async (_group, _input, _onProcess, onOutput) => {
        late = onOutput;
        return { status: 'success', result: null, newSessionId: 'session-1' };
      },
    );
    const sendMessage = vi.fn(async () => {});
    const queue = await runDueTasks(sendMessage);

    await late?.({
      status: 'success',
      result: 'Batch done: 3 papers uploaded.',
      newSessionId: 'session-1',
    });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(sendMessage).toHaveBeenCalledWith(
      CHAT,
      'Batch done: 3 papers uploaded.',
    );
    expect(queue.closeStdin).not.toHaveBeenCalled();
    expect(queue.notifyIdle).not.toHaveBeenCalled();
  });

  it('does not re-run a run that sent its result before a later turn was refused', async () => {
    // A background-task nudge, say: the run answered, then its next turn hit
    // the limit. Running it again after the reset would send the answer twice.
    addDueTask('nightly', 'cron', '30 23 * * *');
    agentAnswers('Batch done: 3 papers uploaded.', LIMIT);
    const sendMessage = vi.fn(async () => {});

    await runDueTasks(sendMessage);

    expect(sendMessage).toHaveBeenCalledWith(
      CHAT,
      'Batch done: 3 papers uploaded.',
    );
    const task = getTaskById('nightly');
    expect(task?.status).toBe('active');
    expect(deferredPastReset(task)).toBe(false);
    expect(Date.parse(task!.next_run!)).toBeGreaterThan(NOW);
    expect(task?.last_result).toMatch(/^Error: .*usage limit/i);
  });

  it('does not re-run a run answered on a turn after the refusal', async () => {
    // Extra usage, say: the refusal came first and a later turn got through.
    addDueTask('one-off', 'once', '2026-10-13T11:59:00.000Z');
    agentAnswers(LIMIT, 'Batch done: 3 papers uploaded.');

    await runDueTasks(vi.fn(async () => {}));

    const task = getTaskById('one-off');
    expect(task?.status).toBe('completed');
    expect(task?.next_run).toBeNull();
    expect(task?.last_result).toMatch(/^Error: .*usage limit/i);
  });

  it('defers a run whose only other result was an API error', async () => {
    // The runner forwards an is_error result as a success, so its text is
    // the error, and nothing ran.
    addDueTask('nightly', 'cron', '30 23 * * *');
    agentAnswers('API Error: 500 {"type":"error"}', LIMIT);

    await runDueTasks(vi.fn(async () => {}));

    expect(deferredPastReset(getTaskById('nightly'))).toBe(true);
  });

  it('returns to its own schedule once a run gets through', async () => {
    addDueTask('nightly', 'cron', '30 23 * * *');
    agentAnswers(LIMIT);
    await runDueTasks(vi.fn(async () => {}));
    const deferredTo = Date.parse(getTaskById('nightly')!.next_run!);

    vi.setSystemTime(deferredTo + 60_000);
    agentAnswers('Batch done: 3 papers uploaded.');
    const sendMessage = vi.fn(async () => {});
    await runDueTasks(sendMessage);

    expect(sendMessage).toHaveBeenCalledWith(
      CHAT,
      'Batch done: 3 papers uploaded.',
    );
    const task = getTaskById('nightly');
    expect(deferredPastReset(task)).toBe(false);
    expect(Date.parse(task!.next_run!)).toBeGreaterThan(deferredTo);
  });
});

describe('a scheduled run while a usage-limit window is open', () => {
  beforeEach(() => {
    _initTestDatabase();
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    vi.mocked(runContainerAgent).mockReset();
  });

  afterEach(() => {
    _resetSchedulerLoopForTests();
    vi.useRealTimers();
  });

  it('starts no container and moves the run past the reset', async () => {
    // A message was refused a minute ago; the window it opened is announced.
    recordUsageLimit(LIMIT);
    addDueTask('nightly', 'cron', '30 23 * * *');
    addDueTask('one-off', 'once', '2026-10-13T11:59:00.000Z');
    agentAnswers('Batch done: 3 papers uploaded.');
    const sendMessage = vi.fn(async () => {});

    await runDueTasks(sendMessage);

    expect(runContainerAgent).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
    for (const id of ['nightly', 'one-off']) {
      const task = getTaskById(id);
      expect(task?.status, id).toBe('active');
      expect(deferredPastReset(task), id).toBe(true);
      expect(task?.last_result, id).toMatch(/^Error: .*usage limit/i);
    }
  });

  it('closes the window when a run started before it answers', async () => {
    addDueTask('nightly', 'cron', '30 23 * * *');
    vi.mocked(runContainerAgent).mockImplementation(
      async (_group, _input, _onProcess, onOutput) => {
        // A message is refused while this run is under way...
        recordUsageLimit(LIMIT);
        // ...and then this run's request gets through.
        await onOutput?.({
          status: 'success',
          result: 'Batch done: 3 papers uploaded.',
          newSessionId: 'session-1',
        });
        return { status: 'success', result: null, newSessionId: 'session-1' };
      },
    );
    const lifted = vi.fn();

    await runDueTasks(
      vi.fn(async () => {}),
      lifted,
    );

    expect(usageLimitHeldUntil()).toBeNull();
    expect(lifted).toHaveBeenCalledTimes(1);
  });

  it('keeps the window open when the run gets an API error instead', async () => {
    addDueTask('nightly', 'cron', '30 23 * * *');
    vi.mocked(runContainerAgent).mockImplementation(
      async (_group, _input, _onProcess, onOutput) => {
        recordUsageLimit(LIMIT);
        await onOutput?.({
          status: 'success',
          result: 'API Error: Rate limit reached',
          newSessionId: 'session-1',
        });
        return { status: 'success', result: null, newSessionId: 'session-1' };
      },
    );
    const lifted = vi.fn();

    await runDueTasks(
      vi.fn(async () => {}),
      lifted,
    );

    expect(usageLimitHeldUntil()).not.toBeNull();
    expect(lifted).not.toHaveBeenCalled();
  });
});
