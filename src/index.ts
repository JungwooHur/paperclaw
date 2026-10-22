import fs from 'fs';
import { spawn } from 'child_process';
import os from 'os';
import path from 'path';

import {
  ASSISTANT_NAME,
  IDLE_TIMEOUT,
  MAIN_GROUP_FOLDER,
  POLL_INTERVAL,
  TRIGGER_PATTERN,
} from './config.js';
import { warnIfRunningStaleBuild } from './build-freshness.js';
import { WhatsAppChannel } from './channels/whatsapp.js';
import {
  ContainerOutput,
  runContainerAgent,
  writeGroupsSnapshot,
  writeTasksSnapshot,
} from './container-runner.js';
import {
  cleanupOrphans,
  ensureContainerRuntimeRunning,
} from './container-runtime.js';
import {
  getAllChats,
  getAllRegisteredGroups,
  getAllSessions,
  getAllTasks,
  getMessagesSince,
  getNewMessages,
  getRouterState,
  initDatabase,
  setRegisteredGroup,
  setRouterState,
  setSession,
  deleteSession,
  storeChatMetadata,
  storeMessage,
} from './db.js';
import { GroupQueue } from './group-queue.js';
import { resolveGroupFolderPath } from './group-folder.js';
import { startIpcWatcher } from './ipc.js';
import { findChannel, formatMessages, formatOutbound } from './router.js';
import { SchedulerDependencies, startSchedulerLoop } from './task-scheduler.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';
import { logHeartbeat } from './liveness-log.js';
import { logger } from './logger.js';
import { qaFollowupCommand, worthFilingCheck } from './qa-followup.js';
import {
  closeUsageLimitOnAnswer,
  isUsageLimit,
  recordUsageLimit,
  usageLimitHeldUntil,
} from './usage-limit.js';

// Re-export for backwards compatibility during refactor
export { escapeXml, formatMessages } from './router.js';

let lastTimestamp = '';
let sessions: Record<string, string> = {};
let registeredGroups: Record<string, RegisteredGroup> = {};
let lastAgentTimestamp: Record<string, string> = {};
let messageLoopRunning = false;

let whatsapp: WhatsAppChannel;
const channels: Channel[] = [];
const queue = new GroupQueue();
let usageLimitRecoveryTimer: ReturnType<typeof setTimeout> | null = null;
/** A prompt a message container was given and has not finished a turn on. */
interface OpenPrompt {
  /** The cursor at the end of its messages. */
  cursor: string;
  /** The input file it was piped in as; the first prompt has none. */
  file?: string;
  /**
   * The id the runner reports it by in a result's `turn`: the cursor range
   * its messages cover (inputRef). Missing only from a record an older
   * process saved.
   */
  ref?: string;
  /** A result's `turn` named it, but an older prompt is still open. */
  answered?: boolean;
}

/**
 * The input id of the messages after cursor `from`, up to cursor `to`. It
 * names its own range, so an id from a file an earlier container left, which
 * the next container drains at start-up, is still recognised.
 */
function inputRef(from: string, to: string): string {
  return `${from}|${to}`;
}

/** The cursor range an input id names, or null for an id that is no range. */
function refRange(ref: string): { from: string; to: string } | null {
  const bar = ref.indexOf('|');
  if (bar < 0) return null;
  return { from: ref.slice(0, bar), to: ref.slice(bar + 1) };
}

/**
 * Drops the answered prompts off the front of `prompts`. Returns the cursor
 * at the end of the last one dropped, or undefined when none was.
 */
function shiftAnswered(prompts: OpenPrompt[]): string | undefined {
  let cursor: string | undefined;
  while (prompts.length > 0 && prompts[0].answered) {
    cursor = prompts.shift()!.cursor;
  }
  return cursor;
}

/**
 * Marks the prompts a result's turn names answered. Returns the ids it names
 * that are no open prompt of this container: a file an earlier container
 * left, drained into this one's first prompt, answers no prompt here.
 */
function markAnswered(prompts: OpenPrompt[], inputs: string[]): string[] {
  const unknown: string[] = [];
  for (const id of inputs) {
    const prompt = prompts.find((p) => p.ref === id);
    if (prompt) prompt.answered = true;
    else unknown.push(id);
  }
  return unknown;
}

/**
 * Where a container's exit can put the cursor back to: the start of each
 * prompt no result answered, oldest first, whether the container took it or
 * never drained its file. Left on disk, that file would wait for the group's
 * next container, which starts only on the user's next message; the put-back
 * deletes it, so the retry delivers its messages once. Call only once the
 * container has exited, so that file can no longer go.
 */
