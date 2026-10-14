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
import { startSchedulerLoop } from './task-scheduler.js';
import { Channel, NewMessage, RegisteredGroup } from './types.js';
import { logger } from './logger.js';
import { qaFollowupCommand, worthFilingCheck } from './qa-followup.js';
import {
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
}
// Per group with a message container running: its open prompts, oldest first.
const openPrompts: Record<string, OpenPrompt[]> = {};
// router_state key: per group, the piped prompts the latest usage-limit
// refusal put back, so a restart before the container exits can still drop
// the files it left. Cleared once the files are dealt with.
const REFUSED_INPUT_KEY = 'usage_limit_refused_input';

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

  const isMainGroup = group.folder === MAIN_GROUP_FOLDER;

  const sinceTimestamp = lastAgentTimestamp[chatJid] || '';
  const missedMessages = getMessagesSince(
    chatJid,
    sinceTimestamp,
    ASSISTANT_NAME,
  );

  if (missedMessages.length === 0) return true;

  // For non-main groups, check if trigger is required and present
  if (!isMainGroup && group.requiresTrigger !== false) {
    const hasTrigger = missedMessages.some((m) =>
      TRIGGER_PATTERN.test(m.content.trim()),
    );
    if (!hasTrigger) return true;
  }

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
  const prompts: OpenPrompt[] = [{ cursor: lastAgentTimestamp[chatJid] }];
  openPrompts[chatJid] = prompts;

  const output = await runAgent(group, prompt, chatJid, async (result) => {
    // Streaming output callback — called for each agent result
    if (result.result) {
      const raw =
        typeof result.result === 'string'
          ? result.result
          : JSON.stringify(result.result);
      logger.info({ group: group.name }, `Agent output: ${raw.slice(0, 200)}`);
      const limit = recordUsageLimit(raw);
      if (limit) {
        // The CLI's refusal, not an answer: hold the request for the reset.
        usageLimited = true;
        // Put the unanswered messages back now, not when the container exits:
        // the window is already persisted, so a restart in between would
        // keep a cursor past messages nothing answered, and recovery after
        // the reset would find nothing pending.
        lastAgentTimestamp[chatJid] = answeredCursor;
        saveState();
        rememberRefusedInput(chatJid, prompts);
        queue.closeStdin(chatJid);
        if (limit.notice) await channel.sendMessage(chatJid, limit.notice);
        const resumeAt = usageLimitHeldUntil();
        if (resumeAt) resumeAfterUsageLimit(resumeAt);
        return;
      }
      // A result ends a turn, and turns take prompts in the order they were
      // given. A turn answered only through send_message counts too.
      const settled = prompts.shift();
      if (settled !== undefined) answeredCursor = settled.cursor;
      if (usageLimited) {
        // A turn after a refusal (extra usage, say) answered more; keep the
        // saved cursor exact for a restart.
        lastAgentTimestamp[chatJid] = answeredCursor;
        saveState();
        rememberRefusedInput(chatJid, prompts);
      }
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
      // Only reset idle timer on actual results, not session-update markers (result: null)
      resetIdleTimer();
    }

    if (result.status === 'success') {
      queue.notifyIdle(chatJid);
    }

    if (result.status === 'error') {
      hadError = true;
    }
  });
  if (openPrompts[chatJid] === prompts) delete openPrompts[chatJid];

  await channel.setTyping?.(chatJid, false);
  if (idleTimer) clearTimeout(idleTimer);

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
    // Roll back cursor so retries can re-process these messages
    lastAgentTimestamp[chatJid] = previousCursor;
    saveState();
    logger.warn(
      { group: group.name },
      'Agent error, rolled back message cursor for retry',
    );
    return false;
  }

  return true;
}

async function runAgent(
  group: RegisteredGroup,
  prompt: string,
  chatJid: string,
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
  // the refused request, and the group keeps the one it had.
  let usageLimited = false;
  const wrappedOnOutput = onOutput
    ? async (output: ContainerOutput) => {
        if (typeof output.result === 'string' && isUsageLimit(output.result)) {
          usageLimited = true;
        }
        if (output.newSessionId && output.status !== 'error' && !usageLimited) {
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
      },
      (proc, containerName) =>
        queue.registerProcess(chatJid, proc, containerName, group.folder),
      wrappedOnOutput,
    );

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

          const isMainGroup = group.folder === MAIN_GROUP_FOLDER;
          const needsTrigger = !isMainGroup && group.requiresTrigger !== false;

          // For non-main groups, only act on trigger messages.
          // Non-trigger messages accumulate in DB and get pulled as
          // context when a trigger eventually arrives.
          if (needsTrigger) {
            const hasTrigger = groupMessages.some((m) =>
              TRIGGER_PATTERN.test(m.content.trim()),
            );
            if (!hasTrigger) continue;
          }

          // Pull all messages since lastAgentTimestamp so non-trigger
          // context that accumulated between triggers is included.
          const allPending = getMessagesSince(
            chatJid,
            lastAgentTimestamp[chatJid] || '',
            ASSISTANT_NAME,
          );
          const messagesToSend =
            allPending.length > 0 ? allPending : groupMessages;

          if (!pipeToActiveContainer(chatJid, channel, messagesToSend)) {
            // No active container — enqueue for a new one
            queue.enqueueMessageCheck(chatJid);
          }
        }
      }
    } catch (err) {
      logger.error({ err }, 'Error in message loop');
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL));
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
  // on top of the messages being pending. Pending is enough; they are held.
  if (usageLimitHeldUntil()) return false;
  const file = queue.sendMessage(chatJid, formatMessages(messages));
  if (!file) return false;
  logger.debug(
    { chatJid, count: messages.length },
    'Piped messages to active container',
  );
  lastAgentTimestamp[chatJid] = messages[messages.length - 1].timestamp;
  saveState();
  openPrompts[chatJid]?.push({ cursor: lastAgentTimestamp[chatJid], file });
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
 * so the container that runs them after the reset does not also drain them.
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
 * Startup recovery: check for unprocessed messages in registered groups.
 * Handles crash between advancing lastTimestamp and processing messages.
 * While a usage-limit window is open it only arms itself for the window's end,
 * which is also how held messages get picked up after the reset.
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
      queue.enqueueMessageCheck(chatJid);
    }
  }
}

/** @internal - exported for testing */
export {
  discardRefusedInputAfterRestart as _discardRefusedInputAfterRestart,
  loadState as _loadState,
  pipeToActiveContainer as _pipeToActiveContainer,
  processGroupMessages as _processGroupMessages,
  recoverPendingMessages as _recoverPendingMessages,
};

function ensureContainerSystemRunning(): void {
  ensureContainerRuntimeRunning();
  cleanupOrphans();
}

async function main(): Promise<void> {
  ensureContainerSystemRunning();
  initDatabase();
  logger.info('Database initialized');
  loadState();
  discardRefusedInputAfterRestart();

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
  startSchedulerLoop({
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
  });
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
  setInterval(() => {
    logger.info(
      `heartbeat whatsapp=${whatsapp?.isConnected() ? 'up' : 'down'}`,
    );
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
