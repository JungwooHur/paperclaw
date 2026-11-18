# Agent container

`./container/build.sh` builds the agent image from `container/Dockerfile`.

## Build cache

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT invalidate COPY steps — the builder's volume retains stale files. To force a truly clean rebuild, prune the builder then re-run `./container/build.sh`.

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
