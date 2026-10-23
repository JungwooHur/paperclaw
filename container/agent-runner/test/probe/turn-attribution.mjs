// End-to-end probe of turn attribution, run inside the agent image, offline,
// by scripts/probe-turn-attribution.sh. Prints the fixture JSON on stdout.
//
// A fake Messages API on 127.0.0.1 answers the CLI. The runner compiled to
// /tmp/dist gets its prompt on stdin and its follow-ups as IPC files, each
// written at the moment its case needs: after an output, or, timed off the
// fake API, while a tool runs or during a turn's last API call. Every output
// the runner writes is recorded under the case that produced it. The CLI's
// home carries the settings.json every group is seeded with (agent teams on)
// and one user skill, for the slash-command cases. Two turns fail after the
// CLI acknowledged their prompt, one on a scheduled run's maxTurns and one on
// an API stream that breaks after a tool call, and the follow-up after each
// answers with the history its request carried. The last container runs
// the CLI through a `node` that drops --replay-user-messages, so replay is
// broken there, with no switch in the runner. Two containers show what a
// usage-limit refusal leaves in the session: the first is refused, and the
// second resumes the session the host keeps, with the refused message sent
// again. One more case drives the SDK
// directly, as the runner never reuses a uuid: what the CLI does with a push
// under a uuid it has already seen. Last, it reads out of the CLI bundle the
// prompt the CLI injects when its input closes while teammates are up, which
// no case here makes it send.
import http from 'http';
import { spawn } from 'child_process';
import { randomUUID } from 'crypto';
import fs from 'fs';
import path from 'path';

import { sourceHash } from './source-hash.mjs';

const RUNNER = '/tmp/dist/index.js';
const SDK_PACKAGE = '/app/node_modules/@anthropic-ai/claude-agent-sdk/package.json';
const SDK_MODULE = '/app/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs';
const SDK_CLI = '/app/node_modules/@anthropic-ai/claude-agent-sdk/cli.js';
const IPC_INPUT_DIR = '/workspace/ipc/input';
const PORT = 8787;
const MODEL = 'claude-sonnet-4-5-20250929';
const OUTPUT_START = '---PAPERCLAW_OUTPUT_START---';
const OUTPUT_END = '---PAPERCLAW_OUTPUT_END---';
// The runner nudges after 60 s parked with a background task outstanding.
const TIMEOUT_MS = 330_000;

const t0 = Date.now();
const log = (...a) =>
  console.error(((Date.now() - t0) / 1000).toFixed(1).padStart(6), ...a);

function writeInput(file, text, ref) {
  const target = path.join(IPC_INPUT_DIR, file);
  const body = { type: 'message', text, ...(ref ? { ref } : {}) };
  fs.writeFileSync(`${target}.tmp`, JSON.stringify(body));
  fs.renameSync(`${target}.tmp`, target);
  log('IPC', file, ref ?? '(no ref)');
}

// ---------- fake API ----------
function textOf(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((b) => (b.type === 'text' ? b.text : b.type === 'tool_result' ? '[tool_result]' : ''))
    .join(' ');
}

function sse(res, blocks, stop) {
  res.writeHead(200, { 'content-type': 'text/event-stream', 'request-id': 'req_probe' });
  const ev = (name, data) => res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
  ev('message_start', {
    type: 'message_start',
    message: {
      id: 'msg_' + randomUUID().slice(0, 8), type: 'message', role: 'assistant', model: MODEL,
      content: [], stop_reason: null, stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 1 },
    },
  });
  blocks.forEach((b, i) => {
    if (b.type === 'text') {
      ev('content_block_start', { type: 'content_block_start', index: i, content_block: { type: 'text', text: '' } });
      ev('content_block_delta', { type: 'content_block_delta', index: i, delta: { type: 'text_delta', text: b.text } });
    } else {
      ev('content_block_start', {
        type: 'content_block_start', index: i,
        content_block: { type: 'tool_use', id: 'toolu_' + randomUUID().replace(/-/g, '').slice(0, 20), name: b.name, input: {} },
      });
      ev('content_block_delta', {
        type: 'content_block_delta', index: i,
        delta: { type: 'input_json_delta', partial_json: JSON.stringify(b.input) },
      });
    }
    ev('content_block_stop', { type: 'content_block_stop', index: i });
  });
  ev('message_delta', { type: 'message_delta', delta: { stop_reason: stop, stop_sequence: null }, usage: { output_tokens: 5 } });
  ev('message_stop', { type: 'message_stop' });
  res.end();
}

