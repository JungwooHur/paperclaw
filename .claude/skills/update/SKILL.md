---
name: update
description: "Update PaperClaw from upstream. Fetches latest changes, merges with your customizations and skills, runs migrations. Triggers on \"update\", \"pull upstream\", \"sync with upstream\", \"get latest changes\"."
---

# Update PaperClaw

Pull upstream changes and merge them with the user's installation, preserving skills and customizations. Scripts live in `.claude/skills/update/scripts/`.

**Principle:** Handle everything automatically. Only pause for user confirmation before applying changes, or when merge conflicts need human judgment.

**UX Note:** Use `AskUserQuestion` for all user-facing questions.

## 1. Pre-flight

Check that the skills system is initialized:

```bash
test -d .paperclaw && echo "INITIALIZED" || echo "NOT_INITIALIZED"
```

**If NOT_INITIALIZED:** Run `initSkillsSystem()` first:

```bash
npx tsx -e "import { initPaperclawDir } from './skills-engine/init.js'; initPaperclawDir();"
```

Check for uncommitted git changes:

```bash
git status --porcelain
```

**If there are uncommitted changes:** Warn the user: "You have uncommitted changes. It's recommended to commit or stash them before updating. Continue anyway?" Use `AskUserQuestion` with options: "Continue anyway", "Abort (I'll commit first)". If they abort, stop here.

## 2. Fetch upstream

Run the fetch script:

```bash
./.claude/skills/update/scripts/fetch-upstream.sh
```

Parse the structured status block between `<<< STATUS` and `STATUS >>>` markers. Extract:
- `TEMP_DIR` — path to extracted upstream files
- `REMOTE` — which git remote was used
- `CURRENT_VERSION` — version from local `package.json`
- `NEW_VERSION` — version from upstream `package.json`
- `STATUS` — "success" or "error"

**If STATUS=error:** Show the error output and stop.

**If CURRENT_VERSION equals NEW_VERSION:** Tell the user they're already up to date. Ask if they want to force the update anyway (there may be non-version-bumped changes). If no, clean up the temp dir and stop.

## 3. Preview

Run the preview to show what will change:

```bash
npx tsx scripts/update-core.ts --json --preview-only <TEMP_DIR>
```

This outputs JSON with: `currentVersion`, `newVersion`, `filesChanged`, `filesDeleted`, `conflictRisk`, `customPatchesAtRisk`.

Present to the user:
- "Updating from **{currentVersion}** to **{newVersion}**"
- "{N} files will be changed" — list them if <= 20, otherwise summarize
- If `conflictRisk` is non-empty: "These files have skill modifications and may conflict: {list}"
- If `customPatchesAtRisk` is non-empty: "These custom patches may need re-application: {list}"
- If `filesDeleted` is non-empty: "{N} files will be removed"

## 4. Confirm

Use `AskUserQuestion`: "Apply this update?" with options:
- "Yes, apply update"
- "No, cancel"

If cancelled, clean up the temp dir (`rm -rf <TEMP_DIR>`) and stop.

## 5. Apply

Run the update:

```bash
npx tsx scripts/update-core.ts --json <TEMP_DIR>
```

Parse the JSON output. The result has: `success`, `previousVersion`, `newVersion`, `mergeConflicts`, `backupPending`, `customPatchFailures`, `skillReapplyResults`, `error`.

**If success=true with no issues:** Continue to step 7.

**If customPatchFailures exist:** Warn the user which custom patches failed to re-apply. These may need manual attention after the update.

**If skillReapplyResults has false entries:** Warn the user which skill tests failed after re-application.

## 6. Handle conflicts

**If backupPending=true:** There are unresolved merge conflicts.

For each file in `mergeConflicts`:
1. Read the file — it contains conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Check if there's an intent file for this path in any applied skill (e.g., `.claude/skills/<skill>/modify/<path>.intent.md`)
3. Use the intent file and your understanding of the codebase to resolve the conflict
4. Write the resolved file

After resolving all conflicts:

```bash
npx tsx scripts/post-update.ts
```

This clears the backup, confirming the resolution.

**If you cannot confidently resolve a conflict:** Show the user the conflicting sections and ask them to choose or provide guidance.

## 7. Run migrations

Run migrations between the old and new versions:

```bash
npx tsx scripts/run-migrations.ts <CURRENT_VERSION> <NEW_VERSION> <TEMP_DIR>
```

Parse the JSON output. It contains: `migrationsRun` (count), `results` (array of `{version, success, error?}`).

**If any migration fails:** Show the error to the user. The update itself is already applied — the migration failure needs manual attention.

