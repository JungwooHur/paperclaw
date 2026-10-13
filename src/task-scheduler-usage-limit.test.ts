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

const LIMIT = "You've hit your limit · resets Oct 14, 10am (Asia/Seoul)";
const NOW = Date.parse('2026-10-13T12:00:00.000Z'); // 21:00 in Seoul
const RESET = Date.parse('2026-10-14T01:00:00.000Z');
const CHAT = 'main@g.us';

function agentAnswers(text: string): void {
  vi.mocked(runContainerAgent).mockImplementation(
    async (_group, _input, _onProcess, onOutput) => {
      const output: ContainerOutput = {
        status: 'success',
        result: text,
        newSessionId: 'session-1',
      };
      await onOutput?.(output);
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
): Promise<void> {
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
    queue: {
      enqueueTask: (_jid: string, _id: string, fn: () => Promise<void>) => {
        void fn();
      },
      closeStdin: vi.fn(),
      notifyIdle: vi.fn(),
    } as any,
    onProcess: () => {},
    sendMessage,
  });
  await vi.advanceTimersByTimeAsync(10);
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

  it('tells the user once, however many runs hit the same window', async () => {
    addDueTask('first', 'cron', '30 23 * * *');
    addDueTask('second', 'interval', String(60 * 60_000));
    agentAnswers(LIMIT);
    const sendMessage = vi.fn(async () => {});

    await runDueTasks(sendMessage);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0].at(0)).toBe(CHAT);

    // A later hit in the same window says nothing new.
    vi.setSystemTime(NOW + 30 * 60_000);
    addDueTask('third', 'cron', '30 23 * * *');
    await runDueTasks(sendMessage);
    expect(sendMessage).toHaveBeenCalledTimes(1);
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
