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

import { ASSISTANT_NAME, IDLE_TIMEOUT } from './config.js';
import {
  ContainerOutput,
  ContainerTurn,
  runContainerAgent,
} from './container-runner.js';
import {
  _initTestDatabase,
  deleteSession,
  getRouterState,
  setRegisteredGroup,
  setRouterState,
  setSession,
  storeChatMetadata,
  storeMessage,
} from './db.js';
import {
  _loadState,
  _pollNewMessages,
  _processGroupMessages,
  _recoverPendingMessages,
  _restoreStartupState,
  _schedulerDependencies,
  _setChannels,
  _setRegisteredGroups,
} from './index.js';
import { RESENT_PREFIX } from './router.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';

/**
 * The runner's own input-id readers, so the fake below reports each input by
 * the id the runner would read off what the host wrote, and the runner's own
 * stamp for an output once its guard switched attribution off: a field
 * renamed on either side fails here. Loaded by a path tsc does not follow,
 * since the runner is outside this package's rootDir.
 */
const RUNNER_IDS = '../container/agent-runner/src/turn-attribution.js';
const { ipcInputId, startupInputIds, TurnAttribution } = (await import(
  /* @vite-ignore */ RUNNER_IDS
)) as {
  ipcInputId(file: string, data: { ref?: unknown }): string;
  startupInputIds(input: { promptRef?: unknown }, drained: string[]): string[];
  TurnAttribution: new () => {
    push(text: string, inputs: string[]): string;
    turnStarted(): void;
    ack(uuid: string): void;
    result(subtype: string): ContainerTurn | undefined;
    stamp(output: ContainerOutput): ContainerOutput;
  };
};

/** The runner's record once its guard tripped: a success with no replay. */
const attributionOff = new TurnAttribution();
attributionOff.push('prompt', ['prompt']);
attributionOff.turnStarted();
attributionOff.result('success');

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
/** While set, clearing the typing indicator after a run waits for it. */
let typingHold: Promise<void> | null = null;
const channel: Channel = {
  name: 'test',
  connect: async () => {},
  sendMessage,
  isConnected: () => true,
  ownsJid: (jid) => jid === CHAT,
  disconnect: async () => {},
  setTyping: async (_jid, on) => {
    if (!on && typingHold) await typingHold;
  },
};
const { queue } = _schedulerDependencies();
/** The input dir of the group under test. */
function groupInputDir(group: RegisteredGroup): string {
  return path.join(dataDir, 'ipc', group.folder, 'input');
}
let inputDir = groupInputDir(MAIN_GROUP);

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
  /**
   * A result, from a runner that reports `turn`, one that does not (none
   * given), or one whose guard switched attribution off ('off').
   */
  result(text: string | null, turn?: ContainerTurn | 'off'): Promise<void>;
  /** The session-update marker the runner writes when a query ends. */
  marker(off?: 'off'): Promise<void>;
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

/** The runner's drain: every input file, by the id the runner reads off it. */
function drainFiles(): { id: string; text: string }[] {
  return inputFiles().map((file) => {
    const filePath = path.join(inputDir, file);
    const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    fs.unlinkSync(filePath);
    return { id: ipcInputId(file, data), text: data.text };
  });
}

/** How long container-runner waits for output callbacks after an exit. */
const SETTLE_BOUND = 120_000;

/**
 * Starts containers as container-runner runs them: their outputs' callbacks
 * in order, one at a time, and an exit that waits for them, but at most
 * SETTLE_BOUND.
 */
