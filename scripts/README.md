# Scripts

## Leak gates

The rules these enforce are in the root [CLAUDE.md](../CLAUDE.md), "Public Repo
Hygiene"; this is how each gate decides.

- `scripts/check-sensitive.sh` is the pattern scan. `.husky/pre-commit` and
  `.husky/commit-msg` run it: it blocks forbidden paths (even `git add -f`) and
  scans added lines / commit messages for secrets, emails, phones, JIDs, arxiv IDs,
  UUIDs and the assistant session URL.
- `.husky/pre-push` runs `check-sensitive.sh --range` and
  `scripts/private_values.py --range` over exactly what is pushed and no remote has
  yet — each commit's paths, added lines, message and author/committer (checked for
  GitHub noreply addresses), plus annotated-tag messages and taggers and the ref
  names — before typecheck and tests. A ref at a blob or tree is refused.
- **The pre-push gate in force is the MAIN checkout's copy.** `core.hooksPath` is
  shared and absolute, so a push from a worktree runs `~/paperclaw/.husky/pre-push`,
  which finds its scanners next to itself, not in the worktree. A change to the hook
  or the scanners therefore protects pushes only once main is pulled after the
  merge.
- The session URL is not a credential — it returns 403 to anyone but the owner, so
  no conversation content is exposed — but it is an account-linked activity trace
  that is permanent, public, and useless to every reader, since nobody else can
  open it.

### PR and issue text

A git hook only sees commits; a title, body or comment goes from `gh` straight to
GitHub. So `.claude/settings.json` runs `scripts/guard-gh-publish.py` before every
Bash call that mentions `gh`, and it denies a `gh pr`/`issue`/`release`/`gist`/
`repo`/`label` publish or a writing `gh api` call (any endpoint; GraphQL only for a
mutation) whose text fails `check-pr-body.py`'s scan — and one whose text it cannot
read (`$(...)`, a variable, a heredoc, stdin by any name, `--fill`/`--web`, code
piped into a shell), or whose body file an earlier command in the same line could
rewrite (anything but read-only tools, `git push`/`commit` and `check-pr-body.py`).

The scan also compares the text against the real section titles on recent Notion
paper pages, fetched live, which is the only oracle that stays current as papers
are added. The oracle is cached for the guard in
`~/.cache/paperclaw/section-titles.json` (0600) and refreshed in the background.

`python3 scripts/check-pr-body.py --sweep` audits what is already up;
`.github/workflows/leak-scan.yml` re-runs the pattern scan, redacted, as the
backstop for whatever got past the local gates.
