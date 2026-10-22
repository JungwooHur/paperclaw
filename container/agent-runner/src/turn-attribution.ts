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
 * Pure bookkeeping, no I/O: the runner owns the stream and the output, and
 * this module is tested on its own (container/agent-runner/test/).
 */
import { randomUUID, type UUID } from 'crypto';
import path from 'path';

/** The inputs one result answered. Written on every output made from a result. */
export interface Turn {
  /** Input ids, in the order the CLI acknowledged them. */
  inputs: string[];
  /** The runner's own background-task nudge was part of the turn. */
  nudge?: true;
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
}

export class TurnAttribution {
  /** Pushes of the current query that no result has answered yet. */
  private readonly pushes = new Map<string, Push>();
  /** Uuids acknowledged since the previous result, in order. */
  private acked: string[] = [];
  private off = false;

  constructor(
    private readonly log: (message: string) => void = () => {},
    private readonly newUuid: () => UUID = randomUUID,
  ) {}

  /**
   * Records a push and returns the uuid it must be sent with. Always a fresh
   * one: the CLI skips a uuid it has already seen, without a reply.
   */
  push(inputs: string[], nudge = false): UUID {
    const uuid = this.newUuid();
    if (!this.off) this.pushes.set(uuid, { inputs: [...inputs], nudge });
    return uuid;
  }

  /** A replayed user message. A uuid this runner did not push is ignored. */
  ack(uuid: string | undefined): void {
    if (uuid && this.pushes.has(uuid) && !this.acked.includes(uuid)) {
      this.acked.push(uuid);
    }
  }

  /**
   * The turn behind a result, or undefined once acknowledgements proved
   * missing. A success result always follows at least one acknowledged push,
   * so one without any means replay is not working here, and every later
   * turn would claim to answer nothing: attribution stops for the rest of
   * this container rather than report that.
   */
  result(subtype: string): Turn | undefined {
    const acked = this.acked;
    this.acked = [];
    if (this.off) return undefined;
    if (acked.length === 0 && subtype === 'success') {
      this.off = true;
      this.pushes.clear();
      this.log(
        'Turn attribution off for this container: a success result came with no acknowledged input',
      );
      return undefined;
    }
    const turn: Turn = { inputs: [] };
    for (const uuid of acked) {
      const push = this.pushes.get(uuid)!;
      this.pushes.delete(uuid);
      turn.inputs.push(...push.inputs);
      if (push.nudge) turn.nudge = true;
    }
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
    this.acked = [];
    return unsettled;
  }
}
