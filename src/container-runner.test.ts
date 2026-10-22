import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { EventEmitter } from 'events';
import { PassThrough } from 'stream';

// Sentinel markers must match container-runner.ts
const OUTPUT_START_MARKER = '---PAPERCLAW_OUTPUT_START---';
const OUTPUT_END_MARKER = '---PAPERCLAW_OUTPUT_END---';

// Mock config
vi.mock('./config.js', () => ({
  AGENT_MODEL: 'model-under-test',
  AGENT_MODEL_ALIASES: {
    SONNET: 'sonnet-under-test',
    HAIKU: 'haiku-under-test',
  },
  CONTAINER_IMAGE: 'paperclaw-agent:latest',
  CONTAINER_MAX_OUTPUT_SIZE: 10485760,
  CONTAINER_TIMEOUT: 1800000, // 30min
  DATA_DIR: '/tmp/paperclaw-test-data',
  GROUPS_DIR: '/tmp/paperclaw-test-groups',
  IDLE_TIMEOUT: 1800000, // 30min
  TIMEZONE: 'America/Los_Angeles',
}));

// Mock logger
vi.mock('./logger.js', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

// Mock fs
vi.mock('fs', async () => {
  const actual = await vi.importActual<typeof import('fs')>('fs');
  return {
    ...actual,
    default: {
      ...actual,
      existsSync: vi.fn(() => false),
      mkdirSync: vi.fn(),
      writeFileSync: vi.fn(),
      readFileSync: vi.fn(() => ''),
      readdirSync: vi.fn(() => []),
      statSync: vi.fn(() => ({ isDirectory: () => false })),
      copyFileSync: vi.fn(),
    },
  };
});

// Mock mount-security
vi.mock('./mount-security.js', () => ({
  validateAdditionalMounts: vi.fn(() => []),
}));

// The runner copy has its own suite (runner-seed.test.ts); here only the calls.
vi.mock('./runner-seed.js', () => ({
  hostRunnerBundle: vi.fn(() => '/pinned/agent-runner-src'),
  refreshRunnerCopy: vi.fn(() => 'current'),
}));

// Create a controllable fake ChildProcess
function createFakeProcess() {
  const proc = new EventEmitter() as EventEmitter & {
    stdin: PassThrough;
    stdout: PassThrough;
    stderr: PassThrough;
    kill: ReturnType<typeof vi.fn>;
    pid: number;
  };
  proc.stdin = new PassThrough();
  proc.stdout = new PassThrough();
  proc.stderr = new PassThrough();
  proc.kill = vi.fn();
  proc.pid = 12345;
  return proc;
}

let fakeProc: ReturnType<typeof createFakeProcess>;

// Mock child_process.spawn
vi.mock('child_process', async () => {
  const actual =
    await vi.importActual<typeof import('child_process')>('child_process');
  return {
    ...actual,
    spawn: vi.fn(() => fakeProc),
    exec: vi.fn(
      (_cmd: string, _opts: unknown, cb?: (err: Error | null) => void) => {
        if (cb) cb(null);
        return new EventEmitter();
      },
    ),
  };
});

import { spawn } from 'child_process';

import {
  OUTPUT_SETTLE_TIMEOUT,
  runContainerAgent,
  ContainerOutput,
} from './container-runner.js';
import { hostRunnerBundle, refreshRunnerCopy } from './runner-seed.js';
import type { RegisteredGroup } from './types.js';

// Taken now, before any test clears the mocks: importing the module is when
// the host starts.
const runnerBundleCallsAtStart = [...vi.mocked(hostRunnerBundle).mock.calls];

const testGroup: RegisteredGroup = {
  name: 'Test Group',
  folder: 'test-group',
  trigger: '@Andy',
  added_at: new Date().toISOString(),
};

const testInput = {
  prompt: 'Hello',
  groupFolder: 'test-group',
  chatJid: 'test@g.us',
  isMain: false,
};

function emitOutputMarker(
  proc: ReturnType<typeof createFakeProcess>,
  output: ContainerOutput,
) {
  const json = JSON.stringify(output);
  proc.stdout.push(`${OUTPUT_START_MARKER}\n${json}\n${OUTPUT_END_MARKER}\n`);
}

describe('container-runner timeout behavior', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fakeProc = createFakeProcess();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('timeout after output resolves as success', async () => {
    const onOutput = vi.fn(async () => {});
    const resultPromise = runContainerAgent(
      testGroup,
      testInput,
      () => {},
      onOutput,
    );

    // Emit output with a result
    emitOutputMarker(fakeProc, {
      status: 'success',
      result: 'Here is my response',
      newSessionId: 'session-123',
    });

    // Let output processing settle
    await vi.advanceTimersByTimeAsync(10);

    // Fire the hard timeout (IDLE_TIMEOUT + 30s = 1830000ms)
    await vi.advanceTimersByTimeAsync(1830000);

    // Emit close event (as if container was stopped by the timeout)
    fakeProc.emit('close', 137);

    // Let the promise resolve
    await vi.advanceTimersByTimeAsync(10);

    const result = await resultPromise;
    expect(result.status).toBe('success');
    expect(result.newSessionId).toBe('session-123');
    expect(onOutput).toHaveBeenCalledWith(
      expect.objectContaining({ result: 'Here is my response' }),
    );
  });

  it('timeout with no output resolves as error', async () => {
    const onOutput = vi.fn(async () => {});
    const resultPromise = runContainerAgent(
      testGroup,
      testInput,
      () => {},
      onOutput,
    );

    // No output emitted — fire the hard timeout
    await vi.advanceTimersByTimeAsync(1830000);

    // Emit close event
    fakeProc.emit('close', 137);

    await vi.advanceTimersByTimeAsync(10);

    const result = await resultPromise;
    expect(result.status).toBe('error');
    expect(result.error).toContain('timed out');
    expect(onOutput).not.toHaveBeenCalled();
  });

  it('normal exit after output resolves as success', async () => {
    const onOutput = vi.fn(async () => {});
    const resultPromise = runContainerAgent(
      testGroup,
      testInput,
      () => {},
      onOutput,
    );

    // Emit output
    emitOutputMarker(fakeProc, {
      status: 'success',
      result: 'Done',
      newSessionId: 'session-456',
    });

    await vi.advanceTimersByTimeAsync(10);

    // Normal exit (no timeout)
    fakeProc.emit('close', 0);

    await vi.advanceTimersByTimeAsync(10);

    const result = await resultPromise;
    expect(result.status).toBe('success');
    expect(result.newSessionId).toBe('session-456');
  });
});

