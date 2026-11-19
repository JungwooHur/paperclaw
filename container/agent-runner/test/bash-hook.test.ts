/**
 * Runs the agent's Bash calls through the pinned agent SDK and the CLI bundled
 * with it, against a fake Messages API on loopback, with and without the
 * runner's Bash hook, and checks what bash printed for each command.
 */
import { randomUUID } from 'crypto';
import fs from 'fs';
import http from 'http';
import type { AddressInfo } from 'net';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

import { query, type HookCallback } from '@anthropic-ai/claude-agent-sdk';
import { beforeAll, describe, expect, it } from 'vitest';

import { sanitizeBashHook } from '../src/bash-hook.js';

const CLI = fileURLToPath(
  new URL(
    '../node_modules/@anthropic-ai/claude-agent-sdk/cli.js',
    import.meta.url,
  ),
);

/** Value of PROBE_VAR in the CLI's environment. */
const PROBE = 'probe-value';

const PIPED_VAR = 'echo "v=${PROBE_VAR:+set} $PROBE_VAR" | cat';
const PIPED_FOR = [
  "printf '{}' | python3 -c \"import sys",
  "for line in sys.stdin: print(line, end='')\"",
].join('\n');

/** Prints whether the CLI's auth key is set. */
const AUTH_KEY = 'echo "k=${ANTHROPIC_API_KEY:+set}"';

/** Command -> what bash prints when it runs the command as written. */
const AS_WRITTEN: Record<string, string> = {
  [PIPED_VAR]: `v=set ${PROBE}`,
  [PIPED_FOR]: '{}',
  'cat | wc -c | tr -d " "': '0',
  'echo "$(printf ab)" | cat': 'ab',
  'echo `printf cd` | cat': 'cd',
  'printf \'ab\\n\' | while read -r l; do echo "got $l"; done': 'got ab',
  "cat <<'EOF' | tr a-z A-Z\nquoted $PROBE_VAR\nEOF": 'QUOTED $PROBE_VAR',
  'cat <<EOF | cat\nplain $PROBE_VAR\nEOF': `plain ${PROBE}`,
  'echo \')\' | cat; echo "a)b" | cat': ')\na)b',
  'case x in x) echo case-x;; esac | cat': 'case-x',
  'echo \'it\'"\'"\'s\' "a \\"q\\"" | cat': 'it\'s a "q"',
  "printf '%s\\n' 'a\\b' \"c\\\\d\" | cat": 'a\\b\nc\\d',
  'echo trailing | cat\n': 'trailing',
  'echo $((1 << 2)) | cat; [ 1 != 2 ] && echo ne': '4\nne',
  "echo 1 \\\n  '!' | cat": '1 !',
  'echo $((1 << 2)) "x!y"; ! false && echo neg': '4 x!y\nneg',
  'x=PROBE_VAR; [[ 1 != 2 ]] && echo "${!x}" $((1<<2))': `${PROBE} 4`,
  'read -r x; echo "rc=$?"': 'rc=1',
};

/** Prints `out` and leaves exit status 3. */
const EXIT_3 = 'echo out | cat; (exit 3)';

/** Commands whose last command exits 1, which the CLI reads as no error. */
const NOT_ERRORS = [
  'grep zzz /dev/null',
  'test -e /nonexistent',
  'echo a | grep zzz',
];

/** Commands that stop at a bash error on their last line. */
const LINE_ERRORS = ['cd /nonexistent', 'true\n\ncd /nonexistent'];

/**
 * Lines past the unhooked call's line number that a bash error in a hooked
 * call reports.
 */
const LINE_OFFSET = 4;

/** The line number in a bash error message `text`. */
function errorLine(text: string): number {
  const match = /line (\d+): cd:/.exec(text);
  if (!match) throw new Error(`no bash error line in ${JSON.stringify(text)}`);
  return Number(match[1]);
}

interface ContentBlock {
  type: string;
  id?: string;
  name?: string;
  input?: unknown;
  text?: string;
  tool_use_id?: string;
  is_error?: boolean;
  content?: string | { type: string; text?: string }[];
}

interface ToolResult {
  text: string;
  isError: boolean;
}

function sse(res: http.ServerResponse, model: string, blocks: ContentBlock[]) {
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  const ev = (name: string, data: object) =>
    res.write(
      `event: ${name}\ndata: ${JSON.stringify({ type: name, ...data })}\n\n`,
    );
  ev('message_start', {
    message: {
      id: `msg_${randomUUID().slice(0, 8)}`,
      type: 'message',
      role: 'assistant',
      model,
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 1 },
    },
  });
  blocks.forEach((b, index) => {
    if (b.type === 'text') {
      ev('content_block_start', {
        index,
        content_block: { type: 'text', text: '' },
      });
      ev('content_block_delta', {
        index,
        delta: { type: 'text_delta', text: b.text },
      });
    } else {
      ev('content_block_start', {
        index,
        content_block: { type: 'tool_use', id: b.id, name: b.name, input: {} },
      });
      ev('content_block_delta', {
        index,
        delta: {
          type: 'input_json_delta',
          partial_json: JSON.stringify(b.input),
        },
      });
    }
    ev('content_block_stop', { index });
  });
  const stop = blocks.some((b) => b.type === 'tool_use')
    ? 'tool_use'
    : 'end_turn';
  ev('message_delta', {
    delta: { stop_reason: stop, stop_sequence: null },
    usage: { output_tokens: 5 },
  });
  ev('message_stop', {});
  res.end();
}

function resultText(block: ContentBlock): string {
  const c = block.content ?? '';
  return typeof c === 'string' ? c : c.map((p) => p.text ?? '').join('');
}

