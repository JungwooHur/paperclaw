# Agent container

`./container/build.sh` builds the agent image from `container/Dockerfile`.

## Build cache

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT invalidate COPY steps — the builder's volume retains stale files. To force a truly clean rebuild, prune the builder then re-run `./container/build.sh`.

## Bash hook

The CLI bundled with the pinned agent SDK (0.2.34, CLI 2.1.34) rewrites a piped
Bash command before bash sees it, and both rewrites break it:

- A piped command with no backtick, no `$(` and no `for`/`while`/`until`/`if`/
  `case`/`select` word is split with shell-quote's `parse`, which gets no
  environment, and joined again around an inserted `< /dev/null`: every `$VAR`
  and `${...}` comes out empty.
- Any other piped command is quoted whole with `<` and `/dev/null` as quoted
  words, so the redirect lands on the pipe's last stage, which reads `/dev/null`.

Both showed as "NOTION_TOKEN is empty": `curl ... -H "Authorization: Bearer
$NOTION_TOKEN" | head` sent an empty token (Notion answers 401), and `curl ... |
python3 -c "...for..."` failed with `JSONDecodeError`, while `env` in the same
shell showed the token set. The agent skipped its Notion writes and reported a
token problem.

The runner's PreToolUse hook (`container/agent-runner/src/bash-hook.ts`) runs
every command as

```
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN 2>/dev/null; { eval "$(cat <<'PAPERCLAW_BASH_CMD'
<command>
PAPERCLAW_BASH_CMD
)"; } < /dev/null
```

The CLI passes a command holding a heredoc through quoted whole with no
redirect, so neither rewrite runs; the quoted delimiter hands bash the text
verbatim, and the outer redirect keeps stdin on `/dev/null`. A command with a
line that starts with the delimiter is refused: inside `$(...)` bash also ends
the heredoc at a line such as `PAPERCLAW_BASH_CMD)`.

One shape still breaks. A command holding a shift by a digit (`1 << 2`) or a
`<<` inside `$((...))` no longer looks like a heredoc to the CLI, and when a line
continuation also comes before its first double quote, the CLI can turn each `!`
into `\!`.

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
