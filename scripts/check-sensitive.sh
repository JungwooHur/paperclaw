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

# Files the scanner never scans (they legitimately contain the patterns).
SELF_EXCLUDE_RE='^(scripts/check-sensitive\.sh|\.husky/|vendor/|package-lock\.json$|.*\.lock$)'

# --- 1. forbidden paths -----------------------------------------------------
FORBIDDEN_PATH_RE='(^|/)\.env(\..*)?$|\.(db|sqlite3?|db-journal|pdf|keys\.json)$|(^|/)(attachments|conversations|sessions)/|(^|/)(store|data|logs)/|notebooks\.json$|papers_queue\.json$|(^|/)research-papers/config\.json$|baileys_auth|x-auth'
ALLOWED_PATH_RE='\.env\.example$'

# --- content patterns -------------------------------------------------------
# secrets (never allowed, no exclusions)
RE_SECRET='ntn_[A-Za-z0-9]{10,}|secret_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{10,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN[ A-Z]*PRIVATE KEY|eyJ[A-Za-z0-9_/+-]{15,}\.eyJ'
# personal data
RE_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.(com|net|org|edu|io|ai|kr|de|fr|jp|cn)'
EXCL_EMAIL='noreply|no-reply|@example\.|@anthropic\.com|@users\.noreply|@s\.whatsapp\.net|user@|you@|name@'
RE_JID='[0-9]{7,}@(s\.whatsapp\.net|g\.us|lid)'
EXCL_JID='12345|00000|xxxx'
RE_PHONE='(\+82|82|0)1[0-9][ .-]?[0-9]{3,4}[ .-]?[0-9]{4}'
EXCL_PHONE='12345678|1234-5678|0000'
# research activity
RE_ARXIV='\b[0-9]{4}\.[0-9]{4,5}(v[0-9]+)?\b'
EXCL_ARXIV='2401\.12345|2501\.12345|2403\.67890|1234\.5678'   # documented dummy ids
RE_UUID='\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
EXCL_UUID='00000000-0000|12036300'
# assistant-session identifiers. Not a credential — the URL is 403 for anyone but
# the owner — but it is an account-linked activity trace that is permanent, public,
# and useless to every reader, since nobody else can open it. It reached 34 commits
# and 12 PR bodies before anyone noticed, because nothing here scanned for it.
RE_SESSION='claude[.]ai/code/sess[i]on|(^|[^A-Za-z0-9_])sess[i]on_[A-Za-z0-9]{16,}|Claude[-]Session:'
EXCL_SESSION='sess[i]on_XXXX|<session|session_id|session_state|storage_state'

