/**
 * Files a paper Q&A the moment the answer is sent, instead of up to six minutes
 * later.
 *
 * The agent is supposed to save a paper answer itself, and repeatedly does not —
 * a prose rule in the group's memory that has been strengthened several times
 * and still gets skipped. `auto_save_qa.py` on a five-minute timer exists for
 * exactly that, and it works; what it cannot do is be prompt. An answer sent one
 * second after a cycle ends waits for the whole next one, which is what "it did
 * not save it again" looks like from the outside even though it eventually does.
 *
 * So the same script is also run once, right after the reply goes out, scoped to
 * the chat that was just answered. The timer stays exactly as it is: this
 * trigger is an accelerator, not a replacement, and if it fails for any reason
 * the next cycle still catches the pair.
 */
import path from 'path';

/**
 * How long a reply must be before it is worth scanning for.
 *
 * A pre-filter only. The backstop applies its own, higher floor once it has tied
 * the pair to a paper, so this one is deliberately lower than that: erring high
 * would silently drop an answer that the backstop would have saved, turning a
 * late save into no save. Erring low costs one background run that finds
 * nothing.
 */
export const LIKELY_ANSWER_CHARS = 200;

export function worthFilingCheck(reply: string): boolean {
  return reply.trim().length >= LIKELY_ANSWER_CHARS;
}

export interface QaFollowupOptions {
  projectRoot: string;
  chatJid: string;
  lockFile: string;
}

/**
 * The command that files anything the agent left unsaved in one chat.
 *
 * Held under a lock because the same script runs on a timer: two copies reading
 * the same window at once would each decide the answer is missing and file it
 * twice. `-n` means a run that collides simply gives up — the timer's own cycle
 * is already doing the work.
 */
export function qaFollowupCommand(opts: QaFollowupOptions): {
  cmd: string;
  args: string[];
} {
  const script = path.join(
    opts.projectRoot,
    'groups',
    'main',
    'research-papers',
    'auto_save_qa.py',
  );
  return {
    cmd: 'flock',
    args: [
      '-n',
      opts.lockFile,
      'python3',
      script,
      '--hours',
      '1',
      '--chat',
      opts.chatJid,
    ],
  };
}