/** A stream that breaks: it starts a message and ends without finishing it. */
function brokenSse(res) {
  res.writeHead(200, { 'content-type': 'text/event-stream', 'request-id': 'req_probe' });
  res.write(`event: message_start\ndata: ${JSON.stringify({
    type: 'message_start',
    message: {
      id: 'msg_broken', type: 'message', role: 'assistant', model: MODEL,
      content: [], stop_reason: null, stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 1 },
    },
  })}\n\n`);
  res.end();
}

/**
 * The account's usage limit, as the API refuses a request with it: a 429 whose
 * unified rate-limit headers say rejected. The CLI words its refusal from
 * these, for a subscription token only.
 */
function refusal(res) {
  res.writeHead(429, {
    'content-type': 'application/json',
    'request-id': 'req_probe',
    'anthropic-ratelimit-unified-status': 'rejected',
    'anthropic-ratelimit-unified-reset': String(Math.floor(Date.now() / 1000) + 3 * 3600),
    'anthropic-ratelimit-unified-representative-claim': 'five_hour',
    'anthropic-ratelimit-unified-overage-status': 'rejected',
    'anthropic-ratelimit-unified-overage-disabled-reason': 'org_level_disabled',
  });
  res.end(JSON.stringify({
    type: 'error',
    error: { type: 'rate_limit_error', message: 'Number of request tokens has exceeded your rate limit' },
  }));
}

function json(res, code, body) {
  res.writeHead(code, { 'content-type': 'application/json', 'request-id': 'req_probe' });
  res.end(JSON.stringify(body));
}

const text = (t) => ({ type: 'text', text: t });
function lastToolName(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const content = msgs[i].role === 'assistant' && Array.isArray(msgs[i].content) ? msgs[i].content : [];
    const use = content.filter((x) => x.type === 'tool_use').pop();
    if (use) return use.name;
  }
  return null;
}
const tool = (name, input) => ({ type: 'tool_use', name, input });
const MAX_TURNS_GLOB = 'probe-max-turns-*';
const BROKEN_COMMAND = 'echo before the stream breaks';
/** The input of the last tool call in the conversation. */
function lastToolInput(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const content = msgs[i].role === 'assistant' && Array.isArray(msgs[i].content) ? msgs[i].content : [];
    const use = content.filter((x) => x.type === 'tool_use').pop();
    if (use) return use.input;
  }
  return null;
}
/** A block or message text as the history below names it. */
function partName(t) {
  if (t.startsWith('<system-reminder>')) return 'reminder';
  return (t.match(/PROBE-[A-Z]+/) || [t.slice(0, 40)])[0];
}

/**
 * What a request carried, message by message: its role and each block, a
 * text by its marker, else by its first words. A tool call and its result
 * repeated back to back are counted, not listed.
 */
function historyOf(msgs) {
  const items = msgs.map((m) => {
    const parts = Array.isArray(m.content)
      ? m.content.map((c) => (c.type === 'text' ? partName(c.text) : c.type))
      : [partName(String(m.content))];
    return `${m.role} ${parts.join('+')}`;
  });
  const out = [];
  for (let i = 0; i < items.length;) {
    let n = 1;
    while (items[i + 2 * n + 1] !== undefined && items[i + 2 * n] === items[i] && items[i + 2 * n + 1] === items[i + 1]) n++;
    if (n > 1) {
      out.push(`(${items[i]}, ${items[i + 1]}) x${n}`);
      i += 2 * n;
    } else {
      out.push(items[i]);
      i++;
    }
  }
  return out.join(', ');
}
const once = new Set();
// While the reused-uuid case runs: every marker any request carried.
let modelSaw = null;
// The running container's command that must land behind a team message.
let behindTeam = null;