describe('container-runner exit with output callbacks still running', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fakeProc = createFakeProcess();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Whether the promise has settled by now. */
  async function settled(promise: Promise<unknown>): Promise<boolean> {
    let done = false;
    void promise.then(() => (done = true));
    await vi.advanceTimersByTimeAsync(0);
    return done;
  }

  // A refusal ends in a non-zero exit once stdin closes, while its callback
  // can still be sending the notice. The caller settles the run's state when
  // the run resolves, so a callback that ran later would undo that.
  const exits: Array<[string, () => void | Promise<void>]> = [
    ['a non-zero exit', () => void fakeProc.emit('close', 1)],
    ['a clean exit', () => void fakeProc.emit('close', 0)],
    [
      'a timeout',
      async () => {
        await vi.advanceTimersByTimeAsync(1830000);
        fakeProc.emit('close', 137);
      },
    ],
    ['a spawn error', () => void fakeProc.emit('error', new Error('gone'))],
  ];
  for (const [name, exit] of exits) {
    it(`resolves only after the callbacks have run, on ${name}`, async () => {
      let sent: () => void = () => {};
      const calls: string[] = [];
      const onOutput = vi.fn(async (output: ContainerOutput) => {
        calls.push(String(output.result));
        if (calls.length === 1) await new Promise<void>((r) => (sent = r));
      });
      const resultPromise = runContainerAgent(
        testGroup,
        testInput,
        () => {},
        onOutput,
      );
      emitOutputMarker(fakeProc, { status: 'success', result: 'first' });
      emitOutputMarker(fakeProc, { status: 'success', result: 'second' });
      await vi.advanceTimersByTimeAsync(10);

      await exit();
      expect(await settled(resultPromise)).toBe(false);

      sent();
      expect(await settled(resultPromise)).toBe(true);
      expect(calls).toEqual(['first', 'second']);
    });
  }

  it('stops waiting for a callback that never finishes', async () => {
    const onOutput = vi.fn(() => new Promise<void>(() => {}));
    const resultPromise = runContainerAgent(
      testGroup,
      testInput,
      () => {},
      onOutput,
    );
    emitOutputMarker(fakeProc, { status: 'success', result: 'stuck' });
    await vi.advanceTimersByTimeAsync(10);
    fakeProc.emit('close', 1);

    await vi.advanceTimersByTimeAsync(OUTPUT_SETTLE_TIMEOUT - 1000);
    expect(await settled(resultPromise)).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect((await resultPromise).status).toBe('error');
  });

  it('runs the later callbacks, and resolves, after one throws', async () => {
    const calls: string[] = [];
    const onOutput = vi.fn(async (output: ContainerOutput) => {
      calls.push(String(output.result));
      if (calls.length === 1) throw new Error('send failed');
    });
    const resultPromise = runContainerAgent(
      testGroup,
      testInput,
      () => {},
      onOutput,
    );
    emitOutputMarker(fakeProc, { status: 'success', result: 'first' });
    emitOutputMarker(fakeProc, { status: 'success', result: 'second' });
    await vi.advanceTimersByTimeAsync(10);
    fakeProc.emit('close', 0);
    await vi.advanceTimersByTimeAsync(10);

    expect((await resultPromise).status).toBe('success');
    expect(calls).toEqual(['first', 'second']);
  });
});

