#!/usr/bin/env bash
# Guard against committing personal / sensitive / research-activity content to
# this PUBLIC repository.
#
# Two modes:
#   check-sensitive.sh --staged       pre-commit: scan staged paths + added lines
#   check-sensitive.sh --msg <file>   commit-msg: scan the commit message
#
# Override (use deliberately, never by default):
#   PAPERCLAW_ALLOW_SENSITIVE=1 git commit ...
#
# What it blocks:
#   1. Forbidden paths   - runtime data, auth state, chat history, attachments,
#                          paper queues/configs. These are gitignored, but
#                          `git add -f` bypasses gitignore; this does not.
#   2. Secret material   - API tokens, private keys, JWTs.
#   3. Personal data     - real-looking emails, KR phone numbers, WhatsApp JIDs.
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

# NOTE: scan_text is called at the end of pipelines, i.e. in a SUBSHELL — it
# cannot mutate the parent's FAIL. It communicates via its exit status instead;
# call sites do `... | scan_text "label" || FAIL=1`.
scan_text() {  # $1 = label of what is being scanned, stdin = text
  local label="$1" text bad=0
  text="$(cat)"
  [ -z "$text" ] && return 0

  check() {  # $2 = pattern, $3 = exclusion ('' = none), $4 = message
    # Case-insensitive on purpose: uppercase variants (FOO@KAIST.AC.KR, A-F hex
    # UUIDs) must not bypass the scan. Over-matching is the safe direction —
    # the override env var exists for genuine false positives.
    local hits
    hits=$(printf '%s\n' "$text" | grep -niIE "$2" || true)
    [ -n "$3" ] && hits=$(printf '%s\n' "$hits" | grep -viE "$3" || true)
    if [ -n "$hits" ]; then
      say ""
      say "✗ [$1] $4"
      printf '%s\n' "$hits" | head -5 >&2
      bad=1
    fi
  }
  check "$label" "$RE_SECRET" ''            'secret/token material'
  check "$label" "$RE_EMAIL"  "$EXCL_EMAIL" 'real-looking email address'
  check "$label" "$RE_JID"    "$EXCL_JID"   'real-looking WhatsApp JID'
  check "$label" "$RE_PHONE"  "$EXCL_PHONE" 'possible phone number'
  check "$label" "$RE_ARXIV"  "$EXCL_ARXIV" 'arxiv id (paper-specific content; use a placeholder)'
  check "$label" "$RE_UUID"   "$EXCL_UUID"  'UUID (Notion page/db id?; use a placeholder)'
  return $bad
}

case "$MODE" in
  --msg)
    [ -f "$MSG_FILE" ] || exit 0
    grep -vE '^#' "$MSG_FILE" | scan_text "commit message" || FAIL=1
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
      printf '%s' "$f" | grep -qE "$ALLOWED_PATH_RE" && continue
      if printf '%s' "$f" | grep -qE "$FORBIDDEN_PATH_RE"; then
        say "✗ [path] forbidden file staged: $f"
        say "        runtime data / auth / chat / paper state never goes in the public repo."
        FAIL=1
        continue
      fi
      printf '%s' "$f" | grep -qE "$SELF_EXCLUDE_RE" && continue
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
    say "usage: check-sensitive.sh --staged | --msg <file>"; exit 64 ;;
esac

if [ "$FAIL" -ne 0 ]; then
  say ""
  say "Commit blocked: sensitive content detected (this is a PUBLIC repo)."
  say "Fix the content, or — only if every hit above is a false positive —"
  say "re-run with PAPERCLAW_ALLOW_SENSITIVE=1."
  exit 1
fi
exit 0