function unansweredStarts(prompts: OpenPrompt[]): string[] {
  const starts: string[] = [];
  for (const prompt of prompts) {
    if (prompt.answered) continue;
    const range = prompt.ref ? refRange(prompt.ref) : null;
    if (range) starts.push(range.from);
  }
  return starts;
}
// Per group with a message container running: its open prompts, oldest first.
const openPrompts: Record<string, OpenPrompt[]> = {};
// Groups whose message container is closing on a usage-limit refusal, until it
// exits. That can outlast the window, so the window alone does not cover it.
const closingOnRefusal = new Set<string>();
// router_state key: per group, the piped prompts the latest usage-limit
// refusal put back, so a restart before the container exits can still drop
// the files it left. Cleared once the files are dealt with.
const REFUSED_INPUT_KEY = 'usage_limit_refused_input';
// router_state key: per group, the cursor a container's exit last put back to
// for a prompt no result answered, until an exit leaves nothing unanswered.
// When the oldest prompt the next exit finds unanswered starts there, the
// retry failed it the same way: that prompt is not put back a third time,
// but a newer one is. Persisted, so a restart in between does not buy it
// another run.
const PUT_BACK_KEY = 'turn_put_back';

/**
 * Files a paper Q&A right after the answer goes out, so it does not wait for the
 * five-minute backstop. Deliberately fire-and-forget: the reply has already been
 * sent, the timer is still the safety net, and a failure here must never turn
 * into a failed message.
 */
function fileAnsweredQa(chatJid: string, reply: string): void {
  if (!worthFilingCheck(reply)) return;
  const { cmd, args } = qaFollowupCommand({
    projectRoot: process.cwd(),
    chatJid,
    lockFile: path.join(os.tmpdir(), 'paperclaw-auto-save-qa.lock'),
  });
  try {
    const child = spawn(cmd, args, { detached: true, stdio: 'ignore' });
    child.on('error', (err: Error) =>
      logger.warn({ err: err.message }, 'qa follow-up could not start'),
    );
    child.unref();
  } catch (err) {
    logger.warn({ err: String(err) }, 'qa follow-up could not start');
  }
}

function loadState(): void {
  lastTimestamp = getRouterState('last_timestamp') || '';
  const agentTs = getRouterState('last_agent_timestamp');
  try {
    lastAgentTimestamp = agentTs ? JSON.parse(agentTs) : {};
  } catch {
    logger.warn('Corrupted last_agent_timestamp in DB, resetting');
    lastAgentTimestamp = {};
  }
  sessions = getAllSessions();
  registeredGroups = getAllRegisteredGroups();
  logger.info(
    { groupCount: Object.keys(registeredGroups).length },
    'State loaded',
  );
}

function saveState(): void {
  setRouterState('last_timestamp', lastTimestamp);
  setRouterState('last_agent_timestamp', JSON.stringify(lastAgentTimestamp));
}

function registerGroup(jid: string, group: RegisteredGroup): void {
  let groupDir: string;
  try {
    groupDir = resolveGroupFolderPath(group.folder);
  } catch (err) {
    logger.warn(
      { jid, folder: group.folder, err },
      'Rejecting group registration with invalid folder',
    );
    return;
  }

  registeredGroups[jid] = group;
  setRegisteredGroup(jid, group);

  // Create group folder
  fs.mkdirSync(path.join(groupDir, 'logs'), { recursive: true });

  logger.info(
    { jid, name: group.name, folder: group.folder },
    'Group registered',
  );
}

/**
 * Get available groups list for the agent.
 * Returns groups ordered by most recent activity.
 */
export function getAvailableGroups(): import('./container-runner.js').AvailableGroup[] {
  const chats = getAllChats();
  const registeredJids = new Set(Object.keys(registeredGroups));

  return chats
    .filter((c) => c.jid !== '__group_sync__' && c.is_group)
    .map((c) => ({
      jid: c.jid,
      name: c.name,
      lastActivity: c.last_message_time,
      isRegistered: registeredJids.has(c.jid),
    }));
}

/** @internal - exported for testing */
export function _setRegisteredGroups(
  groups: Record<string, RegisteredGroup>,
): void {
  registeredGroups = groups;
}

/** @internal - exported for testing */
export function _setChannels(list: Channel[]): void {
  channels.splice(0, channels.length, ...list);
}

/**
 * Process all pending messages for a group.
 * Called by the GroupQueue when it's this group's turn.
 */