function runnerStarts(): void {
  vi.mocked(runContainerAgent).mockImplementation(
    async (_group, input, onProcess, onOutput) => {
      onProcess({} as never, `paperclaw-main-${containers.length + 1}`);
      const drained = drainFiles();
      let finish!: (output: ContainerOutput) => void;
      const done = new Promise<ContainerOutput>((resolve) => {
        finish = resolve;
      });
      let chain = Promise.resolve();
      const emit = (output: ContainerOutput): Promise<void> => {
        chain = chain.then(() => onOutput!(output));
        return chain;
      };
      const container: FakeContainer = {
        startIds: startupInputIds(
          input,
          drained.map((d) => d.id),
        ),
        prompt: [input.prompt, ...drained.map((d) => d.text)].join('\n'),
        drain: () => drainFiles().map((d) => d.id),
        result: (text, turn) => {
          const output: ContainerOutput = {
            status: 'success',
            result: text,
            newSessionId: 'session-new',
            ...(turn && turn !== 'off' ? { turn } : {}),
          };
          return emit(turn === 'off' ? attributionOff.stamp(output) : output);
        },
        marker: (off) => {
          const output: ContainerOutput = {
            status: 'success',
            result: null,
            newSessionId: 'session-new',
          };
          return emit(off ? attributionOff.stamp(output) : output);
        },
        exited: false,
        exit: async (status = 'success') => {
          container.exited = true;
          const output: ContainerOutput =
            status === 'success'
              ? { status, result: null, newSessionId: 'session-new' }
              : { status, result: null, error: 'exited with code 137' };
          const bound = setTimeout(() => finish(output), SETTLE_BOUND);
          void chain.then(() => {
            clearTimeout(bound);
            finish(output);
          });
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

/** Messages that arrive between two polls: the message loop sees them as one batch. */
async function usersSay(
  messages: [id: string, content: string, at: number][],
): Promise<void> {
  for (const [id, content, at] of messages) {
    storeMessage({
      id,
      chat_jid: CHAT,
      sender: 'user@s.whatsapp.net',
      sender_name: 'User',
      content,
      timestamp: new Date(at).toISOString(),
    });
  }
  _pollNewMessages();
  await vi.advanceTimersByTimeAsync(0);
}

/** The texts of the input files waiting for the container, oldest first. */
function waitingTexts(): string[] {
  return inputFiles().map(
    (file) =>
      JSON.parse(fs.readFileSync(path.join(inputDir, file), 'utf-8')).text,
  );
}

/** The message cursor as saved, which is what a restart would resume from. */
function cursor(): string | undefined {
  return JSON.parse(getRouterState('last_agent_timestamp') || '{}')[CHAT];
}

/**
 * The new process after a restart: state from the database only, as main()
 * starts, with the last process's containers stopped.
 */
function restart(): void {
  _restoreStartupState();
  _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
}

/** The router_state keys a message run writes. */
const RUN_STATE_KEYS = [
  'last_timestamp',
  'last_agent_timestamp',
  'answered_ahead',
  'turn_open_from',
  'turn_put_back',
  'usage_limit_refused_input',
  'held_command',
];

/** Messages stored while no process polls: start-up recovery finds them. */
function storedBeforeStart(
  messages: [id: string, content: string, at: number][],
): void {
  for (const [id, content, at] of messages) {
    storeMessage({
      id,
      chat_jid: CHAT,
      sender: 'user@s.whatsapp.net',
      sender_name: 'User',
      content,
      timestamp: new Date(at).toISOString(),
    });
  }
}

/**
 * What the process leaves behind if it dies now: its router_state and the
 * input files waiting. Returns what puts that back, for a test that has to
 * end the dead process's container first (the queue would pipe into it).
 */
function crashState(): () => void {
  const state = RUN_STATE_KEYS.map((key) => [key, getRouterState(key)]);
  const files = inputFiles().map((file) => [
    file,
    fs.readFileSync(path.join(inputDir, file), 'utf-8'),
  ]);
  return () => {
    for (const [key, value] of state) setRouterState(key!, value ?? '');
    fs.rmSync(inputDir, { recursive: true, force: true });
    fs.mkdirSync(inputDir, { recursive: true });
    for (const [file, text] of files) {
      fs.writeFileSync(path.join(inputDir, file), text);
    }
  };
}

/**
 * The process dies with `container` mid-run, and a new one starts: the
 * container is stopped (cleanupOrphans), state comes from the database, and
 * recovery runs.
 */
async function restartWhileRunning(container: FakeContainer): Promise<void> {
  const deadState = crashState();
  // The dead process starts nothing once its container is gone.
  queue.setProcessMessagesFn(async () => true);
  await container.exit('error');
  queue.setProcessMessagesFn(_processGroupMessages);
  deadState();
  restart();
  _recoverPendingMessages();
  await vi.advanceTimersByTimeAsync(0);
}

/** Per chat, where a restart puts its cursor back to. */
function openFrom(): Record<string, string> {
  return JSON.parse(getRouterState('turn_open_from') || '{}');
}

/** Per chat, the ranges past its cursor a turn already answered. */
function answeredAhead(): Record<string, [string, string][]> {
  return JSON.parse(getRouterState('answered_ahead') || '{}');
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

/**
 * The queue's retry of a run that put messages back: its `nth` retry in a
 * row, which waits 5 s, doubled for each one before it.
 */
async function containerOnRetry(nth = 1): Promise<FakeContainer> {
  const before = containers.length;
  await vi.advanceTimersByTimeAsync(5_000 * 2 ** (nth - 1));
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
  typingHold = null;
  _initTestDatabase();
  setSession('main', 'session-old');
  setRegisteredGroup(CHAT, MAIN_GROUP);
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

  it('reads the turn of a textless first result, before any text came', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // Answered through send_message alone: the container's first result.
    await a.result(null, { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
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

describe('a refusal with a prompt answered after the refused one', () => {
  it('holds only the refused prompt for the reset', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // Sent during m1's last API call: its own turn, after m1's.
    const m2 = await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(LIMIT, { inputs: [a.startIds[0]] });
    // Extra usage let m2's turn through, answered through send_message.
    await a.result(null, { inputs: [p2] });
    await a.exit();

    // Not dropped after a refusal: the reset's run still needs it.
    expect(answeredAhead()).toEqual({
      [CHAT]: [[expect.any(String), m2.timestamp]],
    });
    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    expect(cursor()).toBe(m2.timestamp);
    expect(answeredAhead()).toEqual({});
    await nothingRunsAgain();
  });

  it('holds a failed prompt and the refused one, not one answered between them', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(null, { inputs: [p2] });
    await userSays('m3', M3, NOW - 20_000);
    const [p3] = a.drain();
    await a.result(LIMIT, { inputs: [p3] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.result('Both answered.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });
});

describe('a message a refusal held, sent again after the reset', () => {
  // Measured (container/agent-runner/test/turn-attribution-probe.test.ts):
  // the refused prompt stays in the session, and the request that resumes it
  // carries it in the same user message as the copy sent again.
  it('is marked as sent again when the session it was refused in resumes', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(LIMIT, { inputs: [p2] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, `${RESENT_PREFIX}${M2}`)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: [b.startIds[0]] });
    await b.exit();
    // Answered: a later delivery is no retry.
    expect(getRouterState('usage_limit_refused_turn')).toBe('{}');
  });

  it('marks only the refused message, not one the window held', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(LIMIT, { inputs: [p2] });
    await a.exit();
    // Sent during the window: never refused, so no earlier copy exists.
    await userSays('m3', M3, NOW - 20_000);

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, `${RESENT_PREFIX}${M2}`)).toBe(1);
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(`${RESENT_PREFIX}${M3}`);
    await b.exit();
  });

  it('is not marked when the refused session is not the one the group keeps', async () => {
    // A new session whose first turn was refused: the host stores none, so
    // the next container starts another, with no copy in it.
    deleteSession('main');
    _loadState();
    _setRegisteredGroups({ [CHAT]: MAIN_GROUP });
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(LIMIT, { inputs: [a.startIds[0]] });
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(RESENT_PREFIX);
    await b.exit();
  });

  it('leaves a command bare, so the CLI still runs it', async () => {
    const command = '/probe-skill run it';
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', command, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(LIMIT, { inputs: [p2] });
    await a.exit();

    const b = await containerAfterReset();
    expect(b.prompt).toBe(command);
    await b.exit();
  });
});

describe('a container that exits with input no result answered', () => {
  it('has it delivered again, once', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain(); // taken, but no result ever answers it
    await a.exit();

    expect(cursor()).toBe(m1.timestamp);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('has it delivered again after an error, past an answer that went out', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    await a.exit('error');

    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: [b.startIds[0]] });
    await b.exit();
  });

  it('runs only the unanswered prompt again, not an answered follow-up after it', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // The CLI failed the turn before it acknowledged anything.
    await a.result(null, { inputs: [] });
    const m2 = await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();

    // The cursor goes back to m1, and m2's range is kept as answered.
    expect(cursor()).toBe('');
    expect(answeredAhead()).toEqual({
      [CHAT]: [[m1.timestamp, m2.timestamp]],
    });
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    // The delivery moves the cursor past the answered message too.
    expect(cursor()).toBe(m2.timestamp);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();

    // Behind the cursor now, so the range is dropped.
    expect(answeredAhead()).toEqual({});
    await nothingRunsAgain();
  });

  it('keeps answered ranges across a restart before the retry', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    const m2 = await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();

    // The service restarts before the queue's retry: recovery runs instead.
    restart();
    _recoverPendingMessages();
    await vi.advanceTimersByTimeAsync(0);
    const b = containers[1];
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    expect(cursor()).toBe(m2.timestamp);
    await nothingRunsAgain();
  });

  it('lets the message loop skip an answered range', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();

    // A new message before the retry starts the next container itself.
    const m3 = await userSays('m3', M3, NOW - 20_000);
    const b = containers[1];
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.result('Both answered.', { inputs: [b.startIds[0]] });
    await b.exit();
    expect(cursor()).toBe(m3.timestamp);
    expect(answeredAhead()).toEqual({});
    await nothingRunsAgain();
  });

  it('pipes a follow-up from past an answered range', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    const m2 = await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();
    const b = await containerOnRetry();

    await userSays('m3', M3, NOW - 20_000);
    const [p3] = b.drain();
    // Its range starts after m2, which no prompt here carries.
    expect(p3.startsWith(`${m2.timestamp}|`)).toBe(true);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.result('It covers the appendix.', { inputs: [p3] });
    await b.exit();
    expect(answeredAhead()).toEqual({});
    await nothingRunsAgain();
  });

  it('has the prompt of a turn that failed after the runner took it delivered again', async () => {
    // The turns as the runner's own record reports them: the CLI took m1,
    // then its stream broke, and m2's turn answers m2 alone, since the CLI
    // kept m1 in the history but did not send it again.
    const runner = new TurnAttribution();
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    const u1 = runner.push(a.prompt, a.startIds);
    runner.turnStarted();
    runner.ack(u1);
    await a.result(null, runner.result('error_during_execution'));
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    const u2 = runner.push(M2, [p2]);
    runner.turnStarted();
    runner.ack(u2);
    await a.result('It covers the experiments.', runner.result('success'));
    // Later questions in the same session, each answered by its own turn.
    for (const [id, text, at] of [
      ['m3', M3, NOW - 30_000],
      ['m4', M4, NOW - 20_000],
    ] as const) {
      await userSays(id, text, at);
      const [p] = a.drain();
      const u = runner.push(text, [p]);
      runner.turnStarted();
      runner.ack(u);
      await a.result('Answered.', runner.result('success'));
    }
    await a.exit();

    // Only the failed turn's input runs again.
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    for (const answered of [M2, M3, M4]) {
      expect(b.prompt).not.toContain(answered);
    }
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('puts it back once, not again when its retry answers it neither', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // The CLI fails the turn before it acknowledges anything, every time.
    await a.result(null, { inputs: [] });
    await a.exit();
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    await b.result(null, { inputs: [] });
    await b.exit();

    // Settled as before attribution: a clean exit moves on.
    expect(cursor()).toBe(m1.timestamp);
    await nothingRunsAgain();
    await userSays('m2', M2, Date.now());
    const c = containers[2];
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('It covers the experiments.', { inputs: [c.startIds[0]] });
    await c.exit();
  });

  it('puts a later prompt back after one was put back and answered', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await a.exit();
    const b = await containerOnRetry();
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    b.drain(); // taken, never answered
    await b.exit();

    const c = await containerOnRetry(2);
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('It covers the experiments.', { inputs: [c.startIds[0]] });
    await c.exit();
  });

  /** m1's turn fails in its container and again in the retry. */
  async function m1FailsTwice(): Promise<{
    m1: NewMessage;
    b: FakeContainer;
  }> {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await a.exit();
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    await b.result(null, { inputs: [] });
    return { m1, b };
  }

  it('gives up the prompt put back once, and puts back a newer one the retry took', async () => {
    const { m1, b } = await m1FailsTwice();
    await userSays('m2', M2, NOW - 30_000);
    b.drain(); // taken; the container stops before its turn ends
    await b.exit();

    // m2 was never put back, so it is not given up with m1.
    expect(cursor()).toBe(m1.timestamp);
    const c = await containerOnRetry(2);
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('It covers the experiments.', { inputs: [c.startIds[0]] });
    await c.exit();
    await nothingRunsAgain();
  });

  it('gives up the prompt put back once, and puts back a newer one the retry never drained', async () => {
    const { m1, b } = await m1FailsTwice();
    await userSays('m2', M2, NOW - 30_000); // never drained
    await b.exit();

    expect(cursor()).toBe(m1.timestamp);
    // The file goes, so the retry delivers m2 once.
    expect(inputFiles()).toEqual([]);
    const c = await containerOnRetry(2);
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('It covers the experiments.', { inputs: c.startIds });
    await c.exit();
    await nothingRunsAgain();
  });

  it('gives up the prompt put back once after an error too, and puts back a newer one', async () => {
    const { b } = await m1FailsTwice();
    await userSays('m2', M2, NOW - 30_000);
    b.drain();
    await b.exit('error');

    // Not a full rollback: m1 does not run a third time.
    const c = await containerOnRetry(2);
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('It covers the experiments.', { inputs: [c.startIds[0]] });
    await c.exit();
    await nothingRunsAgain();
    expect(containers.filter((c) => c.prompt.includes(M1))).toHaveLength(2);
  });

  it('puts back a failed prompt with a newer one taken after it, when neither went back before', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await userSays('m2', M2, NOW - 30_000);
    a.drain();
    await a.exit();

    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(timesIn(b.prompt, M2)).toBe(1);
    await b.result('Both answered.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('closes once idle after a failed turn, so its input goes back without waiting for the hard timeout', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    const closed = () => fs.existsSync(path.join(inputDir, '_close'));
    // The session marker is no result: it does not start the idle wait.
    await a.marker();
    await vi.advanceTimersByTimeAsync(IDLE_TIMEOUT);
    expect(closed()).toBe(false);
    // The turn failed: a textless result that answers nothing.
    await a.result(null, { inputs: [] });
    await vi.advanceTimersByTimeAsync(IDLE_TIMEOUT - 1);
    expect(closed()).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(closed()).toBe(true);
    await a.exit();

    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
  });

  it('puts nothing back when an output was still being delivered', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 50_000);
    const [p2] = a.drain();
    // The send of m2's answer hangs past the runner's wait, and m3's answer
    // waits behind it.
    let release!: () => void;
    sendMessage.mockImplementationOnce(
      () => new Promise<void>((resolve) => (release = resolve)),
    );
    void a.result('It covers the experiments.', { inputs: [p2] });
    await userSays('m3', M3, NOW - 40_000);
    const [p3] = a.drain();
    void a.result('It covers the appendix.', { inputs: [p3] });
    await a.exit();
    await vi.advanceTimersByTimeAsync(SETTLE_BOUND);
    release();

    // m3 was answered, only late: running it again would answer it twice.
    await nothingRunsAgain();
    const sent = sendMessage.mock.calls.map((c) => c[1]);
    expect(sent.filter((t) => t === 'It covers the appendix.')).toHaveLength(1);
  });

  it('puts back a follow-up it never drained, without waiting for the next message', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000); // never drained
    await a.exit();

    // The container is gone, so nothing drains the file any more: m2 is
    // pending again, and the file goes, so the retry delivers it once.
    expect(cursor()).toBe(m1.timestamp);
    expect(inputFiles()).toEqual([]);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: b.startIds });
    await b.exit();
    await nothingRunsAgain();
  });

  it('puts back a follow-up piped while it winds down after a clean exit', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    let release!: () => void;
    typingHold = new Promise<void>((resolve) => (release = resolve));
    await a.exit();
    // The queue still counts the container active, so m2 is piped.
    await userSays('m2', M2, NOW - 40_000);
    expect(inputFiles()).toHaveLength(1);
    release();
    await vi.advanceTimersByTimeAsync(0);

    expect(cursor()).toBe(m1.timestamp);
    expect(inputFiles()).toEqual([]);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: b.startIds });
    await b.exit();
  });

  it('puts back a follow-up it never drained when it exits on an error', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // Answered through send_message: no reply text goes out.
    await a.result('<internal>Sent the answer.</internal>', {
      inputs: [a.startIds[0]],
    });
    await userSays('m2', M2, NOW - 40_000); // never drained
    await a.exit('error');

    // No container is coming to drain the file: the retry gets m2 instead.
    expect(inputFiles()).toEqual([]);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: b.startIds });
    await b.exit();
    await nothingRunsAgain();
  });

  it('puts back a follow-up piped while it winds down after an error', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('<internal>Sent the answer.</internal>', {
      inputs: [a.startIds[0]],
    });
    let release!: () => void;
    typingHold = new Promise<void>((resolve) => (release = resolve));
    await a.exit('error');
    // The queue still counts the container active, so m2 is piped.
    await userSays('m2', M2, NOW - 40_000);
    expect(inputFiles()).toHaveLength(1);
    release();
    await vi.advanceTimersByTimeAsync(0);

    expect(inputFiles()).toEqual([]);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: b.startIds });
    await b.exit();
  });

  it('drops the file of a follow-up the put-back made pending', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain(); // taken, never answered
    await userSays('m3', M3, NOW - 20_000); // never drained
    await a.exit();

    expect(inputFiles()).toEqual([]);
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(timesIn(b.prompt, M3)).toBe(1);
    await b.result('Both answered.', { inputs: b.startIds });
    await b.exit();
  });

  it('leaves a restart nothing to put back once it gave a prompt up', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();
    const b = await containerOnRetry();
    await b.result(null, { inputs: [] }); // fails again: given up at exit
    await b.exit();
    await nothingRunsAgain();

    restart();
    _recoverPendingMessages();
    await nothingRunsAgain();
  });

  it('keeps the ranges a put-back kept when the retry is refused', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [] });
    await userSays('m2', M2, NOW - 40_000);
    const [p2] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p2] });
    await a.exit();
    const b = await containerOnRetry();
    expect(b.prompt).not.toContain(M2);
    await b.result(LIMIT, { inputs: [b.startIds[0]] });
    await b.exit();

    const c = await containerAfterReset();
    expect(timesIn(c.prompt, M1)).toBe(1);
    expect(c.prompt).not.toContain(M2);
  });
});