function reply(b) {
  const msgs = b.messages || [];
  const last = msgs[msgs.length - 1];
  const lastText = textOf(last?.content);
  const afterTool = Array.isArray(last?.content) && last.content.some((x) => x.type === 'tool_result');
  const marker = (lastText.match(/PROBE-[A-Z]+/g) || []).pop() || 'none';
  if (process.env.PROBE_VERBOSE) log('API', JSON.stringify({ marker, afterTool, last: lastText.slice(0, 160) }));
  // Side requests (titles, topic checks) carry no tools.
  if (!Array.isArray(b.tools) || b.tools.length === 0) return { blocks: [text('ok')] };
  if (textOf(msgs[0]?.content).includes('SUBAGENT-WORK')) {
    return afterTool
      ? { blocks: [text('SUB-DONE')] }
      : { blocks: [tool('Bash', { command: 'sleep 2', description: 'subagent sleep' })], stop: 'tool_use' };
  }
  // Only an expanded skill carries its body: proof the CLI ran the command.
  if (!afterTool && lastText.includes('PROBE-SKILLBODY')) {
    return { blocks: [text('ANSWER skill body')] };
  }
  // The lead's turn for a member going idle.
  if (lastText.includes('idle_notification')) return { blocks: [text('ANSWER team message')] };
  // An agent-team member: answers once, then goes idle, which messages the lead.
  // Slow, so the lead's turn is over when the member's message arrives.
  if (textOf(msgs[0]?.content).includes('TEAMMATE-WORK')) return { blocks: [text('TM-DONE')], delayMs: 2000 };
  if (afterTool && lastToolName(msgs) === 'TeamCreate') {
    return {
      blocks: [tool('Task', {
        description: 'teammate', prompt: 'TEAMMATE-WORK say done',
        subagent_type: 'general-purpose', name: 'mate', team_name: 'probe-team',
      })],
      stop: 'tool_use',
    };
  }
  // A follow-up after a failed turn answers with the history its request
  // carried: whether the CLI sent the failed turn's prompt again, or only
  // kept it where it was. Before the tool loop below: the CLI may send the
  // follow-up in the message with a tool result.
  if (marker === 'PROBE-AFTERFAIL' || marker === 'PROBE-AFTERBROKEN' || marker === 'PROBE-RESENT') {
    return { blocks: [text(`ANSWER ${marker} after: ${historyOf(msgs)}`)] };
  }
  // The tool call's next API call: the stream breaks there, and on the CLI's
  // retry, so the turn fails after the CLI acknowledged its prompt.
  if (afterTool && lastToolInput(msgs)?.command === BROKEN_COMMAND) {
    return { broken: true };
  }
  // A tool call after every tool result: the turn runs into maxTurns.
  if (afterTool && lastToolInput(msgs)?.pattern === MAX_TURNS_GLOB) {
    return { blocks: [tool('Glob', { pattern: MAX_TURNS_GLOB })], stop: 'tool_use' };
  }
  if (afterTool) return { blocks: [text('ANSWER after-tool')] };
  switch (marker) {
    case 'PROBE-TOOL':
      // Lands while the tool runs: the CLI merges it into this turn.
      if (!once.has('merged')) {
        once.add('merged');
        setTimeout(() => writeInput('0003-merged.json', 'PROBE-MERGED sent while a tool runs', 'r-merged'), 1000);
      }
      return { blocks: [tool('Bash', { command: 'sleep 4', description: 'main sleep' })], stop: 'tool_use' };
    case 'PROBE-SLOW':
      // Lands during the turn's last API call: the CLI runs it as its own turn.
      if (!once.has('own')) {
        once.add('own');
        setTimeout(() => writeInput('0005-own.json', 'PROBE-OWN sent during the last API call', 'r-own'), 1000);
      }
      return { blocks: [text('ANSWER PROBE-SLOW')], delayMs: 3000 };
    case 'PROBE-CMDSLOW':
      // A bare slash command lands during the turn's last API call.
      if (!once.has('late-command')) {
        once.add('late-command');
        setTimeout(() => writeInput('0023-late.json', '/nonexistent-cmd PROBE-LATE', 'r-late-command'), 1000);
      }
      return { blocks: [text('ANSWER PROBE-CMDSLOW')], delayMs: 3000 };
    case 'PROBE-TEAMSLOW':
      // The member's message, due 2 s after the team was made, is queued
      // during this slow last API call, and the command lands behind it.
      if (behindTeam && !once.has(behindTeam[2])) {
        once.add(behindTeam[2]);
        const command = behindTeam;
        setTimeout(() => writeInput(...command), 4000);
      }
      return { blocks: [text('ANSWER PROBE-TEAMSLOW')], delayMs: 8000 };
    case 'PROBE-TEAM':
      return { blocks: [tool('TeamCreate', { team_name: 'probe-team', description: 'probe' })], stop: 'tool_use' };
    case 'PROBE-EMPTY':
      return { blocks: [text('')] };
    case 'PROBE-REFUSED':
      return { refused: true };
    case 'PROBE-NOTHING':
      // No content at all: the CLI ends the turn in error before it yields a
      // message, so it never acknowledges the prompt it consumed.
      return { blocks: [] };
    case 'PROBE-MAXTURNS':
      return { blocks: [tool('Glob', { pattern: MAX_TURNS_GLOB })], stop: 'tool_use' };
    case 'PROBE-BROKEN':
      return { blocks: [tool('Bash', { command: BROKEN_COMMAND, description: 'probe tool' })], stop: 'tool_use' };
    case 'PROBE-BG':
      return {
        blocks: [tool('Task', {
          description: 'background probe', prompt: 'SUBAGENT-WORK in the background',
          subagent_type: 'general-purpose', run_in_background: true,
        })],
        stop: 'tool_use',
      };
    default:
      return { blocks: [text(`ANSWER ${marker}`)] };
  }
}

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', (c) => (body += c));
  req.on('end', () => {
    if (!req.url.startsWith('/v1/messages')) return json(res, 404, {});
    if (req.url.includes('count_tokens')) return json(res, 200, { input_tokens: 100 });
    let b;
    try { b = JSON.parse(body); } catch { return json(res, 400, {}); }
    if (modelSaw) for (const m of body.match(/PROBE-[A-Z]+/g) ?? []) modelSaw.add(m);
    const r = reply(b);
    const stop = r.stop ?? 'end_turn';
    const send = () => {
      if (r.broken) return brokenSse(res);
      if (r.refused) return refusal(res);
      if (b.stream) return sse(res, r.blocks, stop);
      json(res, 200, {
        id: 'msg_probe', type: 'message', role: 'assistant', model: b.model,
        content: r.blocks.map((x) => (x.type === 'text' ? x : { ...x, id: 'toolu_probe' })),
        stop_reason: stop, stop_sequence: null, usage: { input_tokens: 10, output_tokens: 5 },
      });
    };
    if (r.delayMs) setTimeout(send, r.delayMs);
    else send();
  });
});

