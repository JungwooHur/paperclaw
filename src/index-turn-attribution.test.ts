import fs from 'fs';
import path from 'path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The group queue writes follow-ups and the close sentinel under DATA_DIR;
// keep them out of the checkout's data/.
const { dataDir } = vi.hoisted(() => ({
  dataDir: `${process.env.TMPDIR || '/tmp'}/turn-attribution-data-${process.pid}`,
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
vi.mock('./container-runtime.js', () => ({
  cleanupOrphans: vi.fn(),
  ensureContainerRuntimeRunning: vi.fn(),
}));
vi.mock('./qa-followup.js', () => ({
  qaFollowupCommand: vi.fn(),
  worthFilingCheck: () => false,
}));

import {
  ContainerOutput,
  ContainerTurn,
  runContainerAgent,
} from './container-runner.js';
import {
  _initTestDatabase,
  getRouterState,
  setSession,
  storeChatMetadata,
  storeMessage,
} from './db.js';
import {
  _loadState,
  _pollNewMessages,
  _processGroupMessages,
  _schedulerDependencies,
  _setChannels,
  _setRegisteredGroups,
} from './index.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';

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
const M1 = 'what is section 2 about?';
const M2 = 'and section 3?';
const M3 = 'and the appendix?';
const M4 = 'and the related work?';

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
const { queue } = _schedulerDependencies();
const inputDir = path.join(dataDir, 'ipc', MAIN_GROUP.folder, 'input');

/**
 * A message container as the runner behaves: it drains the input files at
 * start-up and while it runs, by the ids the runner reports them by, and its
 * results carry whatever `turn` the test gives them.
 */
interface FakeContainer {
  /** Its start-up prompt's ids: `promptRef`, then the files it drained. */
  startIds: string[];
  /** Its start-up prompt, with the files it drained. */
  prompt: string;
  /** Takes the files piped since, as the runner's poll does; their ids. */
  drain(): string[];
  /** A result, from a runner that reports `turn` or (none given) one that does not. */
  result(text: string | null, turn?: ContainerTurn): Promise<void>;
  /** The session-update marker the runner writes when a query ends. */
  marker(): Promise<void>;
  exit(status?: 'success' | 'error'): Promise<void>;
  exited: boolean;
}
let containers: FakeContainer[];

/** Input files waiting in the group's input dir, oldest first. */
function inputFiles(): string[] {
  if (!fs.existsSync(inputDir)) return [];
  return fs
    .readdirSync(inputDir)
    .filter((f) => f.endsWith('.json'))
    .sort();
}

/** The runner's drain: every input file, by its `ref` or else its name. */
function drainFiles(): { id: string; text: string }[] {
  return inputFiles().map((file) => {
    const filePath = path.join(inputDir, file);
    const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    fs.unlinkSync(filePath);
    return {
      id: typeof data.ref === 'string' ? data.ref : file,
      text: data.text,
    };
  });
}

function runnerStarts(): void {
  vi.mocked(runContainerAgent).mockImplementation(
    async (_group, input, onProcess, onOutput) => {
      onProcess({} as never, `paperclaw-main-${containers.length + 1}`);
      const drained = drainFiles();
      let finish!: (output: ContainerOutput) => void;
      const done = new Promise<ContainerOutput>((resolve) => {
        finish = resolve;
      });
      const emit = (output: ContainerOutput) => onOutput!(output);
      const container: FakeContainer = {
        startIds: [input.promptRef ?? 'stdin', ...drained.map((d) => d.id)],
        prompt: [input.prompt, ...drained.map((d) => d.text)].join('\n'),
        drain: () => drainFiles().map((d) => d.id),
        result: (text, turn) =>
          emit({
            status: 'success',
            result: text,
            newSessionId: 'session-new',
            ...(turn ? { turn } : {}),
          }),
        marker: () =>
          emit({
            status: 'success',
            result: null,
            newSessionId: 'session-new',
          }),
        exited: false,
        exit: async (status = 'success') => {
          container.exited = true;
          finish(
            status === 'success'
              ? { status, result: null, newSessionId: 'session-new' }
              : { status, result: null, error: 'exited with code 137' },
          );
          await vi.advanceTimersByTimeAsync(0);
        },
      };
      containers.push(container);
      return done;
    },
  );
}

/**
 * A user message, handed on the way the message loop does: piped into the
 * running container, or starting one.
 */
async function userSays(
  id: string,
  content: string,
  at: number,
): Promise<NewMessage> {
  const message: NewMessage = {
    id,
    chat_jid: CHAT,
    sender: 'user@s.whatsapp.net',
    sender_name: 'User',
    content,
    timestamp: new Date(at).toISOString(),
  };
  storeMessage(message);
  _pollNewMessages();
  await vi.advanceTimersByTimeAsync(0);
  return message;
}

/** The message cursor as saved, which is what a restart would resume from. */
function cursor(): string | undefined {
  return JSON.parse(getRouterState('last_agent_timestamp') || '{}')[CHAT];
}

function timesIn(text: string, part: string): number {
  return text.split(part).length - 1;
}

/** Recovery after the reset starts a container for what the limit held. */
async function containerAfterReset(): Promise<FakeContainer> {
  const before = containers.length;
  await vi.advanceTimersByTimeAsync(RESET + 5 * 60_000 - Date.now());
  expect(containers).toHaveLength(before + 1);
  return containers[before];
}

/** No container starts for a while: nothing was put back to run again. */
async function nothingRunsAgain(): Promise<void> {
  const before = containers.length;
  await vi.advanceTimersByTimeAsync(10 * 60_000);
  expect(containers).toHaveLength(before);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  fs.rmSync(dataDir, { recursive: true, force: true });
  containers = [];
  _initTestDatabase();
  setSession('main', 'session-old');
  _loadState();
  _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
  _setChannels([channel]);
  storeChatMetadata(CHAT, new Date(NOW).toISOString(), 'Main', 'test', true);
  sendMessage.mockClear();
  vi.mocked(runContainerAgent).mockReset();
  runnerStarts();
  queue.setProcessMessagesFn(_processGroupMessages);
});

afterEach(async () => {
  // The queue outlives a test: a container still up would keep the group
  // busy for the next one.
  queue.setProcessMessagesFn(async () => true);
  for (const container of containers) {
    if (!container.exited) await container.exit();
  }
  vi.restoreAllMocks();
  vi.useRealTimers();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

describe('a result that names the inputs it answered', () => {
  it('settles nothing when only the runner started its turn', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    // The background-task nudge's turn: a reply, but to no message.
    await a.result('The translation is still running.', {
      inputs: [],
      internal: true,
    });
    // m2's own turn is the one the limit refuses, so m2 goes back.
    await a.result(LIMIT, { inputs: [p2] });
    await a.exit();

    expect(sendMessage.mock.calls.map((c) => c[1])).toContain(
      'The translation is still running.',
    );
    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.exit();
  });

  it('settles a follow-up merged into the running turn with its prompt', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    const m2 = await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('Section 2 is the method, section 3 the experiments.', {
      inputs: [a.startIds[0], p2],
    });
    await a.marker();
    await a.exit();

    expect(cursor()).toBe(m2.timestamp);
    await nothingRunsAgain();
  });

  it('settles a follow-up that became its own turn after the one before it', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // Taken during the first turn's last API call: it is the next turn's.
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await a.result('It covers the experiments.', { inputs: [p2] });
    await userSays('m3', M3, NOW - 20_000);
    const [p3] = a.drain();
    await a.result(LIMIT, { inputs: [p3] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    expect(b.prompt).not.toContain(M1);
    await b.exit();
  });

  it('settles on a textless result, and not on the session marker', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    // Answered through send_message alone: the result has no text.
    await a.result(null, { inputs: [p2] });
    await userSays('m3', M3, NOW - 20_000);
    const [p3] = a.drain();
    await a.marker();
    await a.result(LIMIT, { inputs: [p3] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.exit();
  });

  it('puts back only what no turn answered when a refusal follows a merged follow-up', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('Section 2 is the method, section 3 the experiments.', {
      inputs: [a.startIds[0], p2],
    });
    await userSays('m3', M3, NOW - 20_000);
    const [p3] = a.drain();
    await a.result(LIMIT, { inputs: [p3] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    expect(b.prompt).not.toContain(M1);
    await b.exit();
  });
});

describe('an input file an earlier container left', () => {
  it('settles nothing else when the next container names it', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 50_000); // left for the next container
    await a.exit();

    await userSays('m3', M3, NOW - 40_000);
    const b = containers[1];
    const [p3, leftover] = b.startIds;
    expect(leftover).toBeDefined();
    // m4 waits for the next turn while the start-up prompt is answered.
    await userSays('m4', M4, NOW - 30_000);
    const [p4] = b.drain();
    await b.result('Section 3 and the appendix, answered.', {
      inputs: [p3, leftover],
    });
    await b.result(LIMIT, { inputs: [p4] });
    await b.exit();

    const c = await containerAfterReset();
    expect(timesIn(c.prompt, M4)).toBe(1);
    expect(c.prompt).not.toContain(M3);
    expect(c.prompt).not.toContain(M2);
    await c.exit();
  });
});

describe('a container whose results carry no turn', () => {
  it('from a runner that predates it, settles as before and puts nothing back', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.');
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    // The oldest open prompt takes each result, whichever it answered.
    await a.result('The translation is still running.');
    const m3 = await userSays('m3', M3, NOW - 20_000);
    a.drain();
    await a.exit();

    expect(cursor()).toBe(m3.timestamp);
    await nothingRunsAgain();
  });

  it('once the runner turned attribution off, falls back for the rest', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    // The runner's guard switched attribution off: no turn from here on.
    await a.result('It covers the experiments.');
    const m3 = await userSays('m3', M3, NOW - 20_000);
    a.drain();
    await a.exit();

    // Not exact to the end, so the exit puts nothing back, as before.
    expect(cursor()).toBe(m3.timestamp);
    await nothingRunsAgain();
  });

  it('once the runner turned attribution off, a refusal puts back what the fallback left', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    await a.result('It covers the experiments.');
    await userSays('m3', M3, NOW - 20_000);
    a.drain();
    await a.result(LIMIT);
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.exit();
  });
});
