#!/usr/bin/env bash
# Guard against committing personal / sensitive / research-activity content to
# this PUBLIC repository.
#
# Modes:
#   check-sensitive.sh --staged         pre-commit: scan staged paths + added lines
#   check-sensitive.sh --msg <file>     commit-msg: scan the commit message
#   check-sensitive.sh --range <revs>   pre-push / CI: every commit `git rev-list
#                                       <revs>` names - its paths, its added
#                                       lines and its message. A new branch is
#                                       `<sha> --not --remotes=<remote>`.
#   check-sensitive.sh --text [label]   stdin, every line: a PR title or body,
#                                       where a '#' line is a heading and not a
#                                       comment as it is for --msg
#   check-sensitive.sh --tag <sha>      an annotated tag object: its message and
#                                       tagger, which a push publishes but no
#                                       commit carries
#
# Commits (--range) and tags must carry GitHub noreply identities: an author,
# committer or tagger email is published with every object that holds it.
#
# PAPERCLAW_REDACT=1 reports the category and file:line (or sha) of each hit,
# never the matching text. CI sets it: an Actions log is public, and it outlives
# the force-push that takes the leak back off the branch.
#
# Override (use deliberately, never by default):
#   PAPERCLAW_ALLOW_SENSITIVE=1 git commit ...
#
# What it blocks:
#   1. Forbidden paths   - runtime data, auth state, chat history, attachments,
#                          paper queues/configs. These are gitignored, but
#                          `git add -f` bypasses gitignore; this does not.
#   2. Secret material   - API tokens, private keys, JWTs.
#   3. Personal data     - real-looking emails, KR phone numbers, WhatsApp JIDs,
#                          assistant session URLs/trailers.
#   4. Research activity - arxiv ids and Notion page UUIDs. Specific papers the
#                          owner reads/processes belong in gitignored files
#                          (groups/main/*, store/, data/), never in tracked
#                          code, docs, commit messages, or PR bodies. Use
#                          placeholders like <arxiv-id> / "paper A" instead.
set -u

MODE="${1:---staged}"
MSG_FILE="${2:-}"

if [ "${PAPERCLAW_ALLOW_SENSITIVE:-0}" = "1" ]; then
  echo "check-sensitive: PAPERCLAW_ALLOW_SENSITIVE=1 — skipping scan." >&2
  exit 0
fi

FAIL=0
REDACT="${PAPERCLAW_REDACT:-0}"
say() { printf '%s\n' "$*" >&2; }

# Files that legitimately contain the patterns. This script itself is not
# scanned at all; the rest still get the secret scan, which has no exclusions.
SCANNER_RE='^scripts/check-sensitive\.sh$'
SELF_EXCLUDE_RE='^(\.husky/|vendor/|package-lock\.json$|.*\.lock$)'

# --- 1. forbidden paths -----------------------------------------------------
FORBIDDEN_PATH_RE='(^|/)\.env(\..*)?$|\.(db|sqlite3?|db-journal|pdf|keys\.json)$|(^|/)(attachments|conversations|sessions)/|(^|/)(store|data|logs)/|notebooks\.json$|papers_(queue|backlog|sweep_state)\.json(\.tmp)?$|(^|/)research-papers/config\.json$|baileys_auth|x-auth'
ALLOWED_PATH_RE='\.env\.example$'

# --- content patterns -------------------------------------------------------
# secrets (never allowed, no exclusions)
RE_SECRET='ntn_[A-Za-z0-9]{10,}|secret_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{10,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN[ A-Z]*PRIVATE KEY|eyJ[A-Za-z0-9_/+-]{15,}\.eyJ'
# personal data
# Each exclusion is matched against one MATCH, never against the line around it:
# a placeholder on a line used to excuse every real hit next to it.
RE_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.(com|net|org|edu|io|ai|kr|de|fr|jp|cn)'
EXCL_EMAIL='noreply|no-reply|@example\.|@anthropic\.com|@s\.whatsapp\.net|^(user|you|name)@'
RE_JID='[0-9]{7,}@(s\.whatsapp\.net|g\.us|lid)'
EXCL_JID='12345|00000|xxxx'
RE_PHONE='(\+?82[ .-]?|0)1[0-9][ .-]?[0-9]{3,4}[ .-]?[0-9]{4}'
EXCL_PHONE='12345678|1234-5678|0000'
# research activity
RE_ARXIV='\b[0-9]{4}\.[0-9]{4,5}(v[0-9]+)?\b'
EXCL_ARXIV='^(2401\.12345|2501\.12345|2403\.67890|1234\.5678)(v[0-9]+)?$'   # documented dummy ids
RE_UUID='\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
EXCL_UUID='00000000-0000|12036300'
# assistant-session identifiers. Not a credential — the URL is 403 for anyone but
# the owner — but it is an account-linked activity trace that is permanent, public,
# and useless to every reader, since nobody else can open it. It reached 34 commits
# and 12 PR bodies before anyone noticed, because nothing here scanned for it.
RE_SESSION='claude[.]ai/code/sess[i]on|(^|[^A-Za-z0-9_])sess[i]on_[A-Za-z0-9]{16,}|Claude[-]Session:'
EXCL_SESSION='sess[i]on_XXXX|<session|session_id|session_state|storage_state'