// ---------- driver ----------
const close = () => fs.writeFileSync(path.join(IPC_INPUT_DIR, '_close'), '');

/** The running container's runner process. */
let runnerPid = null;

/** The CLI the runner's SDK spawned: the runner's child running cli.js. */
function cliPid() {
  for (const pid of fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf-8');
      const ppid = Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1]);
      const cmdline = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf-8');
      // The CLI renames its process `claude`, over its arguments.
      if (ppid === runnerPid && (stat.includes(' (claude) ') || cmdline.includes('cli.js'))) return Number(pid);
    } catch {
      // gone meanwhile
    }
  }
  throw new Error('no CLI under the runner');
}

/** Signals the runner's CLI: SIGINT ends it with code 0, SIGTERM with 143. */
const signalCli = (signal) => () => {
  const pid = cliPid();
  log('SIGNAL', signal, 'CLI', pid);
  process.kill(pid, signal);
};

/**
 * A directory with a `node` that drops --replay-user-messages and runs the
 * real one. On PATH first, it is the node the SDK spawns the CLI with: the
 * CLI then acknowledges nothing, as one whose replay broke.
 */
function nodeWithoutReplay() {
  const dir = fs.mkdtempSync('/tmp/probe-noreplay-');
  fs.writeFileSync(
    path.join(dir, 'node'),
    '#!/bin/sh\n' +
      'for arg do\n' +
      '  shift\n' +
      '  [ "$arg" = --replay-user-messages ] || set -- "$@" "$arg"\n' +
      'done\n' +
      `exec ${process.execPath} "$@"\n`,
    { mode: 0o755 },
  );
  return dir;
}

