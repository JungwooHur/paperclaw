/**
 * The PreToolUse hook on the agent's Bash tool.
 */
import type {
  HookCallback,
  PreToolUseHookInput,
  SyncHookJSONOutput,
} from '@anthropic-ai/claude-agent-sdk';

/** Auth variables the SDK needs and the agent's commands must not read. */
export const SECRET_ENV_VARS = ['ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'];

/** The heredoc delimiter that carries the agent's command to bash. */
export const COMMAND_DELIMITER = 'PAPERCLAW_BASH_CMD';

/**
 * The hook's output for one Bash call's `tool_input`: the call with its
 * command run after the auth variables are unset, its text carried verbatim
 * in a heredoc closed by COMMAND_DELIMITER and evaluated with stdin on
 * /dev/null; a deny when a line of the command starts with COMMAND_DELIMITER.
 */
export function sanitizeBashInput(
  toolInput: Record<string, unknown>,
): SyncHookJSONOutput {
  const command = toolInput.command as string | undefined;
  if (!command) return {};
  if (command.split('\n').some((line) => line.startsWith(COMMAND_DELIMITER))) {
    return {
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason:
          `Refused: a line of the command starts with ${COMMAND_DELIMITER}, ` +
          'which the runner uses to pass commands to bash. Rewrite it without that line.',
      },
    };
  }
  const wrapped =
    `unset ${SECRET_ENV_VARS.join(' ')} 2>/dev/null; ` +
    `{ eval "$(cat <<'${COMMAND_DELIMITER}'\n${command}\n${COMMAND_DELIMITER}\n)"; } < /dev/null`;
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      updatedInput: { ...toolInput, command: wrapped },
    },
  };
}

/** sanitizeBashInput as the SDK's PreToolUse hook. */
export const sanitizeBashHook: HookCallback = async (input) =>
  sanitizeBashInput(
    (input as PreToolUseHookInput).tool_input as Record<string, unknown>,
  );