describe('an input file an earlier container left', () => {
  /**
   * m1's container falls back to the old rule, so its exit leaves m2's file,
   * never drained, for the next container; m3 starts that one, which drains
   * the file into its first prompt.
   */
  async function drainedLeftover(): Promise<{
    b: FakeContainer;
    m2: NewMessage;
    m3: NewMessage;
  }> {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', 'off');
    const m2 = await userSays('m2', M2, NOW - 50_000);
    await a.exit();
    expect(inputFiles()).toHaveLength(1);
    const m3 = await userSays('m3', M3, NOW - 40_000);
    const b = containers[1];
    expect(b.startIds).toHaveLength(2);
    expect(timesIn(b.prompt, M2)).toBe(1);
    return { b, m2, m3 };
  }

  it('has it delivered again when the turn that took it is refused', async () => {
    const { b, m3 } = await drainedLeftover();
    await b.result(LIMIT, { inputs: b.startIds });
    await b.exit();

    const c = await containerAfterReset();
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(timesIn(c.prompt, M3)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('Both answered.', { inputs: [c.startIds[0]] });
    await c.exit();
    expect(cursor()).toBe(m3.timestamp);
    await nothingRunsAgain();
  });

  it('has it delivered again when no result answers it', async () => {
    const { b } = await drainedLeftover();
    // The CLI failed the start-up turn before it acknowledged anything.
    await b.result(null, { inputs: [] });
    await b.exit();

    const c = await containerOnRetry();
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(timesIn(c.prompt, M3)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('Both answered.', { inputs: [c.startIds[0]] });
    await c.exit();
    await nothingRunsAgain();
  });

  it('has it delivered again when the container dies before any result', async () => {
    const { b } = await drainedLeftover();
    await b.exit('error');

    const c = await containerOnRetry();
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(timesIn(c.prompt, M3)).toBe(1);
    expect(c.prompt).not.toContain(M1);
    await c.result('Both answered.', { inputs: [c.startIds[0]] });
    await c.exit();
    await nothingRunsAgain();
  });

  it('is settled with the start-up prompt it was drained into', async () => {
    const { b, m3 } = await drainedLeftover();
    await b.result('Both answered.', { inputs: b.startIds });
    await b.exit();
    expect(cursor()).toBe(m3.timestamp);
    await nothingRunsAgain();
  });

  it('is settled with the start-up prompt by a runner that reports no turn', async () => {
    const { b } = await drainedLeftover();
    await b.result('Both answered.');
    await userSays('m4', M4, NOW - 30_000);
    b.drain();
    await b.result(LIMIT);
    await b.exit();

    // The old rule: the answer settled the start-up push, leftover and all.
    const c = await containerAfterReset();
    expect(timesIn(c.prompt, M4)).toBe(1);
    expect(c.prompt).not.toContain(M3);
    expect(c.prompt).not.toContain(M2);
    await c.exit();
  });

  it('settles nothing else when the next container names it', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // With attribution off, an exit leaves a file it never drained to the
    // next container, as before attribution.
    await a.result('It describes the method.', 'off');
    await userSays('m2', M2, NOW - 50_000); // left for the next container
    await a.exit();
    expect(inputFiles()).toHaveLength(1);

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

describe('a router_state value of the wrong shape', () => {
  // Only a hand edit or a format this process does not know writes one. It
  // reads as missing, instead of throwing at every start-up.
  it.each([
    ['answered_ahead', 'null'],
    ['answered_ahead', `{"${CHAT}": {}}`],
    ['answered_ahead', `{"${CHAT}": [["a"]]}`],
    ['turn_open_from', 'null'],
    ['turn_open_from', `{"${CHAT}": 5}`],
    ['held_command', '[]'],
    ['held_command', `{"${CHAT}": null}`],
    ['usage_limit_refused_turn', 'null'],
    ['usage_limit_refused_turn', `{"${CHAT}": {"session": "session-old"}}`],
    ['turn_put_back', '"main"'],
    ['usage_limit_refused_input', `{"${CHAT}": [null]}`],
  ])('starts with %s = %s as if it were missing', async (key, value) => {
    storedBeforeStart([['m1', M1, NOW - 60_000]]);
    setRouterState(key, value);
    restart();
    _recoverPendingMessages();
    await vi.advanceTimersByTimeAsync(0);

    const a = containers[0];
    expect(timesIn(a.prompt, M1)).toBe(1);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await a.exit();
    await nothingRunsAgain();
  });
});

describe('a restart while a container runs', () => {
  it('delivers again a follow-up the container took and no turn answered', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain(); // taken; its turn is running when the process dies
    expect(openFrom()).toEqual({ [CHAT]: m1.timestamp });
    await restartWhileRunning(a);

    const b = containers[1];
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M1);
    expect(openFrom()).toEqual({ [CHAT]: m1.timestamp });
    await b.result('It covers the experiments.', { inputs: [b.startIds[0]] });
    await b.exit();
    expect(openFrom()).toEqual({});
    await nothingRunsAgain();
  });

  it('delivers again the first prompt when its turn is still running', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    expect(openFrom()).toEqual({ [CHAT]: '' });
    await restartWhileRunning(a);

    const b = containers[1];
    expect(timesIn(b.prompt, M1)).toBe(1);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('keeps a follow-up answered while an older one is open', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    await userSays('m3', M3, NOW - 30_000);
    const [p3] = a.drain();
    // m2's turn failed, and m3's own turn answered it.
    await a.result(null, { inputs: [] });
    await a.result('It covers the appendix.', { inputs: [p3] });
    await restartWhileRunning(a);

    const b = containers[1];
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(b.prompt).not.toContain(M3);
    expect(b.prompt).not.toContain(M1);
    await b.result('It covers the experiments.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('delivers once a follow-up whose file the container never drained', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    await userSays('m3', M3, NOW - 30_000); // still in its file
    await restartWhileRunning(a);

    // Its messages are pending again, so the file goes.
    const b = containers[1];
    expect(timesIn(b.prompt, M2)).toBe(1);
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(inputFiles()).toEqual([]);
    await b.result('Both answered.', { inputs: b.startIds });
    await b.exit();
    await nothingRunsAgain();
  });

  it('puts nothing back once the runner turned attribution off', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', 'off');
    const m2 = await userSays('m2', M2, NOW - 40_000);
    a.drain();
    expect(openFrom()).toEqual({});
    await restartWhileRunning(a);

    // As before attribution: the cursor stays where it was saved.
    expect(cursor()).toBe(m2.timestamp);
    await nothingRunsAgain();
  });

  it('puts it back once: start-up forgets where it put back to', async () => {
    const m1 = await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    const deadState = crashState();
    await a.exit('error');
    deadState();
    restart();

    expect(cursor()).toBe(m1.timestamp);
    // A second restart before any container runs puts back nothing more.
    expect(openFrom()).toEqual({});
  });
});

describe('a batch whose newest message is a slash command', () => {
  // The router sends such a batch as the command alone.
  const CMD = '/probe-skill run it';

  describe('in a group that needs the trigger', () => {
    const TEAM: RegisteredGroup = {
      name: 'Team',
      folder: 'team',
      trigger: `@${ASSISTANT_NAME}`,
      added_at: '2026-10-01T00:00:00.000Z',
      requiresTrigger: true,
    };
    beforeEach(() => {
      _setRegisteredGroups({ [CHAT]: TEAM });
      inputDir = groupInputDir(TEAM);
    });
    afterEach(() => {
      inputDir = groupInputDir(MAIN_GROUP);
    });

    it('does not make a prompt of chatter before the command', async () => {
      // Context, not a request: sent alone it would draw a reply.
      await usersSay([
        ['m1', 'lunch at noon?', NOW - 60_000],
        ['m2', `@${ASSISTANT_NAME} ${CMD}`, NOW - 50_000],
      ]);
      const a = containers[0];
      expect(a.prompt).toBe(CMD);
      await a.result('The skill ran.', { inputs: [a.startIds[0]] });
      await a.exit();
      await nothingRunsAgain();
    });

    it('runs a triggered question before the command', async () => {
      await usersSay([
        ['m1', `@${ASSISTANT_NAME} ${M1}`, NOW - 60_000],
        ['m2', `@${ASSISTANT_NAME} ${CMD}`, NOW - 50_000],
      ]);
      const a = containers[0];
      expect(timesIn(a.prompt, M1)).toBe(1);
      expect(a.prompt).not.toContain(CMD);
      await a.result('It describes the method.', { inputs: [a.startIds[0]] });
      expect(waitingTexts()).toEqual([CMD]);
      const [command] = a.drain();
      await a.result('The skill ran.', { inputs: [command] });
      await a.exit();
      await nothingRunsAgain();
    });

    it('runs a command sent without the trigger after a triggered question', async () => {
      // On its own it would not start the agent, but the batch it ended did.
      await usersSay([
        ['m1', `@${ASSISTANT_NAME} ${M1}`, NOW - 60_000],
        ['m2', CMD, NOW - 50_000],
      ]);
      const a = containers[0];
      expect(timesIn(a.prompt, M1)).toBe(1);
      expect(a.prompt).not.toContain(CMD);
      await userSays('m3', 'lunch at noon?', NOW - 45_000);
      await a.result('It describes the method.', { inputs: [a.startIds[0]] });
      expect(waitingTexts()).toEqual([CMD]);
      const [command] = a.drain();
      await a.result('The skill ran.', { inputs: [command] });
      // Chatter after the command is still context, not a request.
      expect(waitingTexts()).toEqual([]);
      await a.exit();
      await nothingRunsAgain();
    });

    it('runs such a command after a restart stopped it in its file', async () => {
      setRegisteredGroup(CHAT, TEAM);
      await usersSay([
        ['m1', `@${ASSISTANT_NAME} ${M1}`, NOW - 60_000],
        ['m2', CMD, NOW - 50_000],
      ]);
      const a = containers[0];
      await a.result('It describes the method.', { inputs: [a.startIds[0]] });
      expect(waitingTexts()).toEqual([CMD]); // never drained
      const deadState = crashState();
      await a.exit('error');
      deadState();
      _restoreStartupState();
      _recoverPendingMessages();
      await vi.advanceTimersByTimeAsync(0);

      const b = containers[1];
      expect(b.prompt).toBe(CMD);
      await b.result('The skill ran.', { inputs: [b.startIds[0]] });
      await b.exit();
      await nothingRunsAgain();
    });
  });

  it('goes as the command alone when it shares the second of the message before it', async () => {
    // WhatsApp stamps whole seconds, and the cursor cannot stop between two
    // messages that share a timestamp: the batch goes whole, as before.
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 60_000],
    ]);
    const a = containers[0];
    expect(a.prompt).toBe(CMD);
    await a.result('The skill ran.', { inputs: [a.startIds[0]] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('goes as the command alone at start-up too when it shares that second', async () => {
    storedBeforeStart([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 60_000],
    ]);
    restart();
    _recoverPendingMessages();
    await vi.advanceTimersByTimeAsync(0);

    const a = containers[0];
    expect(a.prompt).toBe(CMD);
    await a.result('The skill ran.', { inputs: [a.startIds[0]] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('runs the messages before the second a command shares with the one before it', async () => {
    await usersSay([
      ['m0', M3, NOW - 70_000],
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 60_000],
    ]);
    const a = containers[0];
    // The cursor can stop before that second, not inside it.
    expect(timesIn(a.prompt, M3)).toBe(1);
    expect(a.prompt).not.toContain(M1);
    expect(a.prompt).not.toContain(CMD);
    await a.result('It covers the appendix.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('runs a message that waited behind a command before a later batch ending in a command that shares a second', async () => {
    const SECOND = '/probe-skill second';
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    // Sent on its own while the question's turn runs: it waits.
    await userSays('m3', M2, NOW - 40_000);
    // Then a question and a command typed within one second.
    await usersSay([
      ['m4', M3, NOW - 30_000],
      ['m5', SECOND, NOW - 30_000],
    ]);
    expect(waitingTexts()).toEqual([]);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    expect(later).not.toContain(M3);
    expect(later).not.toContain(SECOND);
    const [p3] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p3] });
    // The command goes alone, without the question in its second, as before.
    expect(waitingTexts()).toEqual([SECOND]);
    const [second] = a.drain();
    await a.result('The second skill ran.', { inputs: [second] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('keeps a held command bare when a message stored after the poll shares its second', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    // Stored after that poll, in the command's second: the loop never sees
    // it, and the cursor cannot stop between the two.
    await userSays('m3', 'thanks', NOW - 50_000);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('runs a refused command alone after the reset, ahead of a message sent in the window', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', CMD, NOW - 40_000);
    const [p2] = a.drain();
    await a.result(LIMIT, { inputs: [p2] });
    await a.exit();
    await userSays('m3', 'did it run?', NOW - 20_000);

    const b = await containerAfterReset();
    expect(b.prompt).toBe(CMD);
    await b.result('The skill ran.', { inputs: [b.startIds[0]] });
    const [later] = waitingTexts();
    expect(timesIn(later, 'did it run?')).toBe(1);
    expect(later).not.toContain(CMD);
    const [p3] = b.drain();
    await b.result('It did.', { inputs: [p3] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs a refused command that started its container alone after the reset', async () => {
    await userSays('m1', CMD, NOW - 60_000);
    const a = containers[0];
    expect(a.prompt).toBe(CMD);
    await a.result(LIMIT, { inputs: [a.startIds[0]] });
    await a.exit();
    await userSays('m2', 'did it run?', NOW - 20_000);

    const b = await containerAfterReset();
    expect(b.prompt).toBe(CMD);
    await b.result('The skill ran.', { inputs: [b.startIds[0]] });
    const [later] = waitingTexts();
    expect(timesIn(later, 'did it run?')).toBe(1);
    const [p2] = b.drain();
    await b.result('It did.', { inputs: [p2] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs a refused command an earlier container left in its file alone after the reset', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', 'off');
    await userSays('m2', CMD, NOW - 50_000);
    await a.exit();
    expect(waitingTexts()).toEqual([CMD]);
    await userSays('m3', M2, NOW - 40_000);
    const b = containers[1];
    expect(b.startIds).toHaveLength(2);
    await b.result(LIMIT, { inputs: b.startIds });
    await b.exit();

    const c = await containerAfterReset();
    expect(c.prompt).toBe(CMD);
    await c.result('The skill ran.', { inputs: [c.startIds[0]] });
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    const [p3] = c.drain();
    await c.result('It covers the experiments.', { inputs: [p3] });
    await c.exit();
    await nothingRunsAgain();
  });

  it('runs a command put back alone ahead of a message put back after it', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    await userSays('m2', CMD, NOW - 40_000);
    a.drain(); // taken, and its turn never ends
    await userSays('m3', 'did it run?', NOW - 20_000);
    a.drain();
    await a.exit('error');

    const b = await containerOnRetry();
    expect(b.prompt).toBe(CMD);
    await b.result('The skill ran.', { inputs: [b.startIds[0]] });
    const [later] = waitingTexts();
    expect(timesIn(later, 'did it run?')).toBe(1);
    expect(later).not.toContain(CMD);
    const [p3] = b.drain();
    await b.result('It did.', { inputs: [p3] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs the newer of two held commands that share a second', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', '/probe-skill first', NOW - 50_000],
      ['m3', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    expect(a.prompt).not.toContain('/probe-skill');
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('keeps the command a prompt of its own when a message comes before the question is answered', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    await userSays('m3', M2, NOW - 40_000);
    // Nothing goes past the command while it waits.
    expect(waitingTexts()).toEqual([]);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    expect(later).not.toContain(CMD);
    const [p3] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p3] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('keeps the command a prompt of its own across a restart, with a message after it', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    await userSays('m3', M2, NOW - 40_000);
    await restartWhileRunning(a);

    const b = containers[1];
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(CMD);
    expect(b.prompt).not.toContain(M2);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = b.drain();
    await b.result('The skill ran.', { inputs: [command] });
    const [p3] = b.drain();
    await b.result('It covers the experiments.', { inputs: [p3] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('waits for a question piped with it, not only for the turn running then', async () => {
    await userSays('m0', M3, NOW - 70_000);
    const a = containers[0];
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    expect(waitingTexts()).toHaveLength(1);
    expect(waitingTexts()[0]).not.toContain(CMD);
    const [p1] = a.drain();
    await a.result('It covers the appendix.', { inputs: [a.startIds[0]] });
    // The question is still open: the command would be merged into its turn.
    expect(waitingTexts()).toEqual([]);
    await a.result('It describes the method.', { inputs: [p1] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    await a.exit();
    await nothingRunsAgain();
  });

  it('runs the messages before it first, then the command as its own prompt', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    expect(timesIn(a.prompt, M1)).toBe(1);
    expect(a.prompt).not.toContain(CMD);
    // Piped now, it could be merged into the question's turn as plain text.
    expect(waitingTexts()).toEqual([]);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    await a.exit();
    // Passed by the cursor, so the run's end forgets it.
    expect(JSON.parse(getRouterState('held_command') || '{}')).toEqual({});
    await nothingRunsAgain();
  });

  it('runs both a question and a command that a put-back made pending', async () => {
    await userSays('m0', M3, NOW - 70_000);
    const a = containers[0];
    await a.result('It covers the appendix.', { inputs: [a.startIds[0]] });
    await userSays('m1', M1, NOW - 60_000);
    // Alone in its batch, so piped as the command, as before.
    await userSays('m2', CMD, NOW - 50_000);
    expect(waitingTexts()).toHaveLength(2);
    expect(waitingTexts()).toContain(CMD);
    a.drain();
    await a.exit('error'); // stopped before either turn ended

    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(CMD);
    expect(b.prompt).not.toContain(M3);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = b.drain();
    await b.result('The skill ran.', { inputs: [command] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs the command in the next container when this one is closing', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    // Told to close, as for a scheduled task waiting for the group.
    queue.closeStdin(CHAT);
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    expect(waitingTexts()).toEqual([]);
    await a.exit();
    await vi.advanceTimersByTimeAsync(0);

    const b = containers[1];
    expect(b.prompt).toBe(CMD);
    await b.result('The skill ran.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs a refused question and its command once an answer closes the window before the exit', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    await a.result(LIMIT, { inputs: [a.startIds[0]] });
    // A turn the CLI started itself gets through: the window closes while
    // the refused container is still closing.
    await a.result('Background work finished.', {
      inputs: [],
      internal: true,
    });
    await a.exit('error');
    await vi.advanceTimersByTimeAsync(0);

    const b = containers[1];
    expect(b).toBeDefined();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(CMD);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = b.drain();
    await b.result('The skill ran.', { inputs: [command] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs the command after the turn ahead of it failed, without waiting for the idle close', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    // The question's turn fails after the CLI took it: it answers nothing,
    // and no later turn of this container answers the question.
    await a.result(null, { inputs: [] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    // Nothing waits behind the command any more.
    await userSays('m3', M2, NOW - 40_000);
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    const [p3] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p3] });
    await a.exit();

    // Only the failed question goes back.
    const b = await containerOnRetry();
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(CMD);
    expect(b.prompt).not.toContain(M2);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs a message that waited behind the command after the turn ahead of it failed', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    await userSays('m3', M2, NOW - 40_000);
    expect(waitingTexts()).toEqual([]);
    await a.result(null, { inputs: [] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = a.drain();
    await a.result('The skill ran.', { inputs: [command] });
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    expect(later).not.toContain(CMD);
    const [p3] = a.drain();
    await a.result('It covers the experiments.', { inputs: [p3] });
    await a.exit();

    // The message marked the group for its next container, so that one
    // need not wait for the retry.
    await vi.advanceTimersByTimeAsync(5_000);
    expect(containers).toHaveLength(2);
    const b = containers[1];
    expect(timesIn(b.prompt, M1)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    await b.result('It describes the method.', { inputs: [b.startIds[0]] });
    await b.exit();
    await nothingRunsAgain();
  });

  it('runs the command after a failed start-up turn that took a file an earlier container left', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', 'off');
    await userSays('m2', M2, NOW - 55_000);
    await a.exit();
    expect(inputFiles()).toHaveLength(1);
    await usersSay([
      ['m3', M3, NOW - 50_000],
      ['m4', CMD, NOW - 40_000],
    ]);
    const b = containers[1];
    expect(b.startIds).toHaveLength(2);
    expect(b.prompt).not.toContain(CMD);
    // One push, the leftover with the start-up prompt, and its turn failed.
    await b.result(null, { inputs: [] });
    expect(waitingTexts()).toEqual([CMD]);
    const [command] = b.drain();
    await b.result('The skill ran.', { inputs: [command] });
    await b.exit();

    const c = await containerOnRetry();
    expect(timesIn(c.prompt, M2)).toBe(1);
    expect(timesIn(c.prompt, M3)).toBe(1);
    expect(c.prompt).not.toContain(CMD);
    await c.result('Both answered.', { inputs: [c.startIds[0]] });
    await c.exit();
    await nothingRunsAgain();
  });

  it('runs the command after a turn of a runner that reports no turn, with a follow-up merged into it', async () => {
    await userSays('m0', M3, NOW - 70_000);
    const a = containers[0];
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    expect(waitingTexts()).toHaveLength(1);
    a.drain(); // merged into the running turn
    await userSays('m3', M2, NOW - 40_000);
    expect(waitingTexts()).toEqual([]);
    // One result for both, without `turn`: the question never settles.
    await a.result('Appendix and section 2 answered together.');
    expect(waitingTexts()).toEqual([CMD]);
    a.drain();
    await a.result('The skill ran.');
    const [later] = waitingTexts();
    expect(timesIn(later, M2)).toBe(1);
    a.drain();
    await a.result('It covers the experiments.');
    await a.exit();
    await nothingRunsAgain();
  });

  it('runs the command after a textless result from a runner that reports no turn', async () => {
    await usersSay([
      ['m1', M1, NOW - 60_000],
      ['m2', CMD, NOW - 50_000],
    ]);
    const a = containers[0];
    // Answered only through send_message: no text and no `turn`.
    await a.result(null);
    expect(waitingTexts()).toEqual([CMD]);
    a.drain();
    await a.result('The skill ran.');
    await a.exit();
    await nothingRunsAgain();
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

  it('once the runner says attribution is off on a textless result, puts nothing back', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    // Answered through send_message alone, as is m2 below.
    await a.result(null, { inputs: [a.startIds[0]] });
    const m2 = await userSays('m2', M2, NOW - 40_000);
    a.drain();
    // Replay broke: the guard trips on this textless result. Without the
    // runner saying so, it would look like the session marker.
    await a.result(null, 'off');
    await a.marker('off');
    await a.exit();

    // m2 was answered: the exit does what it did before attribution.
    expect(cursor()).toBe(m2.timestamp);
    await nothingRunsAgain();
  });

  it('once the runner says attribution is off, an error exit after a reply puts nothing back', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result('It describes the method.', { inputs: [a.startIds[0]] });
    const m2 = await userSays('m2', M2, NOW - 40_000);
    a.drain();
    await a.result(null, 'off');
    await a.exit('error');

    // A reply went out, so no rollback, as before attribution.
    expect(cursor()).toBe(m2.timestamp);
    await nothingRunsAgain();
  });

  it('once the runner says attribution is off, settles the rest the old way', async () => {
    await userSays('m1', M1, NOW - 60_000);
    const a = containers[0];
    await a.result(null, { inputs: [a.startIds[0]] });
    await userSays('m2', M2, NOW - 40_000);
    a.drain();
    // Textless: the old rule settles nothing on it.
    await a.result(null, 'off');
    await userSays('m3', M3, NOW - 20_000);
    a.drain();
    // The oldest open prompt (m2) takes it, whichever it answered.
    await a.result('It covers the experiments.', 'off');
    await a.result(LIMIT, 'off');
    await a.exit();

    const b = await containerAfterReset();
    expect(timesIn(b.prompt, M3)).toBe(1);
    expect(b.prompt).not.toContain(M2);
    expect(b.prompt).not.toContain(M1);
    await b.exit();
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