async function processGroupMessages(chatJid: string): Promise<boolean> {
  const group = registeredGroups[chatJid];
  if (!group) return true;

  const channel = findChannel(channels, chatJid);
  if (!channel) {
    logger.warn({ chatJid }, 'No channel owns JID, skipping messages');
    return true;
  }

  const sinceTimestamp = lastAgentTimestamp[chatJid] || '';
  const missedMessages = getMessagesSince(
    chatJid,
    sinceTimestamp,
    ASSISTANT_NAME,
  );

  if (missedMessages.length === 0) return true;
  if (!startsAgent(group, missedMessages)) return true;

  // A usage-limit window is open: leave the messages pending rather than start
  // a container the limit would refuse. They run when the window closes.
  const heldUntil = usageLimitHeldUntil();
  if (heldUntil) {
    logger.info(
      { group: group.name, messageCount: missedMessages.length, heldUntil },
      'Usage limit window open, holding messages',
    );
    resumeAfterUsageLimit(heldUntil);
    return true;
  }

  const prompt = formatMessages(missedMessages);

  // Advance cursor so the piping path in startMessageLoop won't re-fetch
  // these messages. Save the old cursor so we can roll back on error.
  const previousCursor = lastAgentTimestamp[chatJid] || '';
  lastAgentTimestamp[chatJid] =
    missedMessages[missedMessages.length - 1].timestamp;
  saveState();

  logger.info(
    { group: group.name, messageCount: missedMessages.length },
    'Processing messages',
  );

  // Track idle timer for closing stdin when agent is idle
  let idleTimer: ReturnType<typeof setTimeout> | null = null;

  const resetIdleTimer = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      logger.debug(
        { group: group.name },
        'Idle timeout, closing container stdin',
      );
      queue.closeStdin(chatJid);
    }, IDLE_TIMEOUT);
  };

  await channel.setTyping?.(chatJid, true);
  let hadError = false;
  let outputSentToUser = false;
  let usageLimited = false;
  // The cursor at the end of the last prompt a turn finished on: a usage limit
  // after it puts back only the messages that came later. Not the cursor at
  // the time, which a follow-up piped in mid-answer has already moved on.
  let answeredCursor = previousCursor;
  const promptRef = inputRef(previousCursor, lastAgentTimestamp[chatJid]);
  const prompts: OpenPrompt[] = [
    { cursor: lastAgentTimestamp[chatJid], ref: promptRef },
  ];
  openPrompts[chatJid] = prompts;
  // This container was never refused, whatever the last run left behind.
  closingOnRefusal.delete(chatJid);
  // Set once runAgent returns. The runner waits for these callbacks first, but
  // only so long (OUTPUT_SETTLE_TIMEOUT), so one can still run after. By then
  // the end below has settled the cursor and the held input, and the group's
  // stdin, idle timer and pipes belong to its next container: a late callback
  // delivers what it carries and writes none of them.
  let runEnded = false;
  // Output callbacks running now. One still running when the run returns has
  // outlived that wait, and the outputs queued behind it are not read yet.
  let outputsRunning = 0;
  // Whether this container's results say which inputs they answered (`turn`,
  // docs/SPEC.md "Turn Attribution"): unknown until its first result. Off for
  // good on the first output that says so (`attribution: 'off'`, from the one
  // the runner's guard tripped on), or once a result with text comes without
  // `turn`: a runner that predates attribution, or this field. Any other
  // textless output without `turn` is the session marker and says nothing
  // either way. (Cast, or the checks after the run would read it as never
  // reassigned.)
  let attribution = 'unknown' as 'unknown' | 'on' | 'off';

  const onOutput = async (result: ContainerOutput): Promise<void> => {
    // Streaming output callback — called for each agent result
    let closed = false;
    const raw = result.result
      ? typeof result.result === 'string'
        ? result.result
        : JSON.stringify(result.result)
      : null;
    if (!runEnded && attribution !== 'off') {
      if (result.attribution === 'off') attribution = 'off';
      else if (result.turn) attribution = 'on';
      else if (raw !== null) attribution = 'off';
    }
    const turn = attribution === 'on' ? result.turn : undefined;
    if (raw !== null) {
      logger.info({ group: group.name }, `Agent output: ${raw.slice(0, 200)}`);
      if (runEnded) {
        logger.warn(
          { group: group.name },
          'Agent output after its run ended, delivered without state changes',
        );
      }
      const limit = recordUsageLimit(raw);
      if (limit) {
        // The CLI's refusal, not an answer: hold the request for the reset.
        // The refused turn's inputs stay unanswered.
        if (!runEnded) {
          usageLimited = true;
          // Put the unanswered messages back now, not when the container
          // exits: the window is already persisted, so a restart in between
          // would keep a cursor past messages nothing answered, and recovery
          // after the reset would find nothing pending.
          lastAgentTimestamp[chatJid] = answeredCursor;
          saveState();
          rememberRefusedInput(chatJid, prompts);
          closingOnRefusal.add(chatJid);
          queue.closeStdin(chatJid);
        }
        if (limit.notice) await channel.sendMessage(chatJid, limit.notice);
        const resumeAt = usageLimitHeldUntil();
        if (resumeAt) resumeAfterUsageLimit(resumeAt);
        return;
      }
      // A request got through: an open window is over. The held messages it
      // releases run at the end, below.
      closed = closeUsageLimitOnAnswer(raw);
    }
    if (!runEnded && (turn || raw !== null)) {
      if (turn) {
        // Exactly the prompts its turn took, textless or not. A turn only
        // the runner or the CLI started (`internal`, no inputs) settles none.
        const unknown = markAnswered(prompts, turn.inputs);
        if (unknown.length > 0)
          logUnknownInputs(group, previousCursor, unknown);
      } else if (prompts.length > 0) {
        // No `turn`: a result ends a turn, and turns take prompts in the
        // order they were given, so it settles the oldest open prompt. A
        // turn answered only through send_message counts too. Wrong when a
        // follow-up was merged into a turn, or a turn took no prompt (#114).
        prompts[0].answered = true;
      }
      const settled = shiftAnswered(prompts);
      if (settled !== undefined) answeredCursor = settled;
      if (usageLimited) {
        // A turn after a refusal (extra usage, say): save what the exit
        // will, so a restart before it leaves the same messages pending.
        // Without `turn` not an exact cursor: the turn settles the oldest
        // open prompt, which is the refused one, whichever it answered.
        lastAgentTimestamp[chatJid] = answeredCursor;
        saveState();
        rememberRefusedInput(chatJid, prompts);
      }
    }
    if (raw !== null) {
      // Single outbound path: formatOutbound strips <internal> blocks AND the
      // trailing "shall I save this to Notion?" offer. This used to inline only
      // the <internal> strip, so the offer-stripping rule silently did not apply
      // to the agent's main reply — the one place it matters most.
      const text = formatOutbound(raw);
      if (text) {
        await channel.sendMessage(chatJid, text);
        outputSentToUser = true;
        fileAnsweredQa(chatJid, text);
      }
    }
    // A turn ended: the container is idle until its next input. Not on the
    // session marker (result: null, no `turn`), which follows the results.
    // A textless result with `turn` counts: a turn that failed, or one
    // answered only through send_message. Without it, a container whose
    // last turn failed would close only on its hard timeout, and its exit
    // would put the input back only then.
    if (!runEnded && (raw !== null || result.turn)) resetIdleTimer();

    if (!runEnded) {
      if (result.status === 'success') {
        queue.notifyIdle(chatJid);
      }
      if (result.status === 'error') {
        hadError = true;
      }
    }

    // Held messages may go into this container, so they run only once this
    // result is done with it. Before it settles its own prompt, a turn no
    // prompt started (a task notification) would settle theirs, and a refusal
    // of their turn would put back nothing. After notifyIdle, a task waiting
    // on this container has closed it, and they wait for the next one
    // instead of holding the task up for another turn.
    if (closed) usageLimitLifted();
  };
  const output = await runAgent(
    group,
    prompt,
    chatJid,
    promptRef,
    async (result) => {
      outputsRunning++;
      try {
        await onOutput(result);
      } finally {
        outputsRunning--;
      }
    },
  );
  runEnded = true;
  // Results the run returned without: what they answered is unknown.
  const outputsLate = outputsRunning > 0;

  await channel.setTyping?.(chatJid, false);
  if (idleTimer) clearTimeout(idleTimer);
  // Only after the last await: the group's queue counts the container active
  // until this run returns, so a pipe while the typing indicator clears still
  // writes a file. It must land in prompts, or be refused after a refusal.
  if (openPrompts[chatJid] === prompts) delete openPrompts[chatJid];
  closingOnRefusal.delete(chatJid);

  if (usageLimited) {
    // The unanswered messages are back (since the refusal). No retry: it
    // would hit the same limit; they run when the window closes. The
    // container is gone, so the files it never drained can go too.
    lastAgentTimestamp[chatJid] = answeredCursor;
    saveState();
    discardRefusedInput(chatJid, prompts);
    rememberRefusedInput(chatJid, []);
    logger.warn(
      { group: group.name },
      'Usage limit reached, messages held until the reset',
    );
    return true;
  }

  if (attribution === 'on' && outputsLate) {
    // An answer still on its way would be sent twice if its prompt went back.
    logger.warn(
      { group: group.name },
      'Container exited with output still being delivered, putting nothing back',
    );
  } else if (attribution === 'on') {
    // Every result said what it answered, so the exit knows what was not:
    // a prompt no result answered goes back, taken or still in its file, on
    // an error or not, and the queue retries it. A duplicate beats a silent
    // loss. Whatever came after it goes back too, answered or not: that
    // reruns an answered follow-up behind an unanswered prompt (#114).
    const starts = unansweredStarts(prompts);
    const putBack = readPutBack();
    if (starts.length === 0) {
      if (chatJid in putBack) rememberPutBack(chatJid, undefined);
      return true;
    }
    if (starts[0] === putBack[chatJid]) {
      // Put back once already, and its retry answered it neither: a prompt
      // that fails every time would otherwise run on, and take every later
      // message along. It is given up. A newer prompt never went back, so
      // it still does; with none, the exit ends as it did before
      // attribution, below.
      logger.error(
        { group: group.name, from: starts[0] },
        'Input no result answered again after it was put back, not putting it back again',
      );
      starts.shift();
    }
    const from = starts[0];
    if (from !== undefined) {
      lastAgentTimestamp[chatJid] = from;
      saveState();
      rememberPutBack(chatJid, from);
      discardRefusedInput(chatJid, prompts);
      logger.warn(
        { group: group.name, error: output === 'error' || hadError },
        'Container exited with input no result answered, putting it back for retry',
      );
      return false;
    }
  }

  if (output === 'error' || hadError) {
    // If we already sent output to the user, don't roll back the cursor —
    // the user got their response and re-processing would send duplicates.
    if (outputSentToUser) {
      logger.warn(
        { group: group.name },
        'Agent error after output was sent, skipping cursor rollback to prevent duplicates',
      );
      return true;
    }
    // Roll back cursor so retries can re-process these messages. That makes
    // every piped one pending again, so the files the container never drained
    // go: the retry would get them twice. Saved first, so a crash in between
    // cannot leave a message neither pending nor in a file.
    lastAgentTimestamp[chatJid] = previousCursor;
    saveState();
    discardRefusedInput(chatJid, prompts);
    logger.warn(
      { group: group.name },
      'Agent error, rolled back message cursor for retry',
    );
    return false;
  }

  return true;
}

