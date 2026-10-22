import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The group queue writes follow-ups and the close sentinel under DATA_DIR;
// keep them out of the checkout's data/.
const { dataDir } = vi.hoisted(() => ({
  dataDir: `${process.env.TMPDIR || '/tmp'}/usage-limit-data-${process.pid}`,
}));
vi.mock('./config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./config.js')>()),
  DATA_DIR: dataDir,
}));
vi.mock('./container-runner.js', () => ({
  runContainerAgent: vi.fn(),
  writeTasksSnapshot: vi.fn(),
  writeGroupsSnapshot: vi.fn(),
}));
// Startup stops the last process's containers; there are none in a test.
vi.mock('./container-runtime.js', () => ({
  cleanupOrphans: vi.fn(),
  ensureContainerRuntimeRunning: vi.fn(),
}));
// Answers here are short and never filed, but keep the Q&A filer from ever
// starting a process from a test.
vi.mock('./qa-followup.js', () => ({
  qaFollowupCommand: vi.fn(),
  worthFilingCheck: () => false,
}));

import { ASSISTANT_NAME } from './config.js';
import { ContainerOutput, runContainerAgent } from './container-runner.js';
import { cleanupOrphans } from './container-runtime.js';
import {
  _initTestDatabase,
  createTask,
  getAllSessions,
  getRouterState,
  setSession,
  storeChatMetadata,
  storeMessage,
} from './db.js';
import { GroupQueue } from './group-queue.js';
import {
  _loadState,
  _pipeToActiveContainer,
  _processGroupMessages,
  _recoverPendingMessages,
  _restoreStartupState,
  _schedulerDependencies,
  _setChannels,
  _setRegisteredGroups,
} from './index.js';
import {
  _resetSchedulerLoopForTests,
  startSchedulerLoop,
} from './task-scheduler.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';
import {
  recordUsageLimit,
  usageLimitHeldUntil,
  usageLimitResetsAt,
} from './usage-limit.js';

const LIMIT = "You've hit your limit · resets Oct 14, 10am (Asia/Seoul)";
const NOW = Date.parse('2026-10-13T12:00:00.000Z'); // 21:00 in Seoul
const RESET = Date.parse('2026-10-14T01:00:00.000Z');
const CHAT = 'main@g.us';
const MAIN_GROUP: RegisteredGroup = {
  name: 'Main',
  folder: 'main',
  trigger: '@Andy',
  added_at: '2026-10-01T00:00:00.000Z',
};

const sendMessage = vi.fn(async (_jid: string, _text: string) => {});
const channel: Channel = {
  name: 'test',
  connect: async () => {},
  sendMessage,
  isConnected: () => true,
  ownsJid: (jid) => jid === CHAT,
  disconnect: async () => {},
  setTyping: async () => {},
};

function agentReplies(...results: string[]): void {
  vi.mocked(runContainerAgent).mockImplementation(
    async (_group, input, _onProcess, onOutput) => {
      bootPrompts.push(startupPrompt(input.prompt));
      for (const result of results) {
        const output: ContainerOutput = {
          status: 'success',
          result,
          newSessionId: 'session-new',
        };
        await onOutput?.(output);
      }
      // The session-update marker the runner sends after each query.
      await onOutput?.({
        status: 'success',
        result: null,
        newSessionId: 'session-new',
      });
      return { status: 'success', result: null, newSessionId: 'session-new' };
    },
  );
}

function userSays(id: string, content: string, at: number): NewMessage {
  const message: NewMessage = {
    id,
    chat_jid: CHAT,
    sender: 'user@s.whatsapp.net',
    sender_name: 'User',
    content,
    timestamp: new Date(at).toISOString(),
  };
  storeMessage(message);
  return message;
}

function sentTexts(): string[] {
  return sendMessage.mock.calls.map((call) => call[1]);
}

function promptOfRun(index: number): string {
  return vi.mocked(runContainerAgent).mock.calls[index][1].prompt;
}

function timesIn(text: string, part: string): number {
  return text.split(part).length - 1;
}

// Stands in for data/ipc/<folder>/input, which the host writes follow-ups to
// and a container drains.
let inputDir: string;
// The first prompt of each container agentReplies started, as the runner
// builds it: the host's prompt plus every input file it drains at start-up.
let bootPrompts: string[];

/**
 * Pipes write real input files, the way GroupQueue.sendMessage does while the
 * group's container runs. Restore the spy once it no longer does.
 */
function pipesWriteFiles() {
  let written = 0;
  return vi
    .spyOn(GroupQueue.prototype, 'sendMessage')
    .mockImplementation((_jid, text) => {
      written++;
      const file = path.join(
        inputDir,
        `${String(written).padStart(4, '0')}.json`,
      );
      fs.writeFileSync(file, JSON.stringify({ type: 'message', text }));
      return file;
    });
}

function inputFiles(): string[] {
  return fs
    .readdirSync(inputDir)
    .filter((f) => f.endsWith('.json'))
    .sort();
}

/** The runner's start-up drain: every input file, in order, into the prompt. */
function startupPrompt(prompt: string): string {
  const drained = inputFiles().map((file) => {
    const filePath = path.join(inputDir, file);
    const { text } = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    fs.unlinkSync(filePath);
    return text as string;
  });
  return [prompt, ...drained].join('\n');
}