**If no migrations found:** This is normal (most updates won't have migrations). Continue silently.

## 7a. Rebuild the agent image

If `filesChanged` (step 3) or a conflict you resolved includes `container/Dockerfile`
or anything under `container/agent-runner/` outside `src/` (`package*.json`,
`tsconfig.json`), rebuild the image now, before 7b restarts the bot. The image
bakes those in, the restart's build stages the new runner, and a runner needing
what the old image lacks fails every container at start.
Prune first (CLAUDE.md, "Container Build Cache"): `--no-cache` alone keeps stale
COPY steps.

```bash
docker builder prune -f && ./container/build.sh
```

## 7b. Re-sync systemd units

If the pull touched `groups/main/research-papers/systemd/*.service` or `*.timer`
(or the units aren't symlinked yet), re-link and reload — otherwise a stale
installed unit silently keeps running the OLD ExecStart steps, so new healer
steps never run (a real past incident: `heal_paper_pages.py` was added to the
repo unit but the installed copy was never refreshed).

```bash
for u in paperclaw-qa-heal.service paperclaw-qa-heal.timer; do
  ln -sf "$PWD/groups/main/research-papers/systemd/$u" ~/.config/systemd/user/$u
done
systemctl --user daemon-reload
systemctl --user is-active paperclaw-qa-heal.timer   # should print "active"
```

The bot and watchdog units live in `systemd/` at the repo root.
`paperclaw.service` rebuilds `dist/` in its `ExecStartPre=`, so installing it is
what makes a restart pick up merged code. Their paths all assume the checkout is
`~/paperclaw`, so only that checkout, installed as a user service, links them;
an older install there has a plain file, which `ln -sf` replaces. Anywhere else
(another directory, or a root install with a system unit) setup wrote a copy
with the real paths, and a link would swap it for the template and leave the
bot unable to start. Re-run setup's service step there instead, which rewrites
that copy. The watchdog units are not linked there either: the watchdog script
reads `~/paperclaw/logs/` too.

Both paths install the unit for the checkout they run in, so first make sure it
is the one the service runs. A git worktree or a second clone has no `store/`
(it is gitignored), and a service pointed at it has no WhatsApp session: it
exits on the QR prompt and restarts every few seconds.

```bash
scope=--user; [ "$(id -u)" -eq 0 ] && scope=
live=$(systemctl $scope show paperclaw -p WorkingDirectory --value 2>/dev/null)
if [ -n "$live" ] && [ "$(readlink -f "$live")" != "$(pwd -P)" ]; then
  echo "The service runs $live, not this checkout. Run /update there; nothing installed."
elif [ "$PWD" = "$HOME/paperclaw" ] && [ "$(id -u)" -ne 0 ]; then
  for u in paperclaw.service paperclaw-watchdog.service paperclaw-watchdog.timer; do
    ln -sf "$PWD/systemd/$u" ~/.config/systemd/user/$u
  done
  systemctl --user daemon-reload
  systemctl --user restart paperclaw
else
  npx tsx setup/index.ts --step service
  systemctl $scope restart paperclaw
fi
```

If it printed "The service runs …", stop here and tell the user: the merge is not
live until `/update` runs in that checkout.

Then check the build the restart ran. `restart` returns once `ExecStartPre=` has
finished, so its exit status is already there; a startup WARN in the log is not
yet, since node writes it a moment later. The build's errors are in
`logs/paperclaw.error.log`.

```bash
systemctl --user show paperclaw -p ExecStartPre | grep -o 'status=[0-9]*'   # as root: drop --user
# status=0: the merge is running. Anything else: the build failed, and the bot
# is running the previous dist/.
```

Linking leaves the node drop-in (`~/.config/systemd/user/paperclaw.service.d/node.conf`,
written by setup) alone. If the update switched node versions and rebuilt
`node_modules`, re-run `npx tsx setup/index.ts --step service` so the drop-in
names the new node; a pinned node that was uninstalled is skipped with a
warning in `logs/paperclaw.error.log`.

## 8. Verify

Run build and tests:

```bash
npm run build && npm test
```

**If build fails:** Show the error. Common causes:
- Type errors from merged files — read the error, fix the file, retry
- Missing dependencies — run `npm install` first, retry

**If step 7b's build status was not 0**, the bot is still running the code from
before the merge, and passing this step does not change that. Once the build
passes, restart again and re-read the status until it is `status=0`:

```bash
systemctl --user restart paperclaw
systemctl --user show paperclaw -p ExecStartPre | grep -o 'status=[0-9]*'
```

**If tests fail:** Show which tests failed. Try to diagnose and fix. If you can't fix automatically, report to the user.

**If both pass:** Report success.

## 9. Cleanup

Remove the temp directory:

```bash
rm -rf <TEMP_DIR>
```

Report final status:
- "Updated from **{previousVersion}** to **{newVersion}**"
- Number of files changed
- Any warnings (failed custom patches, failed skill tests, migration issues)
- Build and test status

## Troubleshooting

**No upstream remote:** The fetch script auto-adds `upstream` pointing to `https://github.com/qwibitai/nanoclaw.git`. If the user forked from a different URL, they should set the remote manually: `git remote add upstream <url>`.

**Merge conflicts in many files:** Consider whether the user has heavily customized core files. Suggest using the skills system for modifications instead of direct edits, as skills survive updates better.

**Build fails after update:** Check if `package.json` dependencies changed. Run `npm install` to pick up new dependencies.

**Rollback:** If something goes wrong after applying but before cleanup, the backup is still in `.paperclaw/backup/`. Run:
```bash
npx tsx -e "import { restoreBackup, clearBackup } from './skills-engine/backup.js'; restoreBackup(); clearBackup();"
```