/**
 * A result's turn named inputs that are no open prompt of its container. One
 * whose range ends at or before the cursor the container started from is a
 * file an earlier container left, drained into this one's first prompt: its
 * messages are already behind the cursor. Neither settles anything.
 */
function logUnknownInputs(
  group: RegisteredGroup,
  startCursor: string,
  ids: string[],
): void {
  for (const id of ids) {
    const range = refRange(id);
    if (range && range.to <= startCursor) {
      logger.debug(
        { group: group.name, input: id },
        'Turn answered a file an earlier container left',
      );
    } else {
      logger.warn(
        { group: group.name, input: id },
        'Turn names an input this container was never given',
      );
    }
  }
}

async function runAgent(
  group: RegisteredGroup,
  prompt: string,
  chatJid: string,
  promptRef: string,
  onOutput?: (output: ContainerOutput) => Promise<void>,
): Promise<'success' | 'error'> {
  const isMain = group.folder === MAIN_GROUP_FOLDER;
  const sessionId = sessions[group.folder];

  // Update tasks snapshot for container to read (filtered by group)
  const tasks = getAllTasks();
  writeTasksSnapshot(
    group.folder,
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

  // Update available groups snapshot (main group only can see all groups)
  const availableGroups = getAvailableGroups();
  writeGroupsSnapshot(
    group.folder,
    isMain,
    availableGroups,
    new Set(Object.keys(registeredGroups)),
  );

  // Wrap onOutput to track session ID from streamed results.
  // Skip when status === 'error' — the agent-runner echoes back the (broken)
  // input sessionId on error, persisting it creates an infinite resume-fail
  // loop where claude can't load a non-existent session and keeps crashing.
  // Skip too once the run hit the usage limit: that session holds nothing but
  // the refused request, and the group keeps the one it had. And skip once the
  // run has returned: a callback that outlived OUTPUT_SETTLE_TIMEOUT would
  // overwrite the session a later run of the group has stored since.
  let usageLimited = false;
  let returned = false;
  const wrappedOnOutput = onOutput
    ? async (output: ContainerOutput) => {
        if (typeof output.result === 'string' && isUsageLimit(output.result)) {
          usageLimited = true;
        }
        if (
          output.newSessionId &&
          output.status !== 'error' &&
          !usageLimited &&
          !returned
        ) {
          sessions[group.folder] = output.newSessionId;
          setSession(group.folder, output.newSessionId);
        }
        await onOutput(output);
      }
    : undefined;

  try {
    const output = await runContainerAgent(
      group,
      {
        prompt,
        sessionId,
        groupFolder: group.folder,
        chatJid,
        isMain,
        assistantName: ASSISTANT_NAME,
        promptRef,
      },
      (proc, containerName) =>
        queue.registerProcess(chatJid, proc, containerName, group.folder),
      wrappedOnOutput,
    ).finally(() => {
      returned = true;
    });

    if (output.newSessionId && output.status !== 'error' && !usageLimited) {
      sessions[group.folder] = output.newSessionId;
      setSession(group.folder, output.newSessionId);
    } else if (output.status === 'error' && !usageLimited) {
      // Drop the stored session ID so the next message starts fresh.
      // Without this, a single bad session (e.g. claude failed to resume
      // because projects/<id>/ was removed) infinitely repeats: orchestrator
      // resends old ID → claude crashes → agent echoes the same ID back.
      // Not after a refusal: the CLI exits 1 after an error result once stdin
      // closes, which is no sign of a bad session, and the group keeps its own.
      delete sessions[group.folder];
      deleteSession(group.folder);
    }

    if (output.status === 'error') {
      logger.error(
        { group: group.name, error: output.error },
        'Container agent error',
      );
      return 'error';
    }

    return 'success';
  } catch (err) {
    logger.error({ group: group.name, err }, 'Agent error');
    return 'error';
  }
}

async function startMessageLoop(): Promise<void> {
  if (messageLoopRunning) {
    logger.debug('Message loop already running, skipping duplicate start');
    return;
  }
  messageLoopRunning = true;

  logger.info(`PaperClaw running (trigger: @${ASSISTANT_NAME})`);

  while (true) {
    try {
      pollNewMessages();
    } catch (err) {
      logger.error({ err }, 'Error in message loop');
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL));
  }
}