let closeStdin: ReturnType<typeof vi.spyOn>;
let enqueueMessageCheck: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  inputDir = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-limit-input-'));
  bootPrompts = [];
  _initTestDatabase();
  setSession('main', 'session-old');
  // A fresh process: cursors and sessions come from the (empty) database.
  _loadState();
  _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
  _setChannels([channel]);
  storeChatMetadata(CHAT, new Date(NOW).toISOString(), 'Main', 'test', true);
  sendMessage.mockClear();
  vi.mocked(runContainerAgent).mockReset();
  closeStdin = vi.spyOn(GroupQueue.prototype, 'closeStdin');
  enqueueMessageCheck = vi
    .spyOn(GroupQueue.prototype, 'enqueueMessageCheck')
    .mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  fs.rmSync(inputDir, { recursive: true, force: true });
  fs.rmSync(dataDir, { recursive: true, force: true });
});

describe('a message answered with the usage limit', () => {
  it('is held for after the reset instead of answered', async () => {
    userSays('m1', 'translate the new paper', NOW - 60_000);
    agentReplies(LIMIT);

    // Handled: the queue must not retry it into the same limit.
    expect(await _processGroupMessages(CHAT)).toBe(true);

    expect(sentTexts().some((t) => t.includes("You've hit your limit"))).toBe(
      false,
    );
    expect(sendMessage).toHaveBeenCalledTimes(1); // the notice
    expect(closeStdin).toHaveBeenCalledWith(CHAT);
    expect(getAllSessions()).toEqual({ main: 'session-old' });

    // Nothing else arrives, and the request is still picked up after the reset.
    expect(enqueueMessageCheck).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(RESET + 5 * 60_000 - Date.now());
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
  });

  it('keeps the stored session when the container exits with an error after the refusal', async () => {
    // The CLI ends print mode with exit code 1 after an error result, so once
    // the host closes stdin the runner reports an error and exits non-zero.
    userSays('m1', 'and section 3?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        await onOutput?.({
          status: 'error',
          result: null,
          newSessionId: 'session-old',
          error: 'Claude Code process exited with code 1',
        });
        return {
          status: 'error',
          result: null,
          error: 'Container exited with code 1',
        };
      },
    );

    expect(await _processGroupMessages(CHAT)).toBe(true);
    expect(getAllSessions()).toEqual({ main: 'session-old' });
  });

  it('starts no container while the window is open, then picks the messages up', async () => {
    userSays('m1', 'translate the new paper', NOW - 60_000);
    agentReplies(LIMIT);
    await _processGroupMessages(CHAT);

    vi.setSystemTime(NOW + 10 * 60_000);
    userSays('m2', 'and the one after it', NOW + 10 * 60_000);
    expect(await _processGroupMessages(CHAT)).toBe(true);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
    expect(enqueueMessageCheck).not.toHaveBeenCalled();

    // The window closes shortly after the reset, and recovery runs on its own.
    await vi.advanceTimersByTimeAsync(RESET - Date.now());
    expect(enqueueMessageCheck).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);

    agentReplies('Both are done.');
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(2);
    expect(promptOfRun(1)).toContain('translate the new paper');
    expect(promptOfRun(1)).toContain('and the one after it');
    expect(sentTexts()).toContain('Both are done.');
  });

  it('says so once per window', async () => {
    userSays('m1', 'translate the new paper', NOW - 60_000);
    agentReplies(LIMIT);
    await _processGroupMessages(CHAT);
    // The scheduler hitting the same window must not repeat it either.
    expect(recordUsageLimit(LIMIT)?.notice).toBeNull();
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  it('does not re-run a request that was already answered', async () => {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    agentReplies('It describes the method.', LIMIT);
    await _processGroupMessages(CHAT);
    expect(sentTexts()).toContain('It describes the method.');

    vi.setSystemTime(RESET + 10 * 60_000);
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
  });

  it('keeps a follow-up piped in during the answer pending when the limit refuses it', async () => {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    const pipes = pipesWriteFiles();
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        // The follow-up arrives while the first answer is being written, and
        // the message loop pipes it into this container.
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(true);
        await onOutput?.({
          status: 'success',
          result: 'It describes the method.',
          newSessionId: 'session-new',
        });
        // The follow-up's own turn is the one the limit refuses.
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(sentTexts()).toContain('It describes the method.');
    // The container is gone, so recovery finds none to pipe into.
    pipes.mockRestore();

    await vi.advanceTimersByTimeAsync(RESET + 5 * 60_000 - Date.now());
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
    agentReplies('It covers the experiments.');
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(2);
    expect(promptOfRun(1)).toContain('and section 3?');
    expect(promptOfRun(1)).not.toContain('what is section 2 about?');
  });

  it('puts back only the piped messages no turn has finished on', async () => {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    pipesWriteFiles();
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        const reply = (result: string) =>
          onOutput?.({
            status: 'success',
            result,
            newSessionId: 'session-new',
          });
        const m2 = userSays('m2', 'and section 3?', NOW - 40_000);
        _pipeToActiveContainer(CHAT, channel, [m2]);
        await reply('It describes the method.');
        await reply('It covers the experiments.');
        const m3 = userSays('m3', 'and the appendix?', NOW - 20_000);
        _pipeToActiveContainer(CHAT, channel, [m3]);
        await reply(LIMIT);
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);

    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('It lists the proofs.');
    await _processGroupMessages(CHAT);
    expect(promptOfRun(1)).toContain('and the appendix?');
    expect(promptOfRun(1)).not.toContain('and section 3?');
    expect(promptOfRun(1)).not.toContain('what is section 2 about?');
  });

  it('does not re-run a request answered only through send_message', async () => {
    // The paper dispatcher acks over IPC and ends its turn with an internal
    // recap, so the turn's result carries no text for the user.
    userSays('m1', 'translate the new paper', NOW - 60_000);
    agentReplies('<internal>acked, translation dispatched</internal>', LIMIT);
    await _processGroupMessages(CHAT);

    vi.setSystemTime(RESET + 10 * 60_000);
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
  });
});