// Two containers, as in production: the first starts the group's session and
// the second resumes it, as every later container does. `cases` labels the
// outputs in the order the runner should write them; `after[n]` runs once the
// n-th output is out. `notResults` numbers the outputs made from no SDK
// result (the session marker, an error), which have no subtype. `freshHome`
// runs the CLI in a home of its own, `sameHome` in the previous container's,
// and `subscription` signs it in with a subscription token, not an API key.
const CONTAINERS = [
  {
    prompt: 'PROBE-START the stdin prompt',
    promptRef: 'r-start',
    // Waiting before the runner starts: drained into its first prompt.
    leftover: ['0000-leftover.json', 'PROBE-LEFTOVER waiting at start-up', 'r-leftover'],
    cases: ['start-up drain', 'plain'],
    after: {
      1: () => writeInput('0001-plain.json', 'PROBE-PLAIN a follow-up with no ref'),
      2: close,
    },
  },
  {
    resume: true,
    prompt: 'PROBE-RESUME the stdin prompt',
    promptRef: 'r-resume',
    leftover: ['0010-leftover.json', 'PROBE-LEFTOVER waiting at a resumed start-up', 'r-leftover-2'],
    cases: [
      'start-up drain, resumed session',
      'merged follow-up',
      'turn running when a follow-up came',
      'follow-up as its own turn',
      'textless',
      'error before any message',
      'background dispatch',
      'nudge-only',
    ],
    after: {
      1: () => writeInput('0002-tool.json', 'PROBE-TOOL run a tool', 'r-tool'),
      2: () => writeInput('0004-slow.json', 'PROBE-SLOW a slow last API call', 'r-slow'),
      4: () => writeInput('0006-empty.json', 'PROBE-EMPTY answer with no text', 'r-empty'),
      5: () => writeInput('0007-nothing.json', 'PROBE-NOTHING answer with no content', 'r-nothing'),
      6: () => writeInput('0008-bg.json', 'PROBE-BG dispatch a background task', 'r-bg'),
      // 7: nothing; the runner nudges on its own.
      8: close,
    },
  },
  {
    // The router sends the newest message bare when it is a slash command.
    resume: true,
    prompt: '/probe-skill PROBE-SKILLARG',
    promptRef: 'r-skill',
    cases: [
      'slash command: skill',
      'slash command: local',
      'slash command: unknown',
      'turn running when a slash command came',
      'slash command as its own turn',
      'follow-up after the slash commands',
    ],
    after: {
      1: () => writeInput('0020-cost.json', '/cost', 'r-cost'),
      2: () => writeInput('0021-unknown.json', '/nonexistent-cmd PROBE-UNKNOWN', 'r-unknown'),
      3: () => writeInput('0022-cmdslow.json', 'PROBE-CMDSLOW a slow last API call', 'r-cmdslow'),
      // 4: the command written during that call runs as the next turn.
      5: () => writeInput('0024-after.json', 'PROBE-AFTERSLASH a plain follow-up', 'r-after-slash'),
      6: close,
    },
  },
  {
    // The team it makes stays in the CLI's home, so this is the last
    // container there.
    resume: true,
    prompt: 'PROBE-TEAM make a team',
    promptRef: 'r-team',
    cases: ['team created', 'agent-team message', 'follow-up after the team message'],
    after: {
      2: () => writeInput('0030-after.json', 'PROBE-AFTERTEAM a plain follow-up', 'r-after-team'),
      3: close,
    },
  },
  // A member's message reaches the lead while a host turn runs, and a bare
  // command lands behind it: the CLI runs the team message first, then the
  // command. Each in a home of its own, for a team of its own.
  {
    freshHome: true,
    prompt: 'PROBE-TEAM make a team',
    promptRef: 'r-team-2',
    behindTeam: ['0041-unknown.json', '/nonexistent-cmd PROBE-BEHINDTEAM', 'r-unknown-behind-team'],
    cases: [
      'team created, before an unknown command',
      'turn running when a team message and an unknown command came',
      'team message ahead of an unknown command',
      'unknown command behind a team message',
      'follow-up after the unknown command',
    ],
    after: {
      1: () => writeInput('0040-slow.json', 'PROBE-TEAMSLOW a slow last API call', 'r-team-slow-2'),
      // 2, 3: the team message, then the command, run as turns of their own.
      4: () => writeInput('0042-after.json', 'PROBE-AFTERBEHIND a plain follow-up', 'r-after-unknown'),
      5: close,
    },
  },
  {
    freshHome: true,
    prompt: 'PROBE-TEAM make a team',
    promptRef: 'r-team-3',
    behindTeam: ['0051-skill.json', '/probe-skill PROBE-SKILLARG', 'r-skill-behind-team'],
    cases: [
      'team created, before a skill',
      'turn running when a team message and a skill came',
      'team message ahead of a skill',
      'skill behind a team message',
      'follow-up after the skill',
    ],
    after: {
      1: () => writeInput('0050-slow.json', 'PROBE-TEAMSLOW a slow last API call', 'r-team-slow-3'),
      4: () => writeInput('0052-after.json', 'PROBE-AFTERBEHIND a plain follow-up', 'r-after-skill'),
      5: close,
    },
  },
  // A scheduled run, which the runner caps at maxTurns: the CLI acknowledges
  // the prompt, and the turn calls tools until it fails on that cap.
  {
    freshHome: true,
    scheduled: true,
    prompt: 'PROBE-MAXTURNS call tools until the turn cap',
    promptRef: 'r-max-turns',
    cases: ['turn failed after its acknowledgement', 'success after a failed turn'],
    after: {
      1: () => writeInput('0060-after.json', 'PROBE-AFTERFAIL a plain follow-up', 'r-after-fail'),
      2: close,
    },
  },
  // A message container's turn fails the same way: the CLI acknowledged its
  // prompt, a tool ran, then the API stream broke, and broke again on the
  // retry. A follow-up and a slash command that makes no API call come after.
  {
    freshHome: true,
    prompt: 'PROBE-BROKEN call a tool, then the stream breaks',
    promptRef: 'r-broken',
    cases: [
      'stream broke after its acknowledgement',
      'follow-up after a broken stream',
      'slash command after a broken stream',
    ],
    after: {
      1: () => writeInput('0065-after.json', 'PROBE-AFTERBROKEN a plain follow-up', 'r-after-broken'),
      2: () => writeInput('0066-cost.json', '/cost', 'r-cost-after-broken'),
      3: close,
    },
  },
  // A usage-limit refusal of a follow-up, after an answer in the same
  // session. The host keeps that session (it stores no session from the
  // refused output), and after the reset a new container resumes it, at its
  // latest point, with the refused message sent again. Its answer shows what
  // that request carried of the refused one.
  {
    freshHome: true,
    subscription: true,
    prompt: 'PROBE-BEFORELIMIT the stdin prompt',
    promptRef: 'r-before-limit',
    cases: ['answer before a refusal', 'usage-limit refusal', 'error after a refusal'],
    notResults: [3],
    // Told to close after the refusal, the CLI exits 1.
    exitCode: 1,
    after: {
      1: () => writeInput('0075-refused.json', 'PROBE-REFUSED the limit refuses this', 'r-refused'),
      2: close,
    },
  },
  {
    resume: true,
    sameHome: true,
    subscription: true,
    prompt: 'PROBE-RESENT the refused message, sent again after the reset',
    promptRef: 'r-resent',
    cases: ['resumed after a refusal'],
    after: { 1: close },
  },
  // Replay broken: the guard trips on the first result, a textless one. Then
  // the CLI is ended cleanly, so the query ends and the runner writes its
  // session marker, and in the next query killed, so the runner fails.
  {
    freshHome: true,
    noReplay: true,
    prompt: 'PROBE-EMPTY answer with no text',
    promptRef: 'r-off-empty',
    cases: [
      'textless result with replay broken',
      'answer after attribution went off',
      'session marker after attribution went off',
      'answer in a later query after attribution went off',
      'error after attribution went off',
    ],
    notResults: [3, 5],
    exitCode: 1,
    after: {
      1: () => writeInput('0070-text.json', 'PROBE-OFFTEXT a plain follow-up', 'r-off-text'),
      2: signalCli('SIGINT'),
      3: () => writeInput('0071-later.json', 'PROBE-OFFLATER starts the next query', 'r-off-later'),
      4: signalCli('SIGTERM'),
    },
  },
];

