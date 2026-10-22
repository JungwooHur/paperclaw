import { ChildProcess } from 'child_process';
import { CronExpressionParser } from 'cron-parser';
import fs from 'fs';

import {
  ASSISTANT_NAME,
  MAIN_GROUP_FOLDER,
  SCHEDULER_POLL_INTERVAL,
  TIMEZONE,
} from './config.js';
import {
  ContainerOutput,
  runContainerAgent,
  writeTasksSnapshot,
} from './container-runner.js';
import {
  getAllTasks,
  getDueTasks,
  getTaskById,
  logTaskRun,
  updateTask,
  updateTaskAfterRun,
} from './db.js';
import { GroupQueue } from './group-queue.js';
import { resolveGroupFolderPath } from './group-folder.js';
import { logger } from './logger.js';
import { RegisteredGroup, ScheduledTask } from './types.js';
import {
  closeUsageLimitOnAnswer,
  deferredTaskRun,
  isAgentAnswer,
  recordUsageLimit,
  usageLimitResetsAt,
} from './usage-limit.js';

// After a query turn ends, close the task container promptly. Tasks are
// single-turn, so there is no reason to wait for the group's hard timeout; the
// short delay lets any final MCP calls land.
export const TASK_CLOSE_DELAY_MS = 10000;

export interface SchedulerDependencies {
  registeredGroups: () => Record<string, RegisteredGroup>;
  getSessions: () => Record<string, string>;
  queue: GroupQueue;
  onProcess: (
    groupJid: string,
    proc: ChildProcess,
    containerName: string,
    groupFolder: string,
  ) => void;
  sendMessage: (jid: string, text: string) => Promise<void>;
  /**
   * A run's answer closed the usage-limit window: run the held messages now
   * rather than at the reset the window had.
   */
  onUsageLimitLifted: () => void;
}