describe('startup recovery', () => {
  it('waits for a window that is still open', async () => {
    recordUsageLimit(LIMIT);
    userSays('m1', 'translate the new paper', NOW - 60_000);
    const heldUntil = usageLimitHeldUntil()!.getTime();

    _loadState();
    _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
    _recoverPendingMessages();
    expect(enqueueMessageCheck).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(heldUntil - Date.now() - 1000);
    expect(enqueueMessageCheck).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2000);
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
  });

  it('recovers at once when the window is already over', () => {
    recordUsageLimit(LIMIT);
    vi.setSystemTime(RESET + 10 * 60_000);
    userSays('m1', 'translate the new paper', NOW - 60_000);

    _loadState();
    _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
    _recoverPendingMessages();
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
  });
});

describe('an answer inside an open window', () => {
  /**
   * Runs m1 in a container that answers with `result` after the window opened
   * under it (a scheduled run refused meanwhile), with m2 held, not piped.
   */
  async function answeredInsideWindow(result: string): Promise<void> {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        expect(recordUsageLimit(LIMIT)?.notice).toBeTruthy();
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(false);
        await onOutput?.({
          status: 'success',
          result,
          newSessionId: 'session-new',
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
  }

  it('closes the window and runs the held messages now, not at the reset', async () => {
    await answeredInsideWindow('It describes the method.');
    expect(usageLimitHeldUntil()).toBeNull();
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);

    agentReplies('It covers the experiments.');
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(2);
    expect(promptOfRun(1)).toContain('and section 3?');
    expect(sentTexts()).toContain('It covers the experiments.');
  });

  it('costs one refused retry when the limit still holds, and no second notice', async () => {
    await answeredInsideWindow('It describes the method.');
    agentReplies(LIMIT);
    await _processGroupMessages(CHAT);
    expect(sentTexts()).toEqual(['It describes the method.']);
    expect(usageLimitHeldUntil()).not.toBeNull();

    // Held again: nothing starts until the reset.
    await _processGroupMessages(CHAT);
    expect(runContainerAgent).toHaveBeenCalledTimes(2);
  });

  it('keeps the window open on an API error', async () => {
    await answeredInsideWindow('API Error: 500 {"type":"error"}');
    expect(usageLimitHeldUntil()).not.toBeNull();
    expect(enqueueMessageCheck).not.toHaveBeenCalled();
  });
});

// Through the module's own queue, as main() runs it: a check for a group whose
// container is still running only marks it pending, and that container takes
// it only when it closes, half an hour after its last answer if it idles.
describe('messages held for a container that is still running', () => {
  const { queue } = _schedulerDependencies();
  const m2Text = 'and section 3?';
  let exitContainer: () => void;

  beforeEach(() => {
    enqueueMessageCheck.mockRestore();
    queue.setProcessMessagesFn(_processGroupMessages);
  });

  afterEach(async () => {
    exitContainer?.();
    await vi.advanceTimersByTimeAsync(0);
    queue.setProcessMessagesFn(async () => true);
  });

  /** What the queue has piped into the group's running container. */
  function pipedTexts(): string[] {
    const dir = path.join(dataDir, 'ipc', MAIN_GROUP.folder, 'input');
    if (!fs.existsSync(dir)) return [];
    return fs
      .readdirSync(dir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8')).text);
  }

  /**
   * Starts m1 through the queue in a container that runs `turn` and then
   * stays up, idle, until the test ends it.
   */
  async function containerRunning(
    turn: (
      onOutput: (output: ContainerOutput) => Promise<void>,
    ) => Promise<void>,
  ): Promise<void> {
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, onProcess, onOutput) => {
        onProcess({} as never, 'paperclaw-main-1');
        await turn(onOutput!);
        await new Promise<void>((resolve) => {
          exitContainer = resolve;
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    queue.enqueueMessageCheck(CHAT);
    await vi.advanceTimersByTimeAsync(0);
  }

  /** m2 arrives while the window is open, and the message loop queues it. */
  function heldWhileOpen(): NewMessage {
    const m2 = userSays('m2', m2Text, Date.now());
    expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(false);
    queue.enqueueMessageCheck(CHAT);
    return m2;
  }

  async function answer(
    onOutput: (output: ContainerOutput) => Promise<void>,
    result: string,
  ): Promise<void> {
    await onOutput({ status: 'success', result, newSessionId: 'session-new' });
    await onOutput({
      status: 'success',
      result: null,
      newSessionId: 'session-new',
    });
  }

  it('go into it at once when its own answer closes the window', async () => {
    let m2!: NewMessage;
    await containerRunning(async (onOutput) => {
      // Another group is refused while m1 is in flight: the window opens.
      recordUsageLimit(LIMIT);
      m2 = heldWhileOpen();
      await answer(onOutput, 'It describes the method.');
    });
    expect(usageLimitHeldUntil()).toBeNull();
    expect(pipedTexts()).toEqual([expect.stringContaining(m2Text)]);
    // The cursor moves past it, as a pipe from the message loop moves it.
    expect(getRouterState('last_agent_timestamp')).toContain(m2.timestamp);

    // The check the loop queued finds nothing left once the container exits:
    // m2 is not run again.
    exitContainer();
    await vi.advanceTimersByTimeAsync(0);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
  });

  it('stay pending when a turn no prompt started closes the window and theirs is refused', async () => {
    let onOutput!: (output: ContainerOutput) => Promise<void>;
    await containerRunning((output) => {
      onOutput = output;
      return answer(output, 'It describes the method.');
    });
    const m1Cursor = getRouterState('last_agent_timestamp');
    // Idle. Another group is refused, and m2 is held for the window.
    recordUsageLimit(LIMIT);
    heldWhileOpen();
    // A background task's notification starts a turn of its own, and its
    // answer closes the window: m2 goes in.
    await answer(onOutput, 'Paper 2 is uploaded.');
    expect(pipedTexts()).toEqual([expect.stringContaining(m2Text)]);
    // m2's turn is refused, so m2 is pending again, not settled by the
    // answer that came before it.
    await answer(
      onOutput,
      "You're out of extra usage · resets Oct 14, 10am (Asia/Seoul)",
    );
    expect(getRouterState('last_agent_timestamp')).toBe(m1Cursor);
    exitContainer();
    await vi.advanceTimersByTimeAsync(0);
    expect(getRouterState('last_agent_timestamp')).toBe(m1Cursor);
  });

  it('stay pending when the answer that closes the window also closes it for a task', async () => {
    const task = vi.fn(async () => {});
    let m2!: NewMessage;
    await containerRunning(async (onOutput) => {
      recordUsageLimit(LIMIT);
      m2 = heldWhileOpen();
      // A scheduled run comes due meanwhile and waits for this container.
      queue.enqueueTask(CHAT, 'nightly', task);
      await answer(onOutput, 'It describes the method.');
    });
    // The container now closes for the task without reading new input, so
    // m2 is not left in a file it never reads, behind a cursor past it.
    expect(usageLimitHeldUntil()).toBeNull();
    expect(pipedTexts()).toEqual([]);
    expect(getRouterState('last_agent_timestamp')).not.toContain(m2.timestamp);

    agentReplies('It covers the experiments.');
    exitContainer();
    await vi.advanceTimersByTimeAsync(0);
    expect(task).toHaveBeenCalledTimes(1);
    expect(runContainerAgent).toHaveBeenCalledTimes(2);
    expect(promptOfRun(1)).toContain(m2Text);
    expect(sentTexts()).toContain('It covers the experiments.');
  });

  it('go into it at once when the window ends at its reset', async () => {
    const reset = NOW + 10 * 60_000; // 9:10pm in Seoul
    await containerRunning((onOutput) =>
      answer(onOutput, 'It describes the method.'),
    );
    // Then another group is refused, and its run arms recovery for the reset.
    recordUsageLimit("You've hit your limit · resets 9:10pm (Asia/Seoul)");
    _recoverPendingMessages();
    heldWhileOpen();
    expect(pipedTexts()).toEqual([]);

    await vi.advanceTimersByTimeAsync(reset + 3 * 60_000 - Date.now());
    expect(pipedTexts()).toEqual([expect.stringContaining(m2Text)]);
    exitContainer();
    await vi.advanceTimersByTimeAsync(0);
    expect(runContainerAgent).toHaveBeenCalledTimes(1);
  });

  // Recovery pipes by the message loop's rule: in a group that needs the
  // trigger, chatter waits as context until a message with the trigger comes.
  describe('in a group that needs the trigger', () => {
    const TEAM = 'team@g.us';
    const TEAM_GROUP: RegisteredGroup = {
      name: 'Team',
      folder: 'team',
      trigger: `@${ASSISTANT_NAME}`,
      added_at: '2026-10-01T00:00:00.000Z',
    };

    function teamSays(id: string, content: string, at: number): NewMessage {
      const message: NewMessage = {
        id,
        chat_jid: TEAM,
        sender: 'user@s.whatsapp.net',
        sender_name: 'User',
        content,
        timestamp: new Date(at).toISOString(),
      };
      storeMessage(message);
      return message;
    }

    function teamPiped(): string[] {
      const dir = path.join(dataDir, 'ipc', TEAM_GROUP.folder, 'input');
      if (!fs.existsSync(dir)) return [];
      return fs
        .readdirSync(dir)
        .filter((f) => f.endsWith('.json'))
        .map(
          (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8')).text,
        );
    }

    function teamCursor(): string | undefined {
      return JSON.parse(getRouterState('last_agent_timestamp') || '{}')[TEAM];
    }

    /** The team's container answered a triggered message and idles. */
    async function teamContainerRunning(): Promise<
      (output: ContainerOutput) => Promise<void>
    > {
      vi.spyOn(channel, 'ownsJid').mockImplementation((jid) => jid === TEAM);
      _setRegisteredGroups({ [TEAM]: TEAM_GROUP });
      storeChatMetadata(
        TEAM,
        new Date(NOW).toISOString(),
        'Team',
        'test',
        true,
      );
      let onOutput!: (output: ContainerOutput) => Promise<void>;
      vi.mocked(runContainerAgent).mockImplementationOnce(
        async (_group, _input, onProcess, output) => {
          onProcess({} as never, 'paperclaw-team-1');
          onOutput = output!;
          await answer(output!, 'It describes the method.');
          await new Promise<void>((resolve) => {
            exitContainer = resolve;
          });
          return {
            status: 'success',
            result: null,
            newSessionId: 'session-new',
          };
        },
      );
      teamSays(
        't1',
        `@${ASSISTANT_NAME} what is section 2 about?`,
        NOW - 60_000,
      );
      queue.enqueueMessageCheck(TEAM);
      await vi.advanceTimersByTimeAsync(0);
      expect(runContainerAgent).toHaveBeenCalledTimes(1);
      return onOutput;
    }

    it('keep chatter without the trigger out of it when the window closes', async () => {
      const onOutput = await teamContainerRunning();
      const cursor = teamCursor();
      recordUsageLimit(LIMIT);
      teamSays('t2', 'thanks, that helps', NOW - 30_000);
      // A turn of its own answers, which closes the window.
      await answer(onOutput, 'Paper 2 is uploaded.');
      expect(usageLimitHeldUntil()).toBeNull();
      expect(teamPiped()).toEqual([]);
      expect(teamCursor()).toBe(cursor);
    });

    it('go into it with that chatter once one has the trigger', async () => {
      const onOutput = await teamContainerRunning();
      recordUsageLimit(LIMIT);
      teamSays('t2', 'thanks, that helps', NOW - 30_000);
      const t3 = teamSays(
        't3',
        `@${ASSISTANT_NAME} and section 3?`,
        NOW - 20_000,
      );
      await answer(onOutput, 'Paper 2 is uploaded.');
      expect(teamPiped()).toEqual([
        expect.stringMatching(/thanks, that helps[\s\S]*and section 3\?/),
      ]);
      expect(teamCursor()).toBe(t3.timestamp);
    });
  });
});

describe('the scheduler, started as main() starts it', () => {
  afterEach(() => {
    _resetSchedulerLoopForTests();
  });

  /**
   * Starts the scheduler with main()'s dependencies and runs one task due
   * now, whose container calls `run` with a way to end a turn on a result.
   */
  async function runScheduledTask(
    run: (turn: (result: string) => Promise<void>) => Promise<void>,
  ): Promise<void> {
    createTask({
      id: 'nightly',
      group_folder: 'main',
      chat_jid: CHAT,
      prompt: 'nightly batch',
      schedule_type: 'cron',
      schedule_value: '30 23 * * *',
      context_mode: 'isolated',
      next_run: new Date(NOW - 60_000).toISOString(),
      status: 'active',
      created_at: '2026-10-01T00:00:00.000Z',
    });
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await run(async (result) => {
          await onOutput?.({
            status: 'success',
            result,
            newSessionId: 'session-task',
          });
        });
        return {
          status: 'success',
          result: null,
          newSessionId: 'session-task',
        };
      },
    );
    _resetSchedulerLoopForTests();
    startSchedulerLoop(_schedulerDependencies());
    await vi.advanceTimersByTimeAsync(10);
  }

  it('runs the held messages at once when a scheduled answer closes the window', async () => {
    await runScheduledTask(async (turn) => {
      // A message is refused while the run is under way, and held...
      recordUsageLimit(LIMIT);
      userSays('m1', 'translate the new paper', NOW - 30_000);
      // ...and then the run's own request gets through.
      await turn('Batch done: 3 papers uploaded.');
    });

    expect(usageLimitHeldUntil()).toBeNull();
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
  });

  it('runs the held messages at an earlier reset a scheduled refusal states', async () => {
    const sessionReset = Date.parse('2026-10-13T14:00:00.000Z'); // 11pm Seoul
    await runScheduledTask(async (turn) => {
      // A message is refused while the run is under way: held, with recovery
      // armed for the window's end, 10am tomorrow.
      userSays('m1', 'translate the new paper', NOW - 30_000);
      recordUsageLimit(LIMIT);
      _recoverPendingMessages();
      // The run is refused too, by a limit that resets sooner.
      await turn("You've hit your session limit · resets 11pm (Asia/Seoul)");
    });
    expect(usageLimitResetsAt()?.getTime()).toBe(sessionReset);
    expect(enqueueMessageCheck).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(sessionReset + 3 * 60_000 - Date.now());
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
  });
});

/**
 * Runs m1 into a refusal, with m2 piped just before it, then the container's
 * later turns `after` (extra usage, say). Returns while the container is
 * still running; the function it returns lets the old run finish.
 */
async function refusedAndStillRunning(
  ...after: string[]
): Promise<() => Promise<boolean>> {
  let exitContainer!: () => void;
  let refused!: () => void;
  const handled = new Promise<void>((resolve) => {
    refused = resolve;
  });
  pipesWriteFiles();
  userSays('m1', 'what is section 2 about?', NOW - 60_000);
  vi.mocked(runContainerAgent).mockImplementationOnce(
    async (_group, _input, _onProcess, onOutput) => {
      const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
      _pipeToActiveContainer(CHAT, channel, [m2]);
      for (const result of [LIMIT, ...after]) {
        await onOutput?.({
          status: 'success',
          result,
          newSessionId: 'session-new',
        });
      }
      refused();
      await new Promise<void>((resolve) => {
        exitContainer = resolve;
      });
      return { status: 'success', result: null, newSessionId: 'session-new' };
    },
  );
  const run = _processGroupMessages(CHAT);
  await handled;
  return () => {
    exitContainer();
    return run;
  };
}

/**
 * Pipes `message` once, the way the message loop would, while the host clears
 * the typing indicator: after the container has exited, before the run ends.
 */
function pipedAsItExits(message: () => NewMessage): () => boolean | undefined {
  let piped: boolean | undefined;
  vi.spyOn(channel, 'setTyping').mockImplementation(async (_jid, on) => {
    if (on || piped !== undefined) return;
    piped = _pipeToActiveContainer(CHAT, channel, [message()]);
  });
  return () => piped;
}

describe('input files around a refusal', () => {
  it('pipes nothing into a container closing on a refusal', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    let piped: boolean | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        // A follow-up arrives while the container winds down on the close
        // sentinel. A file written now would outlive it.
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        piped = _pipeToActiveContainer(CHAT, channel, [m2]);
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(piped).toBe(false);
    expect(inputFiles()).toEqual([]);

    // Held like any other message, and asked once after the reset.
    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('Both are answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'what is section 2 about?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
  });

  it('drops the file of a follow-up the refusal put back', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        // Piped just before the refusal; the runner sees the close sentinel
        // first and exits without draining it.
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(true);
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(inputFiles()).toEqual([]);

    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('Both are answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'what is section 2 about?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
  });

  it('keeps the file of a follow-up the refusal did not put back', async () => {
    // A result settles the oldest open prompt, so a background-task nudge
    // turn can settle a follow-up whose file the runner never drained. The
    // cursor stays past that follow-up, and the file is its only delivery.
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        const reply = (result: string) =>
          onOutput?.({
            status: 'success',
            result,
            newSessionId: 'session-new',
          });
        const m2 = userSays('m2', 'and section 3?', NOW - 40_000);
        _pipeToActiveContainer(CHAT, channel, [m2]);
        await reply('It describes the method.');
        await reply('The translation is still running.'); // the nudge turn
        const m3 = userSays('m3', 'and the appendix?', NOW - 20_000);
        _pipeToActiveContainer(CHAT, channel, [m3]);
        await reply(LIMIT);
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(inputFiles()).toHaveLength(1);

    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('Answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and the appendix?')).toBe(1);
    expect(bootPrompts[0]).not.toContain('what is section 2 about?');
  });

  it('keeps the file a container left when it closed on idle', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await onOutput?.({
          status: 'success',
          result: 'It describes the method.',
          newSessionId: 'session-new',
        });
        // Piped as the idle timer closed the container, which exits without
        // draining it. The next container's start-up drain delivers it.
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        _pipeToActiveContainer(CHAT, channel, [m2]);
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);

    // A window opens (the scheduler hit the limit) and holds the next message.
    recordUsageLimit(LIMIT);
    userSays('m3', 'and the appendix?', NOW - 10_000);
    await _processGroupMessages(CHAT);
    expect(inputFiles()).toHaveLength(1);

    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('Answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and the appendix?')).toBe(1);
  });

  it('pipes nothing into it as it finishes exiting past the window', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        vi.setSystemTime(RESET + 10 * 60_000); // exits after the window
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    const piped = pipedAsItExits(() =>
      userSays('m2', 'and section 3?', RESET + 9 * 60_000),
    );
    await _processGroupMessages(CHAT);
    expect(piped()).toBe(false);
    expect(inputFiles()).toEqual([]);
  });

  it('pipes nothing into it while it closes past the window', async () => {
    const exit = await refusedAndStillRunning();
    // Still winding down after the reset, so the window no longer holds.
    vi.setSystemTime(RESET + 10 * 60_000);
    expect(usageLimitHeldUntil()).toBeNull();
    const m3 = userSays('m3', 'and the appendix?', RESET + 9 * 60_000);
    expect(_pipeToActiveContainer(CHAT, channel, [m3])).toBe(false);
    await exit();
    expect(inputFiles()).toEqual([]);

    // The next container takes follow-ups again.
    let piped: boolean | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, input, _onProcess, onOutput) => {
        bootPrompts.push(startupPrompt(input.prompt));
        const m4 = userSays('m4', 'and the references?', RESET + 11 * 60_000);
        piped = _pipeToActiveContainer(CHAT, channel, [m4]);
        await onOutput?.({
          status: 'success',
          result: 'All three are answered.',
          newSessionId: 'session-new',
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(piped).toBe(true);
    for (const question of [
      'what is section 2 about?',
      'and section 3?',
      'and the appendix?',
    ]) {
      expect(timesIn(bootPrompts[0], question)).toBe(1);
    }
  });
});

