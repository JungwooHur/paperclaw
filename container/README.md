# Agent container

`./container/build.sh` builds the agent image from `container/Dockerfile`.

## Build cache

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT invalidate COPY steps — the builder's volume retains stale files. To force a truly clean rebuild, prune the builder then re-run `./container/build.sh`.

## Bash hook

The CLI bundled with the pinned agent SDK (0.2.34, CLI 2.1.34) runs a Bash
command as `eval <quoted command>`. Unless the command holds a heredoc or a `<`
input redirect, the CLI also means to give it `/dev/null` for stdin, and that is
where it breaks:

- A piped command with no backtick, no `$(` and no `for`/`while`/`until`/`if`/
  `case`/`select` word is split with shell-quote's `parse`, which gets no
  environment, and joined again around an inserted `< /dev/null`: every `$VAR`
  and `${...}` comes out empty.
- Any other command, piped or not, is quoted by shell-quote with `<` and
  `/dev/null` as quoted words, and `eval` appends the redirect to the command
  text, so only its last simple command reads `/dev/null`. A pipe's last stage
  stops reading the pipe, and an earlier command reads the CLI's own stdin: a
  `read` there waits until the CLI moves the call to the background. Only an
  unpiped command holding a quoted string that spans lines escapes this: it is
  single-quoted whole with the redirect on `eval`.

The CLI does not count a heredoc in a command that also holds a shift such as
`1 << 2` or `$((x << y))`.

A command holding a heredoc or a `<` input redirect gets no redirect. It is
single-quoted whole when it holds a heredoc or a quoted string that spans lines,
and quoted by shell-quote otherwise, which keeps the text except that each `!`
gains a backslash when the text holds a `'`.

Both failures showed as "NOTION_TOKEN is empty": `curl ... -H
"Authorization: Bearer $NOTION_TOKEN" | head` sent an empty token (Notion
answers 401), and `curl ... | python3 -c "...for..."` failed with
`JSONDecodeError`, while `env` in the same shell showed the token set. The
agent skipped its Notion writes and reported a token problem.

The runner's PreToolUse hook (`container/agent-runner/src/bash-hook.ts`) puts
two lines before every command:

```
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN 2>/dev/null; exec </dev/null; : '
'
<command>
```

- `exec </dev/null` is a `<` input redirect and `: '<newline>'` a quoted
  string that spans lines, so the CLI single-quotes the whole text with no
  redirect: bash gets the command verbatim, and `exec` gives every command
  `/dev/null` for stdin.
- The command stays last. The CLI takes the first word of the last command to
  read the exit status, and `grep`, `rg`, `find`, `diff`, `test` and `[`
  exiting 1 is no error; a wrapper around the command would hide that word and
  turn a `grep` with no match into an error.

What remains: bash reports an error or `$LINENO` on line k of an n-line command
as line k + n + 3, where plain bash reports k.

CLI 2.1.112 (SDK 0.2.112) quotes every command whole and puts `< /dev/null` on
`eval` itself; #164 tracks that bump. `container/agent-runner/test/bash-hook.test.ts`
runs Bash calls through the bundled CLI against a fake Messages API, with and
without the hook, and fails once the CLI stops erasing a `$VAR` in a piped
command.

## NotebookLM wrapper

Multiple paper requests are meant to run as **parallel background subagents** (the
dispatcher pattern in `groups/main/CLAUDE.md`) — the user sends N papers and must
**never** have to serialize them by hand. The catch: every subagent drives ONE
shared NotebookLM browser profile (`~/.notebooklm`), and Chrome can't be driven by
two processes at once — concurrent `notebooklm ask` calls would collide and yield
summarized/stub sections. So **`container/bin/notebooklm` is a `flock` wrapper**
installed over the real CLI in the Dockerfile: it serializes every NotebookLM call
system-wide, so parallel subagents QUEUE their asks instead of conflicting, while
the rest of each paper's pipeline (figures, tables, Notion upload) still runs in
parallel. **Do NOT advise sending papers one at a time — serialization is the
wrapper's job.** A wrapper change requires a container rebuild to take effect.
Sections dropped, duplicated or reordered by the upload are a different failure
([page audit](../groups/main/research-papers/README.md#page-audit)); don't conflate
the two.

**Serialization does not isolate the context.** The lock covers one command, not a
subagent's sequence of commands, and every call shares
`~/.notebooklm/profiles/default/context.json`: the notebook `use` picked, and the
conversation the last `ask` left. `ask -n X` resumes that cached conversation
whenever X is the cached notebook, so after another subagent's ask an answer comes
from the wrong paper's sources, and a command without `-n` adds a source to an
older paper's notebook. The wrapper therefore deletes that file (and the legacy
`~/.notebooklm/context.json`) under the lock before every call.

It refuses with exit 2 `use`, every `profile` command but `list`, the root options
`-p`/`--profile`/`--storage`, and a non-empty
`NOTEBOOKLM_PROFILE`/`NOTEBOOKLM_HOME`/`NOTEBOOKLM_AUTH_JSON`, so every command must
carry `--notebook <id>`: one without it fails with
"No notebook specified. Use 'notebooklm use <id>' ...",
a hint the wrapper refuses. It refuses the android
backend too (a root `--backend` or a non-empty `NOTEBOOKLM_BACKEND` other than
`web`): that backend signs in with `master_token.json`, a second credential beside
`storage_state.json`. It also refuses every call while `~/.notebooklm/config.json`
names a `default_profile` other than `default`: a `profile switch` on the host would
move every container call to another jar, and to a context the wrapper does not
clear.

**A private profile is not a way out:** it needs a copy of `storage_state.json`, the
two jars' cookies rotate independently, and only the one rotated last stays valid.
When a subagent uses one, the shared jar is signed out and every later resume fails
on auth (`CSRF token not found in HTML` on 0.8.1). `tests/test_notebooklm_wrapper.py`
covers the wrapper.
