/**
 * Which inputs each SDK result answered.
 *
 * A `result` names no input, so the runner asks the CLI to acknowledge what it
 * consumes (`--replay-user-messages`): every user message pushed with a uuid
 * comes back once as `{type: 'user', isReplay: true, uuid}`, between its
 * turn's start and that turn's result. That holds both for a turn's prompt and
 * for a push the CLI merges into a running turn. So the acknowledgements since
 * the previous result belong to the next result.
 *
 * Two kinds of turn break that rule, and both still replay something:
 * - A slash command that starts a turn is never acknowledged under its own
 *   uuid. A skill is replayed expanded, and a local command's output is
 *   replayed, each under a uuid the CLI makes; an unknown command replays
 *   nothing. The CLI takes one queued push per turn, in order, so a command is
 *   credited to the turn that took it, counted off turn starts.
 * - A turn the CLI starts itself, for an agent-team message, replays that
 *   message under the CLI's own uuid. It answers no input.
 *
 * Pure bookkeeping, no I/O: the runner owns the stream and the output, and
 * this module is tested on its own (container/agent-runner/test/).
 */
import { randomUUID, type UUID } from 'crypto';
import path from 'path';

/** The inputs one result answered. Written on every output made from a result. */
export interface Turn {
  /** Input ids: the turn's prompt, then what was merged into it, in order. */
  inputs: string[];
  /**
   * The turn took something the host never sent: the runner's background-task
   * nudge, or a message the CLI queued itself (an agent-team message).
   */
  internal?: true;
}

/** A push no result was attributed to when its query ended. */
export interface UnsettledPush {
  inputs: string[];
  nudge: boolean;
  /** The CLI took it into a turn, but that turn produced no result. */
  acked: boolean;
}

/** The stdin prompt's id when the host gave none. */
export const STDIN_INPUT_ID = 'stdin';

/**
 * A prompt the CLI runs as a slash command. A copy of the router's pattern
 * (src/router.ts), which sends such a message bare; a test keeps the two equal.
 */
export const SLASH_COMMAND = /^\/[a-z][a-z0-9_-]*(\s|$)/i;

export function isSlashCommand(text: string): boolean {
  return SLASH_COMMAND.test(text);
}

/** Ids of the start-up push: the stdin prompt, then the IPC files drained into it. */
export function startupInputIds(
  promptRef: string | undefined,
  drained: string[],
): string[] {
  return [promptRef || STDIN_INPUT_ID, ...drained];
}

/** An IPC input file's id: the `ref` it carries, else its file name. */
export function ipcInputId(file: string, data: { ref?: unknown }): string {
  return typeof data.ref === 'string' && data.ref !== ''
    ? data.ref
    : path.basename(file);
}

interface Push {
  inputs: string[];
  nudge: boolean;
  command: boolean;
  /** A turn start was counted against it: it is that turn's prompt. */
  taken: boolean;
}

export class TurnAttribution {
  /** Pushes of the current query that no result has answered yet, in order. */
  private readonly pushes = new Map<string, Push>();
  /** Every uuid pushed in the current query. */
  private readonly own = new Set<string>();
  /** Own uuids acknowledged since the previous result, in order. */
  private acked: string[] = [];
  /** The push taken by the turn running now, if a push started it. */
  private prompt: string | undefined;
  /** A replay under a uuid this runner never pushed, since the previous result. */
  private foreign = false;
  private off = false;

  constructor(
    private readonly log: (message: string) => void = () => {},
    private readonly newUuid: () => UUID = randomUUID,
  ) {}

  /**
   * Records a push and returns the uuid it must be sent with, a fresh one
   * every time, a retry included. The CLI acknowledges a uuid it has already
   * seen without running it, so a reused one would be a false
   * acknowledgement: the next result would claim an input the model never saw.
   */
  push(text: string, inputs: string[], nudge = false): UUID {
    const uuid = this.newUuid();
    if (!this.off) {
      this.own.add(uuid);
      this.pushes.set(uuid, {
        inputs: [...inputs],
        nudge,
        command: isSlashCommand(text),
        taken: false,
      });
    }
    return uuid;
  }

  /**
   * A turn started (`system/init`). The CLI takes the oldest push it has not
   * taken or merged yet as that turn's prompt.
   */
  turnStarted(): void {
    if (this.off) return;
    this.prompt = undefined;
    for (const [uuid, push] of this.pushes) {
      if (!push.taken && !this.acked.includes(uuid)) {
        push.taken = true;
        this.prompt = uuid;
        return;
      }
    }
  }

  /** A replayed user message, under the runner's uuid or one the CLI made. */
  ack(uuid: string | undefined): void {
    if (uuid && this.pushes.has(uuid)) {
      if (!this.acked.includes(uuid)) this.acked.push(uuid);
    } else if (!uuid || !this.own.has(uuid)) {
      this.foreign = true;
    }
  }

  /**
   * The turn behind a result, or undefined once replay proved missing. A turn
   * always replays something, unless it is a rejected slash command: a success
   * result after no replay at all means replay is not working here, and every
   * later turn would claim to answer nothing. Attribution stops for the rest of
   * this container rather than report that.
   */
  result(subtype: string): Turn | undefined {
    const { acked, prompt, foreign } = this;
    this.acked = [];
    this.prompt = undefined;
    this.foreign = false;
    if (this.off) return undefined;

    const promptPush = prompt ? this.pushes.get(prompt) : undefined;
    const command =
      prompt && promptPush?.command && !acked.includes(prompt)
        ? prompt
        : undefined;
    if (
      subtype === 'success' &&
      acked.length === 0 &&
      !foreign &&
      command === undefined
    ) {
      this.off = true;
      this.pushes.clear();
      this.own.clear();
      this.log(
        'Turn attribution off for this container: a success result followed no replayed input, so replay is not working',
      );
      return undefined;
    }
    // A turn the CLI started for its own message, while this push was queued:
    // the push is still waiting for the turn that takes it.
    if (
      promptPush &&
      !promptPush.command &&
      !acked.includes(prompt!) &&
      foreign &&
      subtype === 'success'
    ) {
      promptPush.taken = false;
    }

    const turn: Turn = { inputs: [] };
    let hostInput = false;
    for (const uuid of command ? [command, ...acked] : acked) {
      const push = this.pushes.get(uuid)!;
      this.pushes.delete(uuid);
      turn.inputs.push(...push.inputs);
      if (push.nudge) turn.internal = true;
      else hostInput = true;
    }
    // Replays no host input explains: a message the CLI queued itself. A
    // command's own replay is one, under the CLI's uuid, and it is explained.
    if (foreign && !hostInput) turn.internal = true;
    return turn;
  }

  /**
   * Ends a query: returns the pushes no result answered, and forgets them, as
   * the next query talks to a new CLI. Nothing once attribution is off.
   */
  endQuery(): UnsettledPush[] {
    const unsettled = [...this.pushes].map(([uuid, push]) => ({
      inputs: push.inputs,
      nudge: push.nudge,
      acked: this.acked.includes(uuid),
    }));
    this.pushes.clear();
    this.own.clear();
    this.acked = [];
    this.prompt = undefined;
    this.foreign = false;
    return unsettled;
  }
}