describe('input files around an error', () => {
  it('drops the file of a follow-up the rollback put back', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(async () => {
      // Piped just before the agent crashed, so never drained.
      const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
      expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(true);
      return { status: 'error', result: null, error: 'exited with code 137' };
    });
    // Not handled: the queue retries, and the retry asks both again.
    expect(await _processGroupMessages(CHAT)).toBe(false);
    expect(inputFiles()).toEqual([]);

    agentReplies('Both are answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'what is section 2 about?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
  });

  it('drops the file of a follow-up piped as the container exits', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(async () => ({
      status: 'error',
      result: null,
      error: 'exited with code 137',
    }));
    const piped = pipedAsItExits(() =>
      userSays('m2', 'and section 3?', NOW - 30_000),
    );
    expect(await _processGroupMessages(CHAT)).toBe(false);
    expect(piped()).toBe(true);
    expect(inputFiles()).toEqual([]);

    agentReplies('Both are answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'what is section 2 about?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
  });

  it('keeps the file of a follow-up when the error comes after an answer', async () => {
    // A reply went out, so there is no rollback: the cursor stays past the
    // follow-up, and its file is the only delivery it has.
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        await onOutput?.({
          status: 'success',
          result: 'It describes the method.',
          newSessionId: 'session-new',
        });
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        _pipeToActiveContainer(CHAT, channel, [m2]);
        return { status: 'error', result: null, error: 'exited with code 137' };
      },
    );
    expect(await _processGroupMessages(CHAT)).toBe(true);
    expect(inputFiles()).toHaveLength(1);

    userSays('m3', 'and the appendix?', NOW - 10_000);
    agentReplies('Answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and the appendix?')).toBe(1);
    expect(bootPrompts[0]).not.toContain('what is section 2 about?');
  });
});

