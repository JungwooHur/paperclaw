---
name: structure
description: Layout guide for this repository - what each top-level area holds, where a new module, test, doc, unit or skill goes, how a retired module leaves the tree, and what makes code reachable. Load before placing, moving or removing code, tests or docs.
---

# Repository layout

The root [CLAUDE.md](../../../CLAUDE.md) routes to each area's doc; this file says
where new work lands. Paths are relative to the repository root.

## Layout map

Every top-level entry of `git ls-files` appears in the first column, alone or
with others of its kind; the drift check below keeps it complete.

| Area | Holds | Doc |
|------|-------|-----|
| `src/` | The host process, built into `dist/`; channels in `src/channels/`; each vitest test beside its module | Key Files in root `CLAUDE.md`; `docs/SPEC.md` |
| `container/` | The agent image: `Dockerfile`, `build.sh`; `agent-runner/`, the process inside it (`src/`, `test/`); `bin/`, commands the image installs; `skills/`, skills every group gets | `container/README.md` |
| `groups/` | Agent prompts `groups/main/CLAUDE.md` and `groups/global/CLAUDE.md`; the paper scripts in `groups/main/research-papers/`, their healer unit in its `systemd/` and dev tools in its `tools/`; everything else is runtime state, gitignored | `groups/main/research-papers/README.md`, `groups/main/research-papers/systemd/README.md` |
| `systemd/` | The bot and watchdog units | `systemd/README.md` |
| `scripts/` | Leak gates, helpers the units run, the skill-package CLIs, probes | `scripts/README.md` |
| `setup/` | The steps behind `npm run setup`, and the Notion setup tools `README.md` runs by hand | none; the `/setup` skill drives it |
| `skills-engine/` | Applies, replays and removes skill packages; tests in `skills-engine/__tests__/` | none |
| `tests/` | The pytest suite | none; Development in root `CLAUDE.md` |
| `docs/` | Docs that span areas: `docs/SPEC.md`, `docs/DEBUG_CHECKLIST.md`, `docs/SECURITY.md`, `docs/REQUIREMENTS.md`, upstream design notes | each file is its own |
| `vendor/` | Third-party code copied in | `vendor/README.md` |
| `.claude/` | `.claude/settings.json`, which wires `scripts/guard-gh-publish.py` in as a hook before `gh` calls; `.claude/skills/`, the repo skills | this file |
| `.github/` | `.github/workflows/ci.yml`, `.github/workflows/leak-scan.yml`, the PR template | none |
| `.husky/` | Git hooks | `scripts/README.md` |
| `launchd/` | An upstream macOS plist; setup writes the one it installs (`setup/service.ts`) | none |
| `assets/` | Upstream images | none |
| `config-examples/` | An example mount allowlist; the real one lives outside the tree (`src/mount-security.ts`) | none |
| `package.json`, `package-lock.json`, `tsconfig.json`, `.prettierrc`, `.nvmrc`, `vitest.config.ts`, `vitest.setup.ts` | Node build, format and test config | none |
| `pytest.ini`, `requirements-dev.txt` | Python test config | none |
| `README.md`, `CHANGELOG.md`, `LICENSE`, `CLAUDE.md` | Project docs; `CLAUDE.md` is the router | none |
| `.env.example`, `.mcp.json`, `.gitignore`, `setup.sh` | Settings template, MCP config, ignore rules, the bootstrap that installs Node packages before `setup/` | none |

## Runtime directories

Gitignored. `npm run build` writes `dist/`, the service manager writes `logs/`
from the unit's output lines (`systemd/paperclaw.service`), and the host creates
the rest.

```
├── dist/                          # Compiled JavaScript (gitignored)
│   └── agent-runner-src/          # The runner staged by the same build; a host pins it to data/runner-bundle/ when it starts
├── store/                         # Local data (gitignored)
│   ├── auth/                      # WhatsApp authentication state
│   └── messages.db                # SQLite database (messages, chats, scheduled_tasks, task_run_logs, registered_groups, sessions, router_state)
├── data/                          # Application state (gitignored)
│   ├── sessions/                  # Per-group session data (.claude/ dirs with JSONL transcripts)
│   ├── runner-bundle/             # The runner this host hands to groups, pinned when it started
│   ├── masks/                     # The empty file and directory mounted over masked paths of the main group's project mount
│   └── ipc/                       # Container IPC (messages/, tasks/)
├── logs/                          # Runtime logs (gitignored)
│   ├── paperclaw.log               # Host stdout
│   └── paperclaw.error.log         # Host stderr
│   # Note: Per-container logs are in groups/{folder}/logs/container-*.log
└── groups/{Group Name}/           # Per-group folders (created on registration)
    ├── logs/                      # Task logs for this group
    └── *.md                       # Files created by the agent
```

## Where new work goes

Walk the rules top to bottom; the first that matches decides.