/** A CLI home with what src/container-runner.ts seeds every group with, agent teams included, and the probe's skill. */
function seededHome() {
  const home = fs.mkdtempSync('/tmp/probe-home-');
  fs.mkdirSync(path.join(home, '.claude/skills/probe-skill'), { recursive: true });
  fs.writeFileSync(
    path.join(home, '.claude/settings.json'),
    JSON.stringify({ env: { CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: '1' } }),
  );
  fs.writeFileSync(
    path.join(home, '.claude/skills/probe-skill/SKILL.md'),
    '---\nname: probe-skill\ndescription: A probe skill\ndisable-model-invocation: true\n---\nReply with PROBE-SKILLBODY and nothing else.\n',
  );
  return home;
}
const home = seededHome();
/** The CLI home the last container ran with. */
let lastHome = home;
const outputs = [];
const notes = [];
const cliEnv = {
  ...process.env,
  HOME: home,
  ANTHROPIC_BASE_URL: `http://127.0.0.1:${PORT}`,
  ANTHROPIC_MODEL: MODEL,
  DISABLE_TELEMETRY: '1',
  DISABLE_AUTOUPDATER: '1',
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
};

/** Runs one container's runner to its exit; resolves with the session it used. */
function runContainer(spec, sessionId) {
  return new Promise((resolve, reject) => {
    for (const f of fs.readdirSync(IPC_INPUT_DIR)) fs.rmSync(path.join(IPC_INPUT_DIR, f), { force: true });
    if (spec.leftover) writeInput(...spec.leftover);
    behindTeam = spec.behindTeam ?? null;
    const mine = [];
    // The SDK results' subtypes, in order, read off the runner's log.
    const subtypes = [];
    let session = sessionId;

    const homeDir = spec.freshHome ? seededHome() : spec.sameHome ? lastHome : home;
    lastHome = homeDir;
    const env = { ...cliEnv, HOME: homeDir };
    if (spec.noReplay) env.PATH = `${nodeWithoutReplay()}:${env.PATH}`;
    const runner = spawn(process.execPath, [RUNNER], { env, stdio: ['pipe', 'pipe', 'pipe'] });
    runnerPid = runner.pid;
    runner.stdin.end(JSON.stringify({
      prompt: spec.prompt,
      promptRef: spec.promptRef,
      ...(spec.resume ? { sessionId } : {}),
      ...(spec.scheduled ? { isScheduledTask: true } : {}),
      groupFolder: 'probe',
      chatJid: 'probe@g.us',
      isMain: true,
      // The CLI words a usage-limit refusal only for a subscription token.
      secrets: spec.subscription
        ? { CLAUDE_CODE_OAUTH_TOKEN: 'offline-probe-token' }
        : { ANTHROPIC_API_KEY: 'offline-probe-key' },
    }));

    let out = '';
    runner.stdout.on('data', (d) => {
      out += d;
      let start;
      while ((start = out.indexOf(OUTPUT_START)) !== -1) {
        const end = out.indexOf(OUTPUT_END, start);
        if (end === -1) break;
        const output = JSON.parse(out.slice(start + OUTPUT_START.length, end).trim());
        out = out.slice(end + OUTPUT_END.length);
        session = output.newSessionId ?? session;
        const n = mine.length + 1;
        mine.push({
          case: spec.cases[n - 1] ?? 'unexpected',
          status: output.status,
          result: output.result,
          turn: output.turn ?? null,
          ...(output.attribution ? { attribution: output.attribution } : {}),
        });
        log('OUTPUT', JSON.stringify(mine[n - 1]));
        if (spec.after[n]) setTimeout(spec.after[n], 500);
      }
    });
    let err = '';
    runner.stderr.on('data', (d) => {
      err += d;
      let i;
      while ((i = err.indexOf('\n')) !== -1) {
        const line = err.slice(0, i);
        err = err.slice(i + 1);
        if (process.env.PROBE_VERBOSE) log(line);
        const result = line.match(/Result #\d+: subtype=(\S+)/);
        if (result) subtypes.push(result[1]);
        if (/Input no result answered|Turn attribution off/.test(line)) {
          notes.push(line.replace(/^\[agent-runner\] /, ''));
        }
      }
    });
    runner.on('exit', (code) => {
      log('runner exit', code);
      let next = 0;
      mine.forEach((o, i) => {
        o.subtype = spec.notResults?.includes(i + 1) ? null : (subtypes[next++] ?? null);
      });
      outputs.push(...mine);
      if (code === (spec.exitCode ?? 0) && mine.length === spec.cases.length) resolve(session);
      else reject(new Error(`runner exit ${code} after ${mine.length} of ${spec.cases.length} outputs`));
    });
  });
}

/** The runner's MessageStream: user messages pushed into a live query. */
class PushStream {
  queue = [];
  wake = null;
  done = false;
  push(content, uuid) {
    this.queue.push({
      type: 'user', message: { role: 'user', content }, parent_tool_use_id: null, session_id: '', uuid,
    });
    this.wake?.();
  }
  end() {
    this.done = true;
    this.wake?.();
  }
  async *[Symbol.asyncIterator]() {
    while (true) {
      while (this.queue.length > 0) yield this.queue.shift();
      if (this.done) return;
      await new Promise((r) => (this.wake = r));
    }
  }
}

/**
 * One push, a second under the same uuid once the first is answered, then a
 * fresh one, all with the runner's replay flag. Records the pushes, the
 * acknowledgements and the results in order, uuids named u1, u2, and which
 * markers ever reached the fake API.
 */
async function reusedUuidCase() {
  const { query } = await import(SDK_MODULE);
  const stream = new PushStream();
  const events = [];
  const names = new Map();
  const name = (uuid) => names.get(uuid) ?? 'an unknown uuid';
  const markerOf = (t) => (t.match(/PROBE-[A-Z]+/) || ['none'])[0];
  const record = (event) => {
    events.push(event);
    log('REUSED', event);
  };
  const push = (content, uuid) => {
    if (!names.has(uuid)) names.set(uuid, `u${names.size + 1}`);
    record(`push ${markerOf(content)} as ${name(uuid)}`);
    stream.push(content, uuid);
  };
  modelSaw = new Set();
  const first = randomUUID();
  push('PROBE-FIRST the first push', first);
  for await (const m of query({
    prompt: stream,
    options: {
      cwd: fs.mkdtempSync('/tmp/probe-reused-'),
      // A home of its own: the team the runner's cases made stays out of it.
      env: { ...cliEnv, HOME: fs.mkdtempSync('/tmp/probe-reused-home-'), ANTHROPIC_API_KEY: 'offline-probe-key' },
      extraArgs: { 'replay-user-messages': null },
      permissionMode: 'bypassPermissions',
      allowDangerouslySkipPermissions: true,
    },
  })) {
    if (m.type === 'user' && m.isReplay) {
      record(`ack ${markerOf(textOf(m.message?.content))} as ${name(m.uuid)}`);
    }
    if (m.type !== 'result') continue;
    record(`result ${m.subtype === 'success' ? m.result : m.subtype}`);
    if (m.result === 'ANSWER PROBE-FIRST') {
      push('PROBE-REUSED pushed under the uuid the CLI has seen', first);
      // Ample time for a turn the fake API answers at once to show up.
      setTimeout(() => push('PROBE-FRESH pushed under a fresh uuid', randomUUID()), 3000);
    } else if (m.result === 'ANSWER PROBE-FRESH') {
      stream.end();
    }
  }
  const saw = [...modelSaw].sort();
  modelSaw = null;
  return { events, modelSaw: saw };
}

/**
 * The prompt the CLI injects when its input closes while teammates are up,
 * read at its injection site in the bundle: the constant passed there.
 */
function cliShutdownPrompt() {
  const bundle = fs.readFileSync(SDK_CLI, 'utf-8');
  const site = bundle.match(/injecting shutdown prompt"\),[\w$]+\(\{mode:"prompt",value:([\w$]+)/);
  if (!site) return null;
  const name = site[1].replace(/\$/g, '\\$');
  const literal = bundle.match(new RegExp(`(?:^|[^\\w$])${name}=\`([^\`]*)\``));
  return literal?.[1] ?? null;
}

server.listen(PORT, '127.0.0.1', async () => {
  let reusedUuid;
  const timer = setTimeout(() => {
    log('TIMEOUT; outputs so far:', JSON.stringify(outputs));
    process.exit(2);
  }, TIMEOUT_MS);
  try {
    fs.mkdirSync(IPC_INPUT_DIR, { recursive: true });
    let session;
    for (const spec of CONTAINERS) session = await runContainer(spec, session);
    reusedUuid = await reusedUuidCase();
  } catch (err) {
    log('FAILED:', err.message, '; outputs so far:', JSON.stringify(outputs));
    process.exit(1);
  }
  clearTimeout(timer);
  const sdkVersion = JSON.parse(fs.readFileSync(SDK_PACKAGE, 'utf-8')).version;
  // The runner source this compiled (mounted at /app/src) and this probe.
  const hash = sourceHash({ src: '/app/src', probe: path.dirname(new URL(import.meta.url).pathname) });
  const fixture = { sdkVersion, sourceHash: hash, outputs, runnerNotes: notes, reusedUuid, cliShutdownPrompt: cliShutdownPrompt() };
  process.stdout.write(JSON.stringify(fixture, null, 2) + '\n');
  server.close();
  process.exit(0);
});