describe('a restart between a refusal and the container exit', () => {
  /** The new process: state from the database only, as main() starts. */
  function restart(): void {
    _restoreStartupState();
    _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
  }

  it('still finds the refused messages pending after the reset', async () => {
    const exit = await refusedAndStillRunning();
    restart();

    vi.setSystemTime(RESET + 10 * 60_000);
    _recoverPendingMessages();
    expect(enqueueMessageCheck).toHaveBeenCalledWith(CHAT);
    await exit();
  });

  it('drops the file of a follow-up the refusal put back', async () => {
    const exit = await refusedAndStillRunning();
    // Until startup stops the old container, it can still drain the file.
    let filesWhenStopped: string[] | undefined;
    vi.mocked(cleanupOrphans).mockImplementationOnce(() => {
      filesWhenStopped = inputFiles();
    });
    restart();
    expect(filesWhenStopped).toHaveLength(1);
    expect(inputFiles()).toEqual([]);

    vi.setSystemTime(RESET + 10 * 60_000);
    agentReplies('Both are answered.');
    await _processGroupMessages(CHAT);
    expect(timesIn(bootPrompts[0], 'what is section 2 about?')).toBe(1);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
    await exit();
  });

  it('sees the cursor the exit saves, after a turn past the refusal', async () => {
    // Extra usage, say: the container answers again after refusing. Which
    // prompt that turn settles is #114's to solve; a restart before the exit
    // must leave the same messages pending as the exit does.
    const exit = await refusedAndStillRunning('It covers the experiments.');
    restart();
    const atRestart = getRouterState('last_agent_timestamp');
    await exit();
    expect(getRouterState('last_agent_timestamp')).toBe(atRestart);
  });

  it('takes no pipe once the window closes before the exit', async () => {
    const exit = await refusedAndStillRunning();
    vi.setSystemTime(RESET + 10 * 60_000);
    const m3 = userSays('m3', 'and the appendix?', RESET + 9 * 60_000);
    expect(_pipeToActiveContainer(CHAT, channel, [m3])).toBe(false);
    restart();
    expect(inputFiles()).toEqual([]);

    agentReplies('All three are answered.');
    await _processGroupMessages(CHAT);
    for (const question of [
      'what is section 2 about?',
      'and section 3?',
      'and the appendix?',
    ]) {
      expect(timesIn(bootPrompts[0], question)).toBe(1);
    }
    await exit();
  });
});

