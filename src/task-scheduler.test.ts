import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
      });
      await vi.advanceTimersByTimeAsync(10);
      expect(queuedRun).toBeDefined();

      change('task-queued');
      await queuedRun!();

      expect(registeredGroups).not.toHaveBeenCalled();
    });
  });
});
