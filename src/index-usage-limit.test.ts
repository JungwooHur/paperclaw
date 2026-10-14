import fs from 'fs';
import os from 'os';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

import { ContainerOutput, runContainerAgent } from './container-runner.js';
import { cleanupOrphans } from './container-runtime.js';
import {
  _initTestDatabase,
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
  _setChannels,
  _setRegisteredGroups,
} from './index.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';
import { recordUsageLimit, usageLimitHeldUntil } from './usage-limit.js';

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

/** Pipes write real input files, the way GroupQueue.sendMessage does. */
function pipesWriteFiles(): void {
  let written = 0;
  vi.spyOn(GroupQueue.prototype, 'sendMessage').mockImplementation(
    (_jid, text) => {
      written++;
      const file = path.join(
        inputDir,
        `${String(written).padStart(4, '0')}.json`,
      );
      fs.writeFileSync(file, JSON.stringify({ type: 'message', text }));
      return file;
    },
  );
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
    pipesWriteFiles();
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