/** One pass of the message loop: hands new messages to their groups. */
function pollNewMessages(): void {
  const jids = Object.keys(registeredGroups);
  const { messages, newTimestamp } = getNewMessages(
    jids,
    lastTimestamp,
    ASSISTANT_NAME,
  );

  if (messages.length > 0) {
    logger.info({ count: messages.length }, 'New messages');

    // Advance the "seen" cursor for all messages immediately
    lastTimestamp = newTimestamp;
    saveState();

    // Deduplicate by group
    const messagesByGroup = new Map<string, NewMessage[]>();
    for (const msg of messages) {
      const existing = messagesByGroup.get(msg.chat_jid);
      if (existing) {
        existing.push(msg);
      } else {
        messagesByGroup.set(msg.chat_jid, [msg]);
      }
    }

    for (const [chatJid, groupMessages] of messagesByGroup) {
      const group = registeredGroups[chatJid];
      if (!group) continue;

      const channel = findChannel(channels, chatJid);
      if (!channel) {
        logger.warn({ chatJid }, 'No channel owns JID, skipping messages');
        continue;
      }

      // Non-trigger messages accumulate in DB and get pulled as
      // context when a trigger eventually arrives.
      if (!startsAgent(group, groupMessages)) continue;

      // Pull all messages since lastAgentTimestamp so non-trigger
      // context that accumulated between triggers is included.
      const allPending = getMessagesSince(
        chatJid,
        lastAgentTimestamp[chatJid] || '',
        ASSISTANT_NAME,
      );
      // None: the cursor is already past these. Recovery piped them, or a
      // container took them at start-up, before this poll saw them.
      if (allPending.length === 0) continue;

      deliverMessages(chatJid, channel, allPending);
    }
  }
}

