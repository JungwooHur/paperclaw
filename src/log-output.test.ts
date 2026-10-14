import { execFileSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { pathToFileURL } from 'url';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

/**
 * What the bot actually writes to logs/paperclaw.log: a separate process that
 * reads its level from .env and writes through pino-pretty, the way the
 * service runs, so the text checked here is the text the watchdog greps.
 */
const repo = path.resolve(import.meta.dirname, '..');
const tsx = path.join(repo, 'node_modules', '.bin', 'tsx');
const src = (file: string) =>
  JSON.stringify(pathToFileURL(path.join(repo, 'src', file)).href);

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'paperclaw-log-output-'));
});

afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** Runs `body` (TypeScript) with `dotenv` as the service's .env; returns stdout. */
function logOutput(dotenv: string, body: string): string {
  fs.writeFileSync(path.join(tmp, '.env'), dotenv);
  const script = path.join(tmp, 'run.ts');
  fs.writeFileSync(script, body);
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PAPERCLAW_ENV_FILE: path.join(tmp, '.env'),
  };
  delete env.LOG_LEVEL;
  return execFileSync(tsx, [script], {
    cwd: tmp,
    env,
    encoding: 'utf-8',
    timeout: 30_000,
  });
}

describe('the lines the watchdog reads', () => {
  const watchdog = fs.readFileSync(
    path.join(repo, 'scripts', 'paperclaw-watchdog.sh'),
    'utf-8',
  );
  // Every pattern the watchdog counts or extracts from the log.
  const patterns = [...watchdog.matchAll(/grep -(?:c|oE) "([^"]+)"/g)].map(
    (m) => new RegExp(m[1]),
  );

  it('finds the patterns in the watchdog', () => {
    // Heartbeat, connect and close at the least; none found means the
    // extraction above broke, not that the watchdog stopped reading the log.
    expect(patterns.length).toBeGreaterThanOrEqual(3);
  });

  it.each(['warn', 'error', 'silent'])(
    'are written with LOG_LEVEL=%s in .env',
    (level) => {
      const out = logOutput(
        `LOG_LEVEL=${level}\n`,
        `import { logger } from ${src('logger.ts')};
import {
  logConnected,
  logConnectionClosed,
  logHeartbeat,
} from ${src('liveness-log.ts')};
logger.info('an ordinary info line');
logHeartbeat(true);
logHeartbeat(false);
logConnectionClosed({ reason: 405, shouldReconnect: true });
logConnected();
`,
      );

      // The level really applies, so the lines below are not a given.
      expect(out).not.toContain('an ordinary info line');
      for (const pattern of patterns) expect(out).toMatch(pattern);
    },
    30_000,
  );

  it('are written only by liveness-log.ts, the logger pinned at info', () => {
    // A second writer would log through the plain logger, which LOG_LEVEL can
    // mute, and the watchdog would read that silence as a hang.
    const files = fs
      .readdirSync(path.join(repo, 'src'), { recursive: true })
      .map(String)
      .filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'))
      .filter((f) => path.basename(f) !== 'liveness-log.ts');
    const writers = files.filter((f) =>
      fs
        .readFileSync(path.join(repo, 'src', f), 'utf-8')
        .split('\n')
        .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
        .some((line) => patterns.some((p) => p.test(line))),
    );
    expect(writers).toEqual([]);
  });

  it('each have a caller', () => {
    const liveness = fs.readFileSync(
      path.join(repo, 'src', 'liveness-log.ts'),
      'utf-8',
    );
    const exported = [...liveness.matchAll(/export function (\w+)/g)].map(
      (m) => m[1],
    );
    const code = fs
      .readdirSync(path.join(repo, 'src'), { recursive: true })
      .map(String)
      .filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'))
      .filter((f) => path.basename(f) !== 'liveness-log.ts')
      .map((f) => fs.readFileSync(path.join(repo, 'src', f), 'utf-8'))
      .join('\n');
    expect(exported.length).toBeGreaterThan(0);
    expect(exported.filter((fn) => !code.includes(`${fn}(`))).toEqual([]);
  });
});

describe('an unknown LOG_LEVEL', () => {
  it('is reported as a WARN that names the value', () => {
    const out = logOutput(
      'LOG_LEVEL=warning\n',
      `import ${src('logger.ts')};\n`,
    );
    const line = out.split('\n').find((l) => l.includes('Unknown LOG_LEVEL'));
    // A merge key named `level` replaces pino's own, and the line comes out
    // as USERLVL with the bad value swallowed.
    expect(line).toContain('WARN');
    expect(out).toContain('"warning"');
  }, 30_000);
});