/**
 * Has a fake model call Bash once for each of `commands`, in one turn, through
 * query() with `hooks` on PreToolUse; returns each command's tool result.
 */
async function runBash(
  commands: string[],
  hooks: HookCallback[],
): Promise<Map<string, ToolResult>> {
  const calls = new Map<string, string>();
  const results = new Map<string, ToolResult>();
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      if (req.url?.includes('count_tokens')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        return res.end(JSON.stringify({ input_tokens: 100 }));
      }
      const b = JSON.parse(body) as {
        model: string;
        tools?: { name: string }[];
        messages: { role: string; content: string | ContentBlock[] }[];
      };
      for (const m of b.messages) {
        if (typeof m.content === 'string') continue;
        for (const block of m.content) {
          const command = calls.get(block.tool_use_id ?? '');
          if (block.type === 'tool_result' && command !== undefined) {
            results.set(command, {
              text: resultText(block),
              isError: block.is_error === true,
            });
          }
        }
      }
      const isMainLoop = b.tools?.some((t) => t.name === 'Bash') ?? false;
      if (!isMainLoop || calls.size > 0) {
        return sse(res, b.model, [{ type: 'text', text: 'done' }]);
      }
      const uses = commands.map((command) => {
        const id = `toolu_${randomUUID().replace(/-/g, '').slice(0, 20)}`;
        calls.set(id, command);
        return {
          type: 'tool_use',
          id,
          name: 'Bash',
          input: { command, description: 'probe' },
        };
      });
      sse(res, b.model, uses);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as AddressInfo).port;
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'bash-hook-home-'));
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'bash-hook-cwd-'));
  let stderr = '';
  try {
    for await (const message of query({
      prompt: 'run the probe commands',
      options: {
        cwd,
        env: {
          PATH: process.env.PATH,
          HOME: home,
          PROBE_VAR: PROBE,
          ANTHROPIC_BASE_URL: `http://127.0.0.1:${port}`,
          ANTHROPIC_API_KEY: 'offline-test-key',
          ANTHROPIC_MODEL: 'claude-offline-test',
          DISABLE_TELEMETRY: '1',
          DISABLE_AUTOUPDATER: '1',
          DISABLE_ERROR_REPORTING: '1',
          CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
          // Any connection off loopback goes to a closed port and fails.
          HTTPS_PROXY: 'http://127.0.0.1:9',
          HTTP_PROXY: 'http://127.0.0.1:9',
          NO_PROXY: '127.0.0.1',
        },
        allowedTools: ['Bash'],
        permissionMode: 'bypassPermissions',
        allowDangerouslySkipPermissions: true,
        hooks: { PreToolUse: [{ matcher: 'Bash', hooks }] },
        stderr: (data) => (stderr += data),
      },
    })) {
      if (message.type === 'result' && message.subtype !== 'success') {
        throw new Error(`query ended ${message.subtype}:\n${stderr}`);
      }
    }
  } finally {
    server.close();
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(cwd, { recursive: true, force: true });
  }
  const missing = commands.filter((c) => !results.has(c));
  if (missing.length > 0) {
    throw new Error(
      `no tool result for ${JSON.stringify(missing)}:\n${stderr}`,
    );
  }
  return results;
}

let hooked: Map<string, ToolResult>;
let raw: Map<string, ToolResult>;

beforeAll(async () => {
  if (!fs.existsSync(CLI)) {
    throw new Error(`${CLI} missing: run npm ci in container/agent-runner`);
  }
  [hooked, raw] = await Promise.all([
    runBash(
      [
        ...Object.keys(AS_WRITTEN),
        AUTH_KEY,
        EXIT_3,
        ...NOT_ERRORS,
        ...LINE_ERRORS,
      ],
      [sanitizeBashHook],
    ),
    runBash(
      [PIPED_VAR, PIPED_FOR, AUTH_KEY, ...NOT_ERRORS, ...LINE_ERRORS],
      [],
    ),
  ]);
}, 90_000);

describe('a Bash call through the runner hook', () => {
  for (const [command, expected] of Object.entries(AS_WRITTEN)) {
    it(`runs ${JSON.stringify(command)} as written`, () => {
      expect(hooked.get(command)).toEqual({ text: expected, isError: false });
    });
  }

  it('unsets the auth key', () => {
    expect(hooked.get(AUTH_KEY)).toEqual({ text: 'k=', isError: false });
  });

  it('keeps the exit status', () => {
    expect(hooked.get(EXIT_3)).toEqual({
      text: 'Exit code 3\nout',
      isError: true,
    });
  });

  for (const command of NOT_ERRORS) {
    it(`reads exit 1 of ${JSON.stringify(command)} as no error`, () => {
      expect(hooked.get(command)).toEqual({ text: '', isError: false });
      expect(hooked.get(command)).toEqual(raw.get(command));
    });
  }

  for (const command of LINE_ERRORS) {
    it(`reports the error line of ${JSON.stringify(command)} ${LINE_OFFSET} lines on`, () => {
      expect(errorLine(hooked.get(command)!.text)).toBe(
        errorLine(raw.get(command)!.text) + LINE_OFFSET,
      );
    });
  }
});

describe('a Bash call without the hook, under the pinned CLI', () => {
  it('sees the auth key', () => {
    expect(raw.get(AUTH_KEY)).toEqual({ text: 'k=set', isError: false });
  });

  it('erases a $VAR in a piped command', () => {
    expect(raw.get(PIPED_VAR)).toEqual({ text: 'v=', isError: false });
  });

  it("feeds /dev/null to a pipe's last stage", () => {
    expect(raw.get(PIPED_FOR)).toEqual({ text: '', isError: false });
  });
});