/**
 * Whether these messages start the agent: any message in the main group, or
 * in a group that needs no trigger; elsewhere only one with the trigger.
 */
function startsAgent(group: RegisteredGroup, messages: NewMessage[]): boolean {
  if (group.folder === MAIN_GROUP_FOLDER || group.requiresTrigger === false) {
    return true;
  }
  return messages.some((m) => TRIGGER_PATTERN.test(m.content.trim()));
}

/**
 * Hands a group's pending messages to its running container, or queues a
 * check that starts one when no container takes them. A check for a group
 * whose container is running only marks it, and that container takes the
 * messages only once it closes, on idle after half an hour: so the message
 * loop and recovery both deliver this way.
 */
function deliverMessages(
  chatJid: string,
  channel: Channel,
  messages: NewMessage[],
): void {
  if (!pipeToActiveContainer(chatJid, channel, messages)) {
    queue.enqueueMessageCheck(chatJid);
  }
}

/**
 * Hands messages to the group's running container, if it has one, and moves
 * the cursor past them. Returns false when no container is running.
 */
function pipeToActiveContainer(
  chatJid: string,
  channel: Channel,
  messages: NewMessage[],
): boolean {
  // Nothing into a container closing on a refusal: it checks the close
  // sentinel before draining, so the file would be left for the next one,
  // on top of the messages being pending. Pending is enough; they are held
  // while the window is open, and start the next container once it closes.
  if (usageLimitHeldUntil() || closingOnRefusal.has(chatJid)) return false;
  const to = messages[messages.length - 1].timestamp;
  const ref = inputRef(lastAgentTimestamp[chatJid] || '', to);
  const file = queue.sendMessage(chatJid, formatMessages(messages), ref);
  if (!file) return false;
  logger.debug(
    { chatJid, count: messages.length },
    'Piped messages to active container',
  );
  lastAgentTimestamp[chatJid] = to;
  saveState();
  openPrompts[chatJid]?.push({ cursor: to, file, ref });
  // Show typing indicator while the container processes the piped message
  channel
    .setTyping?.(chatJid, true)
    ?.catch((err) =>
      logger.warn({ chatJid, err }, 'Failed to set typing indicator'),
    );
  return true;
}

/**
 * Deletes the input files of piped prompts whose messages are pending again,
 * so the container that runs them next does not also drain them.
 * Only those: a file whose message the cursor has passed can be its only
 * delivery (a container that closed before draining it leaves it for the next
 * one's start-up drain). Call only once the group's container has exited.
 */