describe('container-runner agent model', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fakeProc = createFakeProcess();
    vi.mocked(spawn).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('pins the configured model and its aliases in the container env', async () => {
    const resultPromise = runContainerAgent(testGroup, testInput, () => {});
    fakeProc.emit('close', 0);
    await vi.advanceTimersByTimeAsync(10);
    await resultPromise;

    const args = vi.mocked(spawn).mock.calls[0][1] as string[];
    const envs = args.filter((_, i) => args[i - 1] === '-e');
    expect(envs).toContain('ANTHROPIC_MODEL=model-under-test');
    expect(envs).toContain('ANTHROPIC_DEFAULT_SONNET_MODEL=sonnet-under-test');
    expect(envs).toContain('ANTHROPIC_DEFAULT_HAIKU_MODEL=haiku-under-test');
    // Every -e must come before the image name, or docker passes it to the
    // container's entrypoint as an argument instead of setting it.
    const image = args.indexOf('paperclaw-agent:latest');
    expect(args.lastIndexOf('-e')).toBeLessThan(image);
  });
});

describe('container-runner agent-runner copy', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fakeProc = createFakeProcess();
    vi.mocked(spawn).mockClear();
    vi.mocked(refreshRunnerCopy).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('brings the group copy up to date before mounting it as the runner source', async () => {
    const resultPromise = runContainerAgent(testGroup, testInput, () => {});
    fakeProc.emit('close', 0);
    await vi.advanceTimersByTimeAsync(10);
    await resultPromise;

    const copy =
      '/tmp/paperclaw-test-data/sessions/test-group/agent-runner-src';
    expect(refreshRunnerCopy).toHaveBeenCalledWith(
      '/pinned/agent-runner-src',
      copy,
      'test-group',
    );
    const args = vi.mocked(spawn).mock.calls[0][1] as string[];
    expect(args).toContain(`${copy}:/app/src`);
    // Before the container is spawned, so this start compiles the result.
    expect(
      vi.mocked(refreshRunnerCopy).mock.invocationCallOrder[0],
    ).toBeLessThan(vi.mocked(spawn).mock.invocationCallOrder[0]);
  });

  // A build while the host runs restages the runner; read at each start, it
  // reached groups before the restart that runs the host built with it.
  it('settles the runner once, when the host starts, not at each container start', async () => {
    expect(runnerBundleCallsAtStart).toEqual([
      [
        expect.stringMatching(/container-runner\.ts$/),
        `${process.cwd()}/container/agent-runner/src`,
        '/tmp/paperclaw-test-data/runner-bundle',
      ],
    ]);
    vi.mocked(hostRunnerBundle).mockClear();

    for (let i = 0; i < 2; i++) {
      fakeProc = createFakeProcess();
      const resultPromise = runContainerAgent(testGroup, testInput, () => {});
      fakeProc.emit('close', 0);
      await vi.advanceTimersByTimeAsync(10);
      await resultPromise;
    }

    expect(hostRunnerBundle).not.toHaveBeenCalled();
    expect(refreshRunnerCopy).toHaveBeenCalledTimes(2);
  });
});