1. **Runs in the host process**: `src/`, a channel in `src/channels/`. Of the
   host-side TypeScript only `src/` is compiled into `dist/`, typechecked and
   format-checked (`tsconfig.json`, `package.json`); `setup/`, `skills-engine/`
   and the `scripts/` CLIs run through `tsx`. A setting is a `.env` key read with
   `readEnvFile` (`src/env.ts`); how settings reach the process is in
   `systemd/README.md`, "Install".
2. **Runs inside the agent container**: `container/agent-runner/src/`, its tests in
   `container/agent-runner/test/`. How a change reaches a group is in
   `docs/SPEC.md`, "Per-Group Copies: Settings and Runner". A new package in
   `container/agent-runner/package.json` needs an image rebuild first: the image
   installs the runner's packages when it is built (`container/Dockerfile`).
3. **A command the image provides**: `container/bin/<tool>` plus a `COPY` line of
   its own in `container/Dockerfile`, which copies each file by name; a system or
   pip package is a `RUN` line there. Both need an image rebuild
   (`container/README.md`).
4. **A repo skill**, instructions for whoever works in this checkout:
   `.claude/skills/<name>/SKILL.md`. Add `manifest.yaml` only for a package that
   changes code; `skills-engine/replay.ts` and `scripts/validate-all-skills.ts` read
   only directories that have one. A skill contributors should find gets a row in
   the Skills table of root `CLAUDE.md`.
5. **Instructions for a container agent**: for every group,
   `container/skills/<name>/SKILL.md`, synced into each group's skills at every
   container start (`src/skill-sync.ts`); for every group but main,
   `groups/global/CLAUDE.md`, which the runner appends to their prompt
   (`container/agent-runner/src/index.ts`); for the main group, the procedure it
   governs in `groups/main/CLAUDE.md`.
6. **A page repair the healer applies**: a function in a paper script, called from
   `heal` in `groups/main/research-papers/heal_paper_pages.py`, which skips the
   pages `heal_skip.txt` lists and every non-paper page; a unit line of its own
   would bypass both. Only a job over the whole cycle, as
   `groups/main/research-papers/sweep_batch_queue.py` is, gets its own
   `ExecStart=` line in `groups/main/research-papers/systemd/paperclaw-qa-heal.service`.
7. **A paper script**, Python the main agent or the healer runs:
   `groups/main/research-papers/<name>.py` plus its `!` line in `.gitignore`; a dev
   tool for those scripts goes in `groups/main/research-papers/tools/`.
8. **A host service or timer**: `systemd/`, a timer on `OnCalendar=`
   (`systemd/README.md`, "Timers"). It runs only once installed, so add it to the
   link loop in `.claude/skills/update/SKILL.md`, and to `setup/service.ts` when
   first setup must install it.
9. **A setup step**: `setup/<step>.ts`, registered in `STEPS` in `setup/index.ts`.
10. **A helper a unit, hook or operator runs** (shell, Python, or a `tsx` CLI):
    `scripts/`.
11. **Skill-package machinery**: `skills-engine/`.
12. **Third-party code**: `vendor/<project>/`, with its own licence
    (`vendor/README.md`).
13. **A test** goes where its suite collects it:
    - vitest collects only the roots in `vitest.config.ts`: beside the module in
      `src/` or `setup/`, or in `skills-engine/__tests__/` or
      `container/agent-runner/test/`. pytest collects only `tests/`
      (`pytest.ini`).
    - TypeScript is tested with vitest and Python with pytest. A shell script, or
      a whole script run as a subprocess, is tested beside the code it works with:
      `setup/service.test.ts` runs `scripts/paperclaw-node.sh`,
      `tests/test_pre_push.py` runs `.husky/pre-push`, and
      `tests/test_notebooklm_wrapper.py` runs `container/bin/notebooklm`.
    - What a paper-script test covers, and that both suites stay offline, is in
      Development in root `CLAUDE.md`.
    - A check of a doc against the code reads the tracked file
      (`src/env-example.test.ts`, `src/debug-checklist.test.ts`,
      `tests/test_documented_commands.py`).
14. **A doc** goes by what it holds:
    - A fact about one area: that area's README in the map. A folder with none gets
      one only for a fact no existing doc owns, plus a row in Area Docs of root
      `CLAUDE.md`.
    - Host behaviour across modules: `docs/SPEC.md`. What to check first when
      something breaks: `docs/DEBUG_CHECKLIST.md`.
    - A constraint: a test. The story of a fix: its commit and PR.
    - Root `CLAUDE.md`: router rows and repo-wide rules only.

## Conventions

- **Paper scripts are flat modules**, not a package, and import each other by bare
  name (`tests/conftest.py`).
- **`groups/` is an allowlist.** `.gitignore` ignores `groups/*` and re-admits each
  tracked path with a `!` line, so a new script or subdirectory under
  `groups/main/research-papers/` stays untracked until its line is added
  (`groups/main/research-papers/README.md`, "Writing a script or a repair").