function discardRefusedInput(chatJid: string, prompts: OpenPrompt[]): void {
  const cursor = lastAgentTimestamp[chatJid] || '';
  for (const { file, cursor: end } of prompts) {
    if (!file || end <= cursor) continue;
    try {
      fs.rmSync(file, { force: true });
    } catch (err) {
      logger.warn({ chatJid, err }, 'Could not discard refused input file');
    }
  }
}

function readPutBack(): Record<string, string> {
  try {
    return JSON.parse(getRouterState(PUT_BACK_KEY) || '{}');
  } catch {
    return {};
  }
}

/** Persists where a group's exit put back to (undefined: forget the group). */
function rememberPutBack(chatJid: string, from: string | undefined): void {
  const all = readPutBack();
  if (from === undefined) delete all[chatJid];
  else all[chatJid] = from;
  setRouterState(PUT_BACK_KEY, JSON.stringify(all));
}

/** Persists the piped prompts a refusal put back (none: forget the group). */
function rememberRefusedInput(chatJid: string, prompts: OpenPrompt[]): void {
  const all = readRefusedInput();
  const piped = prompts.filter((p) => p.file);
  if (piped.length > 0) all[chatJid] = piped;
  else delete all[chatJid];
  setRouterState(REFUSED_INPUT_KEY, JSON.stringify(all));
}

function readRefusedInput(): Record<string, OpenPrompt[]> {
  try {
    return JSON.parse(getRouterState(REFUSED_INPUT_KEY) || '{}');
  } catch {
    return {};
  }
}

/**
 * Startup: a restart between a refusal and its container's exit leaves the
 * files that container never drained. Runs after orphaned containers are
 * stopped and state is loaded, before any container starts.
 */
function discardRefusedInputAfterRestart(): void {
  for (const [chatJid, prompts] of Object.entries(readRefusedInput())) {
    discardRefusedInput(chatJid, prompts);
  }
  setRouterState(REFUSED_INPUT_KEY, '{}');
}

/** Runs recovery again once a usage-limit window has closed. */
function resumeAfterUsageLimit(heldUntil: Date): void {
  if (usageLimitRecoveryTimer) clearTimeout(usageLimitRecoveryTimer);
  // Capped to what setTimeout can hold; if it fires early, recovery re-arms.
  const delay = Math.min(heldUntil.getTime() - Date.now(), 2 ** 31 - 1);
  usageLimitRecoveryTimer = setTimeout(() => {
    usageLimitRecoveryTimer = null;
    recoverPendingMessages();
  }, delay);
}

/**
 * An answer closed the usage-limit window: run the held messages now, not at
 * the reset the window had. If the limit still holds, the first of them is
 * refused and reopens the window without a second notice. Only a container
 * already running when it reopens can answer into it, so this cannot repeat
 * on its own.
 */
function usageLimitLifted(): void {
  if (usageLimitRecoveryTimer) {
    clearTimeout(usageLimitRecoveryTimer);
    usageLimitRecoveryTimer = null;
  }
  logger.info('Usage limit window closed by an answer, resuming held work');
  recoverPendingMessages();
}

/**
 * A scheduled run changed the usage-limit window. Held messages follow it: a
 * refusal can state an earlier reset than the one recovery is armed for, and
 * an answer that closed the window runs them now.
 */
function usageLimitChanged(): void {
  if (usageLimitHeldUntil()) recoverPendingMessages();
  else usageLimitLifted();
}

/**
 * Startup recovery: check for unprocessed messages in registered groups.
 * Handles crash between advancing lastTimestamp and processing messages.
 * While a usage-limit window is open it only arms itself for the window's end,
 * which is also how held messages get picked up after the reset. Messages a
 * window held while the group's container kept running go into that
 * container, as the message loop would have put them.
 */
function recoverPendingMessages(): void {
  const heldUntil = usageLimitHeldUntil();
  if (heldUntil) {
    logger.info({ heldUntil }, 'Usage limit window open, recovery deferred');
    resumeAfterUsageLimit(heldUntil);
    return;
  }
  for (const [chatJid, group] of Object.entries(registeredGroups)) {
    const sinceTimestamp = lastAgentTimestamp[chatJid] || '';
    const pending = getMessagesSince(chatJid, sinceTimestamp, ASSISTANT_NAME);
    if (pending.length > 0) {
      logger.info(
        { group: group.name, pendingCount: pending.length },
        'Recovery: found unprocessed messages',
      );
      const channel = findChannel(channels, chatJid);
      if (channel && startsAgent(group, pending)) {
        deliverMessages(chatJid, channel, pending);
      } else {
        queue.enqueueMessageCheck(chatJid);
      }
    }
  }
}

/**
 * Startup, after the database opens and before any container starts, in the
 * order the refused-input sweep needs: stop the last process's containers, so
 * none can still drain a file; load the cursors the files are judged against;
 * then drop the files a refusal put back.
 */