# The scan, in order. A path that legitimately holds the other patterns still
# gets the rows marked 1 in SECRET_ROW.
PATTERNS=("$RE_SECRET" "$RE_EMAIL" "$RE_JID" "$RE_PHONE" "$RE_ARXIV" "$RE_UUID" "$RE_SESSION")
EXCLUDES=('' "$EXCL_EMAIL" "$EXCL_JID" "$EXCL_PHONE" "$EXCL_ARXIV" "$EXCL_UUID" "$EXCL_SESSION")
MESSAGES=('secret/token material' 'real-looking email address'
  'real-looking WhatsApp JID' 'possible phone number'
  'arxiv id (paper-specific content; use a placeholder)'
  'UUID (Notion page/db id?; use a placeholder)'
  'assistant session URL/trailer (drop it; only the owner can open it)')
SECRET_ROW=(1 0 0 0 0 0 1)

# GitHub's noreply forms: a user's, and GitHub's own committer on web merges.
NOREPLY_RE='@users\.noreply\.github\.com$|^noreply@github\.com$'
noreply() { printf '%s' "$1" | grep -qiE "$NOREPLY_RE"; }

# One row per MATCH, "<input line>:<match>", with the matches that the
# exclusion covers removed. Case-insensitive on purpose: uppercase variants
# (FOO@KAIST.AC.KR, A-F hex UUIDs) must not bypass the scan. Over-matching is
# the safe direction - the override env var exists for genuine false positives.
match_rows() {  # $1 = pattern, $2 = exclusion ('' = none), stdin = text
  local rows
  rows=$(grep -noiaE -- "$1" || true)
  if [ -n "$rows" ] && [ -n "$2" ]; then
    rows=$(printf '%s\n' "$rows" | EXCL="$2" awk '{
      m = $0; sub(/^[0-9]+:/, "", m)
      if (tolower(m) !~ tolower(ENVIRON["EXCL"])) print }')
  fi
  [ -z "$rows" ] || printf '%s\n' "$rows"
}

# Whether any pattern matches: decides if a path may be printed in a public log.
has_hit() {  # $1 = text
  local i
  for i in "${!PATTERNS[@]}"; do
    [ -n "$(printf '%s\n' "$1" | match_rows "${PATTERNS[$i]}" "${EXCLUDES[$i]}")" ] \
      && return 0
  done
  return 1
}

# NOTE: scan_text is called at the end of pipelines, i.e. in a SUBSHELL — it
# cannot mutate the parent's FAIL. It communicates via its exit status instead;
# call sites do `... | scan_text "label" || FAIL=1`.
#
# With $2 = numbered, each stdin line is "<line number>\t<text>" and a hit is
# reported by that number - its line in the file - instead of its position in
# the input.
scan_text() {  # $1 = label of what is being scanned, stdin = text
  # ONLY_SECRETS=1 runs just the rows SECRET_ROW marks. NUL bytes are dropped
  # first, which also turns UTF-16 text with ASCII content back into ASCII.
  local label="$1" text content numbers='' bad=0 i rows want
  text="$(tr -d '\000')"
  [ -z "$text" ] && return 0
  if [ "${2:-}" = numbered ]; then
    numbers=$(printf '%s\n' "$text" | cut -f1)
    content=$(printf '%s\n' "$text" | cut -f2-)
  else
    content=$text
  fi
  for i in "${!PATTERNS[@]}"; do
    [ "${ONLY_SECRETS:-0}" = 1 ] && [ "${SECRET_ROW[$i]}" = 0 ] && continue
    rows=$(printf '%s\n' "$content" | match_rows "${PATTERNS[$i]}" "${EXCLUDES[$i]}")
    [ -z "$rows" ] && continue
    bad=1
    # the first five lines with a hit, by their line in the file
    want=$(printf '%s\n' "$rows" | cut -d: -f1 | uniq | head -5)
    if [ "$REDACT" = 1 ]; then
      printf '%s\n' "$content" | awk -v want="$want" -v map="$numbers" '
        BEGIN { n = split(want, w, "\n"); for (k = 1; k <= n; k++) keep[w[k]] = 1
                if (map != "") split(map, line, "\n") }
        (NR in keep) { print (map != "" ? line[NR] : NR) }' \
        | while IFS= read -r n; do say "✗ [${MESSAGES[$i]}] $label:$n"; done
    else
      say ""
      say "✗ [$label] ${MESSAGES[$i]}"
      printf '%s\n' "$content" | awk -v want="$want" -v map="$numbers" '
        BEGIN { n = split(want, w, "\n"); for (k = 1; k <= n; k++) keep[w[k]] = 1
                if (map != "") split(map, line, "\n") }
        (NR in keep) { print (map != "" ? line[NR] : NR) ":" $0 }' >&2
    fi
  done
  return $bad
}