async function runTask(
  queuedTask: ScheduledTask,
  deps: SchedulerDependencies,
): Promise<void> {
  // The row was read when the task was enqueued. If the group's container was
  // busy it waited in the queue, and may since have been paused or cancelled.
  const task = getTaskById(queuedTask.id);
  if (!task || task.status !== 'active') {
    logger.info(
      { taskId: queuedTask.id, status: task?.status ?? 'deleted' },
      'Skipping queued task that is no longer active',
    );
    return;
  }

  // A usage-limit window is open, so the run would only be refused. Start
  // nothing, and move this run past the reset as a refused run is moved.
  const openReset = usageLimitResetsAt();
  if (openReset) {
    const deferredTo = deferredTaskRun(openReset);
    const error = `Usage limit window open, deferred to ${deferredTo.toISOString()}`;
    logger.info(
      { taskId: task.id, deferredTo },
      'Usage limit window open, task deferred',
    );
    logTaskRun({
      task_id: task.id,
      run_at: new Date().toISOString(),
      duration_ms: 0,
      status: 'error',
      result: null,
      error,
    });
    updateTaskAfterRun(task.id, deferredTo.toISOString(), `Error: ${error}`);
    return;
  }

  const startTime = Date.now();
  let groupDir: string;
  try {
    groupDir = resolveGroupFolderPath(task.group_folder);
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    // Stop retry churn for malformed legacy rows.
    updateTask(task.id, { status: 'paused' });
    logger.error(
      { taskId: task.id, groupFolder: task.group_folder, error },
      'Task has invalid group folder',
    );
    logTaskRun({
      task_id: task.id,
      run_at: new Date().toISOString(),
      duration_ms: Date.now() - startTime,
      status: 'error',
      result: null,
      error,
    });
    return;
  }
  fs.mkdirSync(groupDir, { recursive: true });

  logger.info(
    { taskId: task.id, group: task.group_folder },
    'Running scheduled task',
  );

  const groups = deps.registeredGroups();
  const group = Object.values(groups).find(
    (g) => g.folder === task.group_folder,
  );

  if (!group) {
    logger.error(
      { taskId: task.id, groupFolder: task.group_folder },
      'Group not found for task',
    );
    logTaskRun({
      task_id: task.id,
      run_at: new Date().toISOString(),
      duration_ms: Date.now() - startTime,
      status: 'error',
      result: null,
      error: `Group not found: ${task.group_folder}`,
    });
    return;
  }

  // Update tasks snapshot for container to read (filtered by group)
  const isMain = task.group_folder === MAIN_GROUP_FOLDER;
  const tasks = getAllTasks();
  writeTasksSnapshot(
    task.group_folder,
    isMain,
    tasks.map((t) => ({
      id: t.id,
      groupFolder: t.group_folder,
      prompt: t.prompt,
      schedule_type: t.schedule_type,
      schedule_value: t.schedule_value,
      status: t.status,
      next_run: t.next_run,
    })),
  );

  let result: string | null = null;
  let error: string | null = null;
  // Set when a turn was refused by the usage limit rather than answered.
  // Typed by assertion: set in the callback, it would otherwise narrow to null.
  let limitResetsAt = null as Date | null;
  // Set when a turn ended with the agent's own answer, so the run's work went
  // out. Not read from `result`, which the run's final output can also set.
  let answered = false;

  // For group context mode, use the group's current session
  const sessions = deps.getSessions();
  const sessionId =
    task.context_mode === 'group' ? sessions[task.group_folder] : undefined;

  let closeTimer: ReturnType<typeof setTimeout> | null = null;
  // Set once the run returns. The runner stops waiting for a stuck callback
  // after OUTPUT_SETTLE_TIMEOUT; one that finishes later must not close or
  // idle the group's next container.
  let runEnded = false;

  const scheduleClose = () => {
    if (closeTimer || runEnded) return; // already scheduled, or too late
    closeTimer = setTimeout(() => {
      logger.debug({ taskId: task.id }, 'Closing task container after result');
      deps.queue.closeStdin(task.chat_jid);
    }, TASK_CLOSE_DELAY_MS);
  };

  try {
    const output = await runContainerAgent(
      group,
      {
        prompt: task.prompt,
        sessionId,
        groupFolder: task.group_folder,
        chatJid: task.chat_jid,
        isMain,
        isScheduledTask: true,
        assistantName: ASSISTANT_NAME,
      },
      (proc, containerName) =>
        deps.onProcess(task.chat_jid, proc, containerName, task.group_folder),
      async (streamedOutput: ContainerOutput) => {
        if (streamedOutput.result) {
          const limit = recordUsageLimit(streamedOutput.result);
          if (limit) {
            // Not an answer: the work did not run. Hold it until the reset.
            limitResetsAt = limit.resetsAt;
            if (limit.notice)
              await deps.sendMessage(task.chat_jid, limit.notice);
          } else {
            result = streamedOutput.result;
            if (isAgentAnswer(streamedOutput.result)) answered = true;
            // A request got through: an open window is over.
            if (closeUsageLimitOnAnswer(streamedOutput.result)) {
              deps.onUsageLimitLifted();
            }
            // Forward result to user (sendMessage handles formatting)
            await deps.sendMessage(task.chat_jid, streamedOutput.result);
          }
          scheduleClose();
        }
        if (streamedOutput.status === 'success') {
          // The runner writes a success marker only when a query turn ends: an
          // SDK result, with or without text, or the session update after the
          // query. A result with no text (error_max_turns carries none) used to
          // leave the container waiting on IPC until the group's hard timeout,
          // holding the group's queue for hours.
          scheduleClose();
          if (!runEnded) deps.queue.notifyIdle(task.chat_jid);
        }
        if (streamedOutput.status === 'error') {
          error = streamedOutput.error || 'Unknown error';
        }
      },
    );
    runEnded = true;

    if (closeTimer) clearTimeout(closeTimer);

    if (output.status === 'error') {
      error = output.error || 'Unknown error';
    } else if (output.result) {
      // Messages are sent via MCP tool (IPC), result text is just logged.
      // Never a refusal: with a streaming callback every exit returns null.
      result = output.result;
    }

    logger.info(
      { taskId: task.id, durationMs: Date.now() - startTime },
      'Task completed',
    );
  } catch (err) {
    runEnded = true;
    if (closeTimer) clearTimeout(closeTimer);
    error = err instanceof Error ? err.message : String(err);
    logger.error({ taskId: task.id, error }, 'Task failed');
  }

  const durationMs = Date.now() - startTime;
  // Run again after the reset only when nothing ran. A run that answered on
  // any turn, before or after the refusal, has sent that answer: running it
  // again would send it twice. Output sent only through send_message is not
  // seen here, so such a run still counts as one that did nothing.
  const deferredTo =
    limitResetsAt && !answered ? deferredTaskRun(limitResetsAt) : null;
  if (deferredTo) {
    error = `Usage limit reached, deferred to ${deferredTo.toISOString()}`;
    logger.warn({ taskId: task.id, deferredTo }, 'Task hit the usage limit');
  } else if (limitResetsAt) {
    const resetsAt = limitResetsAt.toISOString();
    error = `Usage limit reached after the run answered, not run again (resets ${resetsAt})`;
    logger.warn(
      { taskId: task.id, resetsAt },
      'Task hit the usage limit after answering, keeping its schedule',
    );
  }

  logTaskRun({
    task_id: task.id,
    run_at: new Date().toISOString(),
    duration_ms: durationMs,
    status: error ? 'error' : 'success',
    result,
    error,
  });

  let nextRun: string | null = null;
  if (deferredTo) {
    // This run only: the one after it is computed from the schedule again.
    // A 'once' task keeps a next_run, so it stays active instead of completing.
    nextRun = deferredTo.toISOString();
  } else if (task.schedule_type === 'cron') {
    const interval = CronExpressionParser.parse(task.schedule_value, {
      tz: TIMEZONE,
    });
    nextRun = interval.next().toISOString();
  } else if (task.schedule_type === 'interval') {
    const ms = parseInt(task.schedule_value, 10);
    nextRun = new Date(Date.now() + ms).toISOString();
  }
  // 'once' tasks have no next run

  const resultSummary = error
    ? `Error: ${error}`
    : result
      ? result.slice(0, 200)
      : 'Completed';
  updateTaskAfterRun(task.id, nextRun, resultSummary);
}

let schedulerRunning = false;

export function startSchedulerLoop(deps: SchedulerDependencies): void {
  if (schedulerRunning) {
    logger.debug('Scheduler loop already running, skipping duplicate start');
    return;
  }
  schedulerRunning = true;
  logger.info('Scheduler loop started');

  const loop = async () => {
    try {
      const dueTasks = getDueTasks();
      if (dueTasks.length > 0) {
        logger.info({ count: dueTasks.length }, 'Found due tasks');
      }

      for (const task of dueTasks) {
        // Re-check task status in case it was paused/cancelled
        const currentTask = getTaskById(task.id);
        if (!currentTask || currentTask.status !== 'active') {
          continue;
        }

        deps.queue.enqueueTask(currentTask.chat_jid, currentTask.id, () =>
          runTask(currentTask, deps),
        );
      }
    } catch (err) {
      logger.error({ err }, 'Error in scheduler loop');
    }

    setTimeout(loop, SCHEDULER_POLL_INTERVAL);
  };

  loop();
}

/** @internal - for tests only. */
export function _resetSchedulerLoopForTests(): void {
  schedulerRunning = false;
}