function restoreStartupState(): void {
  cleanupOrphans();
  loadState();
  discardRefusedInputAfterRestart();
}

/** What the scheduler is started with: this process's queue and channels. */
function schedulerDependencies(): SchedulerDependencies {
  return {
    registeredGroups: () => registeredGroups,
    getSessions: () => sessions,
    queue,
    onProcess: (groupJid, proc, containerName, groupFolder) =>
      queue.registerProcess(groupJid, proc, containerName, groupFolder),
    sendMessage: async (jid, rawText) => {
      const channel = findChannel(channels, jid);
      if (!channel) {
        logger.warn({ jid }, 'No channel owns JID, cannot send message');
        return;
      }
      const text = formatOutbound(rawText);
      if (text) await channel.sendMessage(jid, text);
    },
    onUsageLimitChanged: usageLimitChanged,
  };
}

/** @internal - exported for testing */
export {
  loadState as _loadState,
  pipeToActiveContainer as _pipeToActiveContainer,
  pollNewMessages as _pollNewMessages,
  processGroupMessages as _processGroupMessages,
  recoverPendingMessages as _recoverPendingMessages,
  restoreStartupState as _restoreStartupState,
  schedulerDependencies as _schedulerDependencies,
};

async function main(): Promise<void> {
  // The unit rebuilds dist/ before each start but lets a failed build through,
  // so say so here when that left this process on old code.
  warnIfRunningStaleBuild(import.meta.url);

  ensureContainerRuntimeRunning();
  initDatabase();
  logger.info('Database initialized');
  restoreStartupState();

  // Graceful shutdown handlers
  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'Shutdown signal received');
    await queue.shutdown(10000);
    for (const ch of channels) await ch.disconnect();
    process.exit(0);
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  // Channel callbacks (shared by all channels)
  const channelOpts = {
    onMessage: (_chatJid: string, msg: NewMessage) => storeMessage(msg),
    onChatMetadata: (
      chatJid: string,
      timestamp: string,
      name?: string,
      channel?: string,
      isGroup?: boolean,
    ) => storeChatMetadata(chatJid, timestamp, name, channel, isGroup),
    registeredGroups: () => registeredGroups,
  };

  // Create and connect channels
  whatsapp = new WhatsAppChannel(channelOpts);
  channels.push(whatsapp);
  await whatsapp.connect();

  // Start subsystems (independently of connection handler)
  startSchedulerLoop(schedulerDependencies());
  startIpcWatcher({
    sendMessage: (jid, text) => {
      const channel = findChannel(channels, jid);
      if (!channel) throw new Error(`No channel for JID: ${jid}`);
      return channel.sendMessage(jid, text);
    },
    registeredGroups: () => registeredGroups,
    registerGroup,
    syncGroupMetadata: (force) =>
      whatsapp?.syncGroupMetadata(force) ?? Promise.resolve(),
    getAvailableGroups,
    writeGroupsSnapshot: (gf, im, ag, rj) =>
      writeGroupsSnapshot(gf, im, ag, rj),
  });
  queue.setProcessMessagesFn(processGroupMessages);
  recoverPendingMessages();

  // Liveness heartbeat. The watchdog (scripts/paperclaw-watchdog.sh) decides a hung
  // process by how long logs/paperclaw.log has been frozen. But paperclaw legitimately
  // logs nothing for ~1h when idle, and also while a long paper batch's subagent
  // containers grind through NotebookLM — so without a heartbeat the watchdog mistook a
  // healthy service for a hang and restarted it ~hourly, each restart detaching the
  // batch's containers and orphaning papers_queue.json (a 27-paper batch stalled this
  // way with 12 papers never dispatched). Emitting an unconditional heartbeat every
  // HEARTBEAT_MS keeps the log fresh whenever the event loop is alive, so a frozen log
  // now reliably means an actually-hung process. The WA status makes the line double as
  // a socket-liveness signal the watchdog can read (a dead-but-not-reconnecting socket
  // shows `whatsapp=down` across heartbeats while the log itself stays fresh).
  const HEARTBEAT_MS = 10 * 60 * 1000;
  // It is written at any LOG_LEVEL (liveness-log.ts).
  setInterval(() => {
    logHeartbeat(whatsapp?.isConnected() ?? false);
  }, HEARTBEAT_MS).unref();

  startMessageLoop().catch((err) => {
    logger.fatal({ err }, 'Message loop crashed unexpectedly');
    process.exit(1);
  });
}

// Guard: only run when executed directly, not when imported by tests
const isDirectRun =
  process.argv[1] &&
  new URL(import.meta.url).pathname ===
    new URL(`file://${process.argv[1]}`).pathname;

if (isDirectRun) {
  main().catch((err) => {
    logger.error({ err }, 'Failed to start PaperClaw');
    process.exit(1);
  });
}