# What to do with a path: skip (an allowed template, or a file that legitimately
# holds the patterns), forbidden, or scan its added lines.
path_verdict() {  # $1 = path
  if printf '%s' "$1" | grep -qE "$SCANNER_RE"; then echo skip
  elif printf '%s' "$1" | grep -qE "$ALLOWED_PATH_RE"; then echo secrets
  elif printf '%s' "$1" | grep -qE "$FORBIDDEN_PATH_RE"; then echo forbidden
  elif printf '%s' "$1" | grep -qE "$SELF_EXCLUDE_RE"; then echo secrets
  else echo scan
  fi
}

# A path as a report may show it. Under PAPERCLAW_REDACT a path that itself
# holds a pattern (an arxiv id, an email) is replaced by its position, since a
# CI log is public.
shown_path() {  # $1 = path, $2 = its position in the commit's path list
  if [ "$REDACT" = 1 ] && has_hit "$1"; then echo "<path #$2>"
  else printf '%s\n' "$1"
  fi
}

# The lines a one-file patch adds, as "<line in the new file>\t<text>". A merge
# (`--cc`) has one prefix column per parent, and only a line added relative to
# EVERY parent is the merge's own resolution; the rest arrive with the merged
# commits, which are scanned in their own right.
added_lines() {  # stdin = `git show -U0` output for a single file
  awk '
    /^@@/ {
      match($0, /^@+/); n = RLENGTH - 1
      h = $0; sub(/^@+ /, "", h); sub(/ @+.*$/, "", h)
      k = split(h, r, " "); sub(/^\+/, "", r[k]); split(r[k], s, ",")
      ln = s[1] + 0; inhunk = 1; next
    }
    !inhunk || /^\\/ { next }
    {
      p = substr($0, 1, n)
      if (p ~ /-/) next                     # a line the result does not have
      if (p ~ /^\++$/) print ln "\t" substr($0, n + 1)
      ln++
    }'
}

# Fails unless every identity a commit publishes is a GitHub noreply address.
# The address itself is never printed: it is what must not be published.
scan_idents() {  # $1 = full sha, $2 = short sha
  local who email
  while IFS=' ' read -r who email; do
    if ! noreply "$email"; then
      say "✗ [$who] commit $2: the $who email is not a GitHub noreply address"
      FAIL=1
    fi
  done < <(git log -1 --format='author %ae%ncommitter %ce' "$1")
}

scan_commit() {  # $1 = full sha
  local sha="$1" short f k=0 shown verdict
  local -a paths=()
  short=$(git rev-parse --short "$sha")
  scan_idents "$sha" "$short"
  # Paths the commit adds or changes, type changes included (a symlink that
  # becomes a file); for a merge, only those whose result differs from every
  # parent. -z keeps a non-ASCII name as itself: git quotes it otherwise, and
  # the quoted form matches no pathspec and no forbidden-path rule.
  if [ "$(git rev-list --parents -n 1 "$sha" | wc -w)" -gt 2 ]; then
    while IFS= read -r -d '' f; do paths+=("$f"); done \
      < <(git diff-tree -z --cc --no-commit-id --name-only "$sha")
  else
    while IFS= read -r -d '' f; do paths+=("$f"); done \
      < <(git diff-tree -z -r --root --no-commit-id --name-only \
            --diff-filter=d "$sha")
  fi
  # The names are published as well as the content.
  if [ "${#paths[@]}" -gt 0 ]; then
    printf '%s\n' "${paths[@]}" | scan_text "$short (paths)" || FAIL=1
  fi
  for f in "${paths[@]}"; do
    k=$((k + 1))
    verdict=$(path_verdict "$f")
    [ "$verdict" = skip ] && continue
    shown=$(shown_path "$f" "$k")
    if [ "$verdict" = forbidden ]; then
      say "✗ [path] forbidden file in commit $short: $shown"
      say "        runtime data / auth / chat / paper state never goes in the public repo."
      FAIL=1
      continue
    fi
    # --text: a NUL byte or a -diff attribute must not turn the content into
    # "Binary files differ", which no pattern can match.
    git --literal-pathspecs show --format= --no-color --no-ext-diff \
      --no-textconv --text --cc -U0 "$sha" -- "$f" | tr -d '\000' \
      | added_lines | ONLY_SECRETS=$([ "$verdict" = secrets ] && echo 1 || echo 0) \
        scan_text "$short:$shown" numbered || FAIL=1
  done
  git log -1 --format=%B "$sha" | scan_text "$short (message)" || FAIL=1
}

