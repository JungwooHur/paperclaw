import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./container-runner.js', () => ({
  runContainerAgent: vi.fn(),
  writeTasksSnapshot: vi.fn(),
  writeGroupsSnapshot: vi.fn(),
}));
// Answers here are short and never filed, but keep the Q&A filer from ever
// starting a process from a test.
vi.mock('./qa-followup.js', () => ({
  qaFollowupCommand: vi.fn(),
  worthFilingCheck: () => false,
}));

import { ContainerOutput, runContainerAgent } from './container-runner.js';
import {
  _initTestDatabase,
  getAllSessions,
  setSession,
  storeChatMetadata,
  storeMessage,
} from './db.js';
import { GroupQueue } from './group-queue.js';
import {
  _loadState,
  _processGroupMessages,
  _recoverPendingMessages,
  _setChannels,
  _setRegisteredGroups,
} from './index.js';
import { Channel, RegisteredGroup } from './types.js';
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
    async (_group, _input, _onProcess, onOutput) => {
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

function userSays(id: string, content: string, at: number): void {
  storeMessage({
    id,
    chat_jid: CHAT,
    sender: 'user@s.whatsapp.net',
    sender_name: 'User',
    content,
    timestamp: new Date(at).toISOString(),
  });
}

function sentTexts(): string[] {
  return sendMessage.mock.calls.map((call) => call[1]);
}

function promptOfRun(index: number): string {
  return vi.mocked(runContainerAgent).mock.calls[index][1].prompt;
}

let closeStdin: ReturnType<typeof vi.spyOn>;
let enqueueMessageCheck: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
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