// The runner waits for a run's callbacks before it returns, but only so long,
// and until that fix an error exit did not wait at all. Whatever runs late
// must leave alone the state the run's end settled.
describe('a callback that runs after its run ended', () => {
  it('leaves the next container open to pipes and the held input as the exit left it', async () => {
    pipesWriteFiles();
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    // The window's notice is slow to send.
    sendMessage.mockImplementationOnce(async () => {
      await new Promise<void>((r) => setTimeout(r, 1500));
    });
    let callbacks: Promise<void> = Promise.resolve();
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        const m2 = userSays('m2', 'and section 3?', NOW - 30_000);
        expect(_pipeToActiveContainer(CHAT, channel, [m2])).toBe(true);
        // Both prompts were in the stream, so the CLI refuses twice, back to
        // back. Each callback waits for the one before it; the container
        // exits 1 once stdin closes, before the notice is out.
        for (let i = 0; i < 2; i++) {
          callbacks = callbacks.then(() =>
            onOutput?.({
              status: 'success',
              result: LIMIT,
              newSessionId: 'session-new',
            }),
          );
        }
        return {
          status: 'error',
          result: null,
          error: 'Container exited with code 1',
        };
      },
    );
    expect(await _processGroupMessages(CHAT)).toBe(true);
    const heldAtExit = getRouterState('usage_limit_refused_input');
    const cursorAtExit = getRouterState('last_agent_timestamp');
    closeStdin.mockClear();

    await vi.advanceTimersByTimeAsync(2000);
    await callbacks;
    expect(getRouterState('usage_limit_refused_input')).toBe(heldAtExit);
    expect(getRouterState('last_agent_timestamp')).toBe(cursorAtExit);
    expect(closeStdin).not.toHaveBeenCalled();

    // After the reset, the group's first container takes follow-ups.
    vi.setSystemTime(RESET + 10 * 60_000);
    let piped: boolean | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, input, _onProcess, onOutput) => {
        bootPrompts.push(startupPrompt(input.prompt));
        const m3 = userSays('m3', 'and the appendix?', RESET + 11 * 60_000);
        piped = _pipeToActiveContainer(CHAT, channel, [m3]);
        await onOutput?.({
          status: 'success',
          result: 'All three are answered.',
          newSessionId: 'session-new',
        });
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    expect(piped).toBe(true);
    expect(timesIn(bootPrompts[0], 'and section 3?')).toBe(1);
  });

  it('delivers a late answer without closing the next container on idle', async () => {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    let late: ((output: ContainerOutput) => Promise<void>) | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        late = onOutput;
        return { status: 'success', result: null, newSessionId: 'session-new' };
      },
    );
    await _processGroupMessages(CHAT);
    const cursor = getRouterState('last_agent_timestamp');

    await late?.({
      status: 'success',
      result: 'It describes the method.',
      newSessionId: 'session-new',
    });
    expect(sentTexts()).toContain('It describes the method.');

    // Another container has the group by now; an idle timer armed by the old
    // run would close it half an hour from here.
    await vi.advanceTimersByTimeAsync(2 * 60 * 60_000);
    expect(closeStdin).not.toHaveBeenCalled();
    expect(getRouterState('last_agent_timestamp')).toBe(cursor);
  });

  it('settles nothing from an answer that arrives after a refused run', async () => {
    userSays('m1', 'what is section 2 about?', NOW - 60_000);
    let late: ((output: ContainerOutput) => Promise<void>) | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        late = onOutput;
        await onOutput?.({
          status: 'success',
          result: LIMIT,
          newSessionId: 'session-new',
        });
        return {
          status: 'error',
          result: null,
          error: 'Container exited with code 1',
        };
      },
    );
    await _processGroupMessages(CHAT);
    const cursor = getRouterState('last_agent_timestamp');
    const held = getRouterState('usage_limit_refused_input');
    const notifyIdle = vi.spyOn(GroupQueue.prototype, 'notifyIdle');

    await late?.({
      status: 'success',
      result: 'It describes the method.',
      newSessionId: 'session-new',
    });

    expect(sentTexts()).toContain('It describes the method.');
    // Settling the refused prompt now would move the cursor past a message
    // that recovery has to run after the reset.
    expect(getRouterState('last_agent_timestamp')).toBe(cursor);
    expect(getRouterState('usage_limit_refused_input')).toBe(held);
    expect(notifyIdle).not.toHaveBeenCalled();
  });

  it("keeps the next run's session from an answer that arrives after its own run", async () => {
    userSays('m1', 'what is section 2 about?', NOW - 120_000);
    let late: ((output: ContainerOutput) => Promise<void>) | undefined;
    vi.mocked(runContainerAgent).mockImplementationOnce(
      async (_group, _input, _onProcess, onOutput) => {
        late = onOutput;
        return { status: 'success', result: null, newSessionId: 'session-a' };
      },
    );
    await _processGroupMessages(CHAT);

    userSays('m2', 'and section 3?', NOW - 60_000);
    vi.mocked(runContainerAgent).mockImplementationOnce(async () => ({
      status: 'success',
      result: null,
      newSessionId: 'session-b',
    }));
    await _processGroupMessages(CHAT);
    expect(getAllSessions()).toEqual({ main: 'session-b' });

    await late?.({
      status: 'success',
      result: 'It describes the method.',
      newSessionId: 'session-a',
    });
    expect(getAllSessions()).toEqual({ main: 'session-b' });
  });
});