case "$MODE" in
  --msg)
    [ -f "$MSG_FILE" ] || exit 0
    grep -vE '^#' "$MSG_FILE" | scan_text "commit message" || FAIL=1
    ;;
  --text)
    scan_text "${MSG_FILE:-text}" || FAIL=1
    ;;
  --tag)
    if [ "$(git cat-file -t "$MSG_FILE" 2>/dev/null)" != tag ]; then
      say "usage: check-sensitive.sh --tag <tag object sha>"; exit 64
    fi
    short=$(git rev-parse --short "$MSG_FILE")
    tagger=$(git cat-file tag "$MSG_FILE" \
      | sed -n '/^$/q; s/^tagger .*<\([^>]*\)>.*/\1/p')
    if [ -n "$tagger" ] && ! noreply "$tagger"; then
      say "✗ [tagger] tag $short: the tagger email is not a GitHub noreply address"
      FAIL=1
    fi
    git cat-file tag "$MSG_FILE" | sed '1,/^$/d' \
      | scan_text "tag $short (message)" || FAIL=1
    ;;
  --range)
    shift
    [ "$#" -gt 0 ] || { say "usage: check-sensitive.sh --range <rev-list args>"; exit 64; }
    # Fail closed: a range that cannot be listed has not been scanned.
    if ! COMMITS=$(git rev-list --reverse "$@"); then
      say "✗ [range] git rev-list $* failed — nothing was scanned."
      exit 2
    fi
    while IFS= read -r sha; do
      [ -n "$sha" ] && scan_commit "$sha"
    done <<< "$COMMITS"
    ;;
  --staged)
    # Commits to this public repo must use a GitHub noreply author address —
    # a personal email in `git config user.email` becomes permanent public
    # history with every commit.
    # `git var` reports the identity the commit will actually get, so a
    # GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL in the environment counts too.
    for who in author committer; do
      ident=$(git var "GIT_$(printf '%s' "$who" | tr a-z A-Z)_IDENT" 2>/dev/null || true)
      email=$(printf '%s' "$ident" | sed -n 's/.*<\([^>]*\)>.*/\1/p')
      if ! noreply "$email"; then
        say "✗ [$who] the $who email is '$email' — use your GitHub noreply address:"
        say "        git config user.email \"<id>+<username>@users.noreply.github.com\""
        FAIL=1
      fi
    done
    # -z: see scan_commit; a quoted non-ASCII name was never scanned.
    while IFS= read -r -d '' f; do
      verdict=$(path_verdict "$f")
      case "$verdict" in
        skip) continue ;;
        forbidden)
          say "✗ [path] forbidden file staged: $f"
          say "        runtime data / auth / chat / paper state never goes in the public repo."
          FAIL=1
          continue ;;
      esac
      printf '%s\n' "$f" | scan_text "path name" || FAIL=1
      # scan only ADDED lines of this file's staged diff. Drop everything
      # before the first hunk header (@@) instead of grepping out '^+++' —
      # an added line like '++i;' renders as '+++i;' in the diff and would
      # be silently skipped by a '^+++' filter.
      git --literal-pathspecs diff --cached -U0 --no-color --text -- "$f" \
        | tr -d '\000' | sed -n '/^@@/,$p' | grep -aE '^\+' | cut -c2- \
        | ONLY_SECRETS=$([ "$verdict" = secrets ] && echo 1 || echo 0) \
          scan_text "$f" || FAIL=1
    done < <(git diff --cached -z --name-only --diff-filter=d)
    ;;
  *)
    say "usage: check-sensitive.sh --staged | --msg <file> | --range <revs> | --text [label] | --tag <sha>"
    exit 64 ;;
esac

if [ "$FAIL" -ne 0 ]; then
  say ""
  case "$MODE" in
    --range) say "Blocked: sensitive content in these commits (this is a PUBLIC repo)."
             say "Rewrite them (git commit --amend / git rebase -i) before publishing." ;;
    --text|--tag) say "Blocked: sensitive content in this text (this is a PUBLIC repo)." ;;
    *)       say "Commit blocked: sensitive content detected (this is a PUBLIC repo)." ;;
  esac
  say "Fix the content, or — only if every hit above is a false positive —"
  say "re-run with PAPERCLAW_ALLOW_SENSITIVE=1."
  exit 1
fi
exit 0
