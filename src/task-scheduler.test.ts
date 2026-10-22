import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./container-runner.js', () => ({
  runContainerAgent: vi.fn(),
  writeTasksSnapshot: vi.fn(),
}));

import { ContainerOutput, runContainerAgent } from './container-runner.js';
import {
  _initTestDatabase,
  createTask,
  deleteTask,
  getTaskById,
  updateTask,
} from './db.js';
import {
  _resetSchedulerLoopForTests,
  startSchedulerLoop,
  TASK_CLOSE_DELAY_MS,
} from './task-scheduler.js';

describe('task scheduler', () => {
  beforeEach(() => {
    _initTestDatabase();
    _resetSchedulerLoopForTests();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('pauses due tasks with invalid group folders to prevent retry churn', async () => {
    createTask({
      id: 'task-invalid-folder',
      group_folder: '../../outside',
      chat_jid: 'bad@g.us',
      prompt: 'run',
      schedule_type: 'once',
      schedule_value: '2026-02-22T00:00:00.000Z',
      context_mode: 'isolated',
      next_run: new Date(Date.now() - 60_000).toISOString(),
      status: 'active',
      created_at: '2026-02-22T00:00:00.000Z',
    });

    const enqueueTask = vi.fn(
      (_groupJid: string, _taskId: string, fn: () => Promise<void>) => {
        void fn();
      },
    );

    startSchedulerLoop({
      registeredGroups: () => ({}),
      getSessions: () => ({}),
      queue: { enqueueTask } as any,
      onProcess: () => {},
      sendMessage: async () => {},
      onUsageLimitChanged: () => {},
    });

    await vi.advanceTimersByTimeAsync(10);

    const task = getTaskById('task-invalid-folder');
    expect(task?.status).toBe('paused');
  });

  // A task that comes due while its group's container is busy waits in the
  // queue; by the time its turn comes it may have been paused or cancelled.
  describe.each([
    ['paused', (id: string) => updateTask(id, { status: 'paused' })],
    ['cancelled', (id: string) => deleteTask(id)],
  ])('a queued task %s before its turn', (_label, change) => {
    it('does not run', async () => {
      createTask({
        id: 'task-queued',
        group_folder: 'main',
        chat_jid: 'main@g.us',
        prompt: 'run',
        schedule_type: 'cron',
        schedule_value: '30 23 * * *',
        context_mode: 'isolated',
        next_run: new Date(Date.now() - 60_000).toISOString(),
        status: 'active',
        created_at: '2026-02-22T00:00:00.000Z',
      });

      let queuedRun: (() => Promise<void>) | undefined;
      const enqueueTask = vi.fn(
        (_groupJid: string, _taskId: string, fn: () => Promise<void>) => {
          queuedRun ??= fn;
        },
      );
      const registeredGroups = vi.fn(() => ({}));

      startSchedulerLoop({
        registeredGroups,
        getSessions: () => ({}),
        queue: { enqueueTask } as any,
        onProcess: () => {},
        sendMessage: async () => {},
        onUsageLimitChanged: () => {},
      });
      await vi.advanceTimersByTimeAsync(10);
      expect(queuedRun).toBeDefined();

      change('task-queued');
      await queuedRun!();

      expect(registeredGroups).not.toHaveBeenCalled();
    });
  });

  // A turn the SDK ends without text — error_max_turns carries none — is still
  // the end of a single-turn run. Left open, the runner waits on IPC until the
  // group's hard timeout, and the group's queue is blocked the whole time.
  it('closes a run whose only output is a turn end with no text', async () => {
    createTask({
      id: 'task-silent',
      group_folder: 'main',
      chat_jid: 'main@g.us',
      prompt: 'resume the batch',
      schedule_type: 'once',
      schedule_value: '',
      context_mode: 'isolated',
      next_run: new Date(Date.now() - 60_000).toISOString(),
      status: 'active',
      created_at: '2026-10-01T00:00:00.000Z',
    });

    let exit: (() => void) | undefined;
    vi.mocked(runContainerAgent).mockImplementation(
      async (_group, _input, _onProcess, onOutput) => {
        const turnEnd: ContainerOutput = {
          status: 'success',
          result: null,
          newSessionId: 'session-1',
        };
        await onOutput?.(turnEnd);
        // The container exits only once it is told to close.
        await new Promise<void>((resolve) => {
          exit = resolve;
        });
        return { status: 'success', result: null, newSessionId: 'session-1' };
      },
    );
    let started = false;
    const queue = {
      enqueueTask: (_jid: string, _id: string, fn: () => Promise<void>) => {
        if (started) return;
        started = true;
        void fn();
      },
      closeStdin: vi.fn(() => exit?.()),
      notifyIdle: vi.fn(),
    };

    startSchedulerLoop({
      registeredGroups: () => ({
        'main@g.us': {
          name: 'Main',
          folder: 'main',
          trigger: '@Andy',
          added_at: '2026-10-01T00:00:00.000Z',
        },
      }),
      getSessions: () => ({}),
      queue: queue as any,
      onProcess: () => {},
      sendMessage: async () => {},
      onUsageLimitChanged: () => {},
    });

    await vi.advanceTimersByTimeAsync(10);
    expect(queue.closeStdin).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(TASK_CLOSE_DELAY_MS);
    expect(queue.closeStdin).toHaveBeenCalledWith('main@g.us');
    expect(getTaskById('task-silent')?.status).toBe('completed');
  });
});