- **Host-only state stays out of agent prompts.** The main agent can write
  `groups/main/`, so a file only host code may touch there, such as the batch
  backlog and the sweep state (`groups/main/research-papers/README.md`, "Batch
  queue sweeper"), is gitignored and named in neither `groups/main/CLAUDE.md` nor
  `container/skills/`.
- **Units are linked, never copied** (`systemd/README.md` and
  `groups/main/research-papers/systemd/README.md`, "Install").
- **Agent-runner tests stay out of its `src/`.** The image's entrypoint compiles
  that directory whole under `set -e` at every container start
  (`container/Dockerfile`).
- **Always-loaded files have a byte budget and no dates.** `src/always-loaded.test.ts`
  holds root `CLAUDE.md`, `groups/main/CLAUDE.md` and `groups/global/CLAUDE.md` to
  it.

## Reject

- A second home for a fact an area README, `docs/SPEC.md` or a test already holds:
  link to it instead.
- Incident narrative, dates, durations or counts in an always-loaded file: they
  belong to the commit and PR.
- A new top-level directory for work an area above already covers.

## Retiring a module

Delete it, with its tests, in the change that replaces it. Find every reference
with `git grep -n <name>` and update each hit in that change: imports,
`package.json` scripts, `ExecStart=` lines, Dockerfile `COPY` lines, `.gitignore`
`!` lines, `vitest.config.ts` roots, docs, agent prompts and skills. Keep no
fallback, flag or shim for the old path. Name in the PR what outside the tree
still uses it: an installed unit, a `.env` key, a group's edited runner copy.

## Reachability roots

Code that nothing below reaches, directly or through imports, is dead: delete it.
A module's own test does not make it reachable: CI counts through the commands
and npm scripts it runs, not through what a test imports.

- CI: `.github/workflows/ci.yml` (format, typecheck of host and agent runner,
  vitest, pytest) and `.github/workflows/leak-scan.yml`.
- `package.json` scripts: `build` (tsc, then `src/stage-runner.ts` from `dist/`),
  `start` and `dev` (`src/index.ts`), `auth` (`src/whatsapp-auth.ts`), `setup`
  (`setup/index.ts`), `test`.
- Service units: `systemd/paperclaw.service` (the build, then the host through
  `scripts/paperclaw-node.sh`), `systemd/paperclaw-watchdog.service`
  (`scripts/paperclaw-watchdog.sh`), and each `ExecStart=` script of
  `groups/main/research-papers/systemd/paperclaw-qa-heal.service`, with their
  timers.
- The image: the `COPY` lines of `container/Dockerfile` and its entrypoint, which
  compiles the agent runner and runs it; `src/container-runner.ts` mounts each
  group's runner copy over the image's and syncs `container/skills/`.
- Hooks: `.husky/pre-commit`, `.husky/commit-msg`, `.husky/pre-push`, and
  `scripts/guard-gh-publish.py`, wired in by `.claude/settings.json`.
- Setup: `setup.sh` and the steps in `setup/index.ts`.
- Instructions: a command is reachable when an instruction file tells someone to
  run it. Those are the always-loaded files (root `CLAUDE.md`,
  `groups/main/CLAUDE.md`, `groups/global/CLAUDE.md`), the repo skills
  (`.claude/skills/*/SKILL.md`), the container skills
  (`container/skills/*/SKILL.md`), and runbook steps: `docs/DEBUG_CHECKLIST.md`,
  a procedure in `docs/SPEC.md`, a README's procedure (the root `README.md`
  included). `tests/test_documented_commands.py` checks the commands in
  `groups/main/CLAUDE.md`. A doc that only mentions a path, without telling
  anyone to run it, does not make it reachable.

## Drift check

Compares the first column of the layout map with the tracked tree. It prints
`in sync`, or each entry missing on either side and exits 1.

```bash
(cd "$(git rev-parse --show-toplevel)" && export LC_ALL=C
listed=$(awk '/^## Layout map/{on=1; next} /^## /{on=0} on && /^\| `/' \
  .claude/skills/structure/SKILL.md | cut -d'|' -f2 | grep -o '`[^`]*`' \
  | tr -d '`' | sed 's:/$::' | sort -u)
tree=$(git ls-files | cut -d/ -f1 | sort -u)
drift=$(comm -23 <(echo "$listed") <(echo "$tree") | sed 's/^/listed, not tracked: /'
  comm -13 <(echo "$listed") <(echo "$tree") | sed 's/^/tracked, not listed: /')
echo "${drift:-in sync}"; [ -z "$drift" ])
```

## When this file and `CLAUDE.md` disagree

Root `CLAUDE.md` wins. Fix both in the same change, and update the layout map in
any change that adds, moves or removes a top-level entry.