# NOTE: scan_text is called at the end of pipelines, i.e. in a SUBSHELL — it
# cannot mutate the parent's FAIL. It communicates via its exit status instead;
# call sites do `... | scan_text "label" || FAIL=1`.
#
# With $2 = numbered, each stdin line is "<line number>\t<text>" and a hit is
# reported by that number - its line in the file - instead of its position in
# the input.
scan_text() {  # $1 = label of what is being scanned, stdin = text
  local label="$1" text content numbers='' bad=0
  text="$(cat)"
  [ -z "$text" ] && return 0
  if [ "${2:-}" = numbered ]; then
    numbers=$(printf '%s\n' "$text" | cut -f1)
    content=$(printf '%s\n' "$text" | cut -f2-)
  else
    content=$text
  fi

  check() {  # $2 = pattern, $3 = exclusion ('' = none), $4 = message
    # Case-insensitive on purpose: uppercase variants (FOO@KAIST.AC.KR, A-F hex
    # UUIDs) must not bypass the scan. Over-matching is the safe direction —
    # the override env var exists for genuine false positives.
    local hits
    hits=$(printf '%s\n' "$content" | grep -niIE "$2" || true)
    [ -n "$3" ] && hits=$(printf '%s\n' "$hits" | grep -viE "$3" || true)
    [ -z "$hits" ] && return 0
    bad=1
    if [ -n "$numbers" ]; then  # input position -> line in the file
      hits=$(printf '%s\n' "$hits" | awk -F: -v OFS=: -v map="$numbers" '
        BEGIN { split(map, line, "\n") } { $1 = line[$1]; print }')
    fi
    if [ "$REDACT" = 1 ]; then
      printf '%s\n' "$hits" | head -5 | cut -d: -f1 \
        | while IFS= read -r n; do say "✗ [$4] $1:$n"; done
    else
      say ""
      say "✗ [$1] $4"
      printf '%s\n' "$hits" | head -5 >&2
    fi
  }
  check "$label" "$RE_SECRET" ''            'secret/token material'
  check "$label" "$RE_EMAIL"  "$EXCL_EMAIL" 'real-looking email address'
  check "$label" "$RE_JID"    "$EXCL_JID"   'real-looking WhatsApp JID'
  check "$label" "$RE_PHONE"  "$EXCL_PHONE" 'possible phone number'
  check "$label" "$RE_ARXIV"  "$EXCL_ARXIV" 'arxiv id (paper-specific content; use a placeholder)'
  check "$label" "$RE_UUID"   "$EXCL_UUID"  'UUID (Notion page/db id?; use a placeholder)'
  check "$label" "$RE_SESSION" "$EXCL_SESSION" 'assistant session URL/trailer (drop it; only the owner can open it)'
  return $bad
}

# What to do with a path: skip (an allowed template, or a file that legitimately
# holds the patterns), forbidden, or scan its added lines.
path_verdict() {  # $1 = path
  if printf '%s' "$1" | grep -qE "$ALLOWED_PATH_RE"; then echo skip
  elif printf '%s' "$1" | grep -qE "$FORBIDDEN_PATH_RE"; then echo forbidden
  elif printf '%s' "$1" | grep -qE "$SELF_EXCLUDE_RE"; then echo skip
  else echo scan
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

scan_commit() {  # $1 = full sha
  local sha="$1" short files f
  short=$(git rev-parse --short "$sha")
  # Paths the commit adds, copies, modifies or renames into. For a merge, only
  # the paths whose result differs from every parent.
  if [ "$(git rev-list --parents -n 1 "$sha" | wc -w)" -gt 2 ]; then
    files=$(git diff-tree --cc --no-commit-id --name-only "$sha")
  else
    files=$(git diff-tree -r --root --no-commit-id --name-only \
      --diff-filter=ACMR "$sha")
  fi
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$(path_verdict "$f")" in
      skip) continue ;;
      forbidden)
        say "✗ [path] forbidden file in commit $short: $f"
        say "        runtime data / auth / chat / paper state never goes in the public repo."
        FAIL=1
        continue ;;
    esac
    git show --format= --no-color --no-ext-diff --no-textconv --cc -U0 \
      "$sha" -- "$f" | added_lines | scan_text "$short:$f" numbered || FAIL=1
  done <<< "$files"
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
    AUTHOR_EMAIL=$(git config user.email || true)
    if ! printf '%s' "$AUTHOR_EMAIL" | grep -qE '@users\.noreply\.github\.com$'; then
      say "✗ [author] git user.email is '$AUTHOR_EMAIL' — use your GitHub noreply address:"
      say "        git config user.email \"<id>+<username>@users.noreply.github.com\""
      FAIL=1
    fi
    STAGED=$(git diff --cached --name-only --diff-filter=ACMR)
    [ -z "$STAGED" ] && exit 0
    while IFS= read -r f; do
      case "$(path_verdict "$f")" in
        skip) continue ;;
        forbidden)
          say "✗ [path] forbidden file staged: $f"
          say "        runtime data / auth / chat / paper state never goes in the public repo."
          FAIL=1
          continue ;;
      esac
      # scan only ADDED lines of this file's staged diff. Drop everything
      # before the first hunk header (@@) instead of grepping out '^+++' —
      # an added line like '++i;' renders as '+++i;' in the diff and would
      # be silently skipped by a '^+++' filter.
      git diff --cached -U0 --no-color -- "$f" \
        | sed -n '/^@@/,$p' | grep -E '^\+' | cut -c2- \
        | scan_text "$f" || FAIL=1
    done <<< "$STAGED"
    ;;
  *)
    say "usage: check-sensitive.sh --staged | --msg <file> | --range <revs> | --text [label]"
    exit 64 ;;
esac

if [ "$FAIL" -ne 0 ]; then
  say ""
  case "$MODE" in
    --range) say "Blocked: sensitive content in these commits (this is a PUBLIC repo)."
             say "Rewrite them (git commit --amend / git rebase -i) before publishing." ;;
    --text)  say "Blocked: sensitive content in this text (this is a PUBLIC repo)." ;;
    *)       say "Commit blocked: sensitive content detected (this is a PUBLIC repo)." ;;
  esac
  say "Fix the content, or — only if every hit above is a false positive —"
  say "re-run with PAPERCLAW_ALLOW_SENSITIVE=1."
  exit 1
fi
exit 0
