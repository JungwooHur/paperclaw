/**
 * The PreToolUse hook on the agent's Bash tool.
 */
import type {
  HookCallback,
  PreToolUseHookInput,
} from '@anthropic-ai/claude-agent-sdk';

/** Auth variables the SDK needs and the agent's commands must not read. */
export const SECRET_ENV_VARS = ['ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'];

/**
 * Shell text put before every Bash command, ending in a newline: unsets
 * SECRET_ENV_VARS, sets the shell's stdin to /dev/null and runs `:` on a
 * single-quoted newline.
 */
export const COMMAND_PREFIX =
  `unset ${SECRET_ENV_VARS.join(' ')} 2>/dev/null; ` +
  `exec </dev/null; : '\n'\n`;

/** The PreToolUse hook: each Bash call with COMMAND_PREFIX before its command. */
export const sanitizeBashHook: HookCallback = async (input) => {
  const toolInput = (input as PreToolUseHookInput).tool_input as Record<
    string,
    unknown
  >;
  const command = toolInput.command as string | undefined;
  if (!command) return {};
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      updatedInput: { ...toolInput, command: COMMAND_PREFIX + command },
    },
  };
};
