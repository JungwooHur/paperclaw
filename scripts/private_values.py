#!/usr/bin/env python3
"""Known-value leak check: the owner's real private values never get published.

The pattern scan in check-sensitive.sh guesses at what a secret LOOKS like, so
a token format it has never seen, or an identifier with no telltale shape,
passes it. This compares against the owner's actual values instead: a format
change cannot slip past it, and it cannot fire on anything but a real value.

Values are read from three places, none of them inside the work tree:

  * the main checkout's .env (PAPERCLAW_ENV_FILE names another file): every
    key that looks private, plus the keys handed to the agent container;
  * store/messages.db, registered_groups.jid, opened read-only
    (PAPERCLAW_MESSAGES_DB names another file);
  * ${XDG_CONFIG_HOME:-~/.config}/paperclaw/private-values, one value per
    line, '#' comments (PAPERCLAW_PRIVATE_VALUES names another file). It lives
    outside the work tree so it can never be staged.

Usage:
  private_values.py --range <rev-list args>   commits: patch, message, idents
  private_values.py --file F|-                a file, or stdin

A hit prints `<label>: commit <sha> (<part>)` or `<label>: <file>` and exits 1.
The value, and the line it sits on, are never printed. With no values loaded
it prints one note to stderr and exits 0. There is no override: a real value
is never a false positive.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request

# Mirrors CONTAINER_SECRET_KEYS in src/container-runner.ts; a test holds the
# two together. OUTPUT_LANGUAGE is a setting, not a secret.
CONTAINER_SECRET_KEYS = (
    'CLAUDE_CODE_OAUTH_TOKEN',
    'ANTHROPIC_API_KEY',
    'NOTION_TOKEN',
    'NOTION_RESEARCH_DB',
    'OUTPUT_LANGUAGE',
)
_NOT_PRIVATE = frozenset({'OUTPUT_LANGUAGE'})
_PRIVATE_KEY_RE = re.compile(
    r'TOKEN|KEY|SECRET|PASS|JID|COOKIE|AUTH|_DB$|_ID$', re.IGNORECASE)

# Shorter values are ordinary words and numbers; they would fire on anything.
MIN_VALUE_LEN = 8
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$',
    re.IGNORECASE)
_JID_RE = re.compile(
    r'^([^@\s]+)@(s\.whatsapp\.net|g\.us|lid|c\.us|broadcast)$',
    re.IGNORECASE)
_JID_NUMBER_RE = re.compile(r'\d{7,}')
# A phone number as people write it: digits and separators only.
_PHONE_RE = re.compile(r'^\+?[\d\s().-]+$')
# What a phone number is written with besides its digits. A digit needle is
# looked for in the text with these removed, so "+82 10-..." still matches.
_SEPARATORS_RE = re.compile(r'[\s().+-]')
_KR_PREFIX = '82'  # the owner's country code; at home a number starts with 0

USAGE = 'usage: private_values.py --range <rev-list args> | --file F|-'


@dataclasses.dataclass(frozen=True)
class Needle:
    """One form of a private value to look for.

    Attributes:
        label: Where the value came from. Safe to print; never the value.
        text: The text to find, case-folded when `fold` is set.
        fold: Whether to compare case-insensitively.
        digits: Whether `text` is a phone number's digits, to be found in the
            text with phone-number separators removed.
    """

    label: str
    text: str
    fold: bool
    digits: bool = False


def is_private_key(key: str) -> bool:
    """Whether a .env key names a value that must never be published."""
    if key in CONTAINER_SECRET_KEYS and key not in _NOT_PRIVATE:
        return True
    return bool(_PRIVATE_KEY_RE.search(key))


def variants(label: str, value: str) -> list[Needle]:
    """Every form in which `value` could appear in published text.

    A UUID is matched dashed and undashed, since Notion ids appear both ways.
    A WhatsApp JID is also matched by its number, which is a phone number or a
    group id on its own; that number, and a value that is a phone number, are
    matched whatever separators surround the digits, and a Korean number in
    its national form too (+82 10... is written 010...). Emails, JIDs and UUIDs
    compare case-insensitively.

    Args:
        label: The value's source, carried on every returned needle.
        value: The raw value.

    Returns:
        The needles for this value; empty when it is too short to be one.
    """
    value = value.strip()
    out = []
    if len(value) >= MIN_VALUE_LEN:
        if _PHONE_RE.match(value) and len(re.sub(r'\D', '', value)) >= 7:
            return _phone_needles(label, re.sub(r'\D', '', value))
        if _UUID_RE.match(value):
            hexed = value.replace('-', '').lower()
            dashed = '-'.join((hexed[:8], hexed[8:12], hexed[12:16],
                               hexed[16:20], hexed[20:]))
            out += [Needle(label, hexed, True), Needle(label, dashed, True)]
        elif '@' in value:
            out.append(Needle(label, value.casefold(), True))
        else:
            out.append(Needle(label, value, False))
    jid = _JID_RE.match(value)
    if jid:
        for number in _JID_NUMBER_RE.findall(jid.group(1)):
            out += _phone_needles(label, number)
    return out


def _phone_needles(label: str, number: str) -> list[Needle]:
    """A number's digits, plus its national form when it is a Korean one."""
    out = [Needle(label, number, False, digits=True)]
    if number.startswith(_KR_PREFIX) and len(number) >= 10:
        out.append(Needle(label, '0' + number[len(_KR_PREFIX):], False,
                          digits=True))
    return out


def main_checkout() -> str | None:
    """The main checkout's root, also from a worktree, which has no .env."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = subprocess.run(
            ['git', '-C', here, 'rev-parse', '--path-format=absolute',
             '--git-common-dir'],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    common = proc.stdout.strip()
    if os.path.basename(common) != '.git':
        return None
    return os.path.dirname(common)


def env_file() -> str | None:
    """The .env to read: PAPERCLAW_ENV_FILE, else the main checkout's."""
    override = os.environ.get('PAPERCLAW_ENV_FILE')
    if override:
        return override
    root = main_checkout()
    return os.path.join(root, '.env') if root else None


def read_env(path: str | None) -> dict[str, str]:
    """Parses a .env file the way src/env.ts does. Missing file: empty dict."""
    if not path:
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    result = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        if value:
            result[key.strip()] = value
    return result


def _messages_db() -> str | None:
    override = os.environ.get('PAPERCLAW_MESSAGES_DB')
    if override:
        return override
    root = main_checkout()
    return os.path.join(root, 'store', 'messages.db') if root else None


def _group_jids(path: str | None) -> list[str]:
    """registered_groups.jid, read-only; empty if the DB is missing."""
    if not path or not os.path.isfile(path):
        return []
    uri = 'file:' + urllib.request.pathname2url(os.path.abspath(path))
    try:
        conn = sqlite3.connect(uri + '?mode=ro', uri=True)
        try:
            rows = conn.execute(
                'SELECT jid FROM registered_groups ORDER BY rowid').fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        print('private_values: messages.db unreadable; its JIDs are not '
              'checked', file=sys.stderr)
        return []
    return [r[0] for r in rows if isinstance(r[0], str)]


def _values_file() -> str:
    override = os.environ.get('PAPERCLAW_PRIVATE_VALUES')
    if override:
        return override
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'paperclaw', 'private-values')


def _listed_values(path: str) -> list[tuple[int, str]]:
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if line and not line.startswith('#'):
            out.append((n, line))
    return out


def load_needles() -> list[Needle]:
    """Every private value from every source, as needles, first label wins."""
    found = []
    env = read_env(env_file())
    for key in sorted(env):
        if is_private_key(key):
            found += variants(f'.env:{key}', env[key])
    for i, jid in enumerate(_group_jids(_messages_db()), 1):
        found += variants(f'messages.db:registered_groups.jid#{i}', jid)
    for n, value in _listed_values(_values_file()):
        found += variants(f'private-values:{n}', value)
    seen, out = set(), []
    for needle in found:
        if needle.text not in seen:
            seen.add(needle.text)
            out.append(needle)
    return out


def find(text: str, needles: list[Needle]) -> list[str]:
    """Labels of the values present in `text`, sorted, without duplicates.

    NUL bytes are dropped first: UTF-16 text with ASCII content is that
    content with a NUL after every character.
    """
    text = text.replace('\0', '')
    folded = squeezed = None
    labels = set()
    for needle in needles:
        if needle.digits:
            if squeezed is None:
                squeezed = _SEPARATORS_RE.sub('', text)
            if needle.text in squeezed:
                labels.add(needle.label)
        elif needle.fold:
            if folded is None:
                folded = text.casefold()
            if needle.text in folded:
                labels.add(needle.label)
        elif needle.text in text:
            labels.add(needle.label)
    return sorted(labels)


def own_added_lines(patch: str) -> str:
    """Text a patch publishes: file headers and the lines it adds.

    Removed and context lines are already public through the parent. For a
    merge (`--cc`), a line is the merge's own only when it is added relative
    to every parent; the rest arrive with the commits being merged, which are
    scanned on their own.

    Args:
        patch: `git show --cc -U0` output.

    Returns:
        The published lines, newline-joined.
    """
    out = []
    parents = None
    for line in patch.splitlines():
        if line.startswith('@@'):
            parents = len(line) - len(line.lstrip('@')) - 1
        elif line.startswith('diff '):
            parents = None
            out.append(line)
        elif parents and line[:parents] == '+' * parents:
            out.append(line[parents:])
    return '\n'.join(out)


def _git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True,
                          errors='replace', check=True).stdout


def commit_parts(sha: str) -> list[tuple[str, str]]:
    """The published text of one commit, as (part, text) pairs."""
    meta = _git('show', '-s', '--format=%an%x00%ae%x00%cn%x00%ce%x00%B', sha)
    an, ae, cn, ce, message = meta.split('\0', 4)
    patch = _git('show', '--format=', '--no-color', '--no-ext-diff',
                 '--no-textconv', '--text', '--cc', '-U0', sha)
    return [('author', f'{an}\n{ae}'), ('committer', f'{cn}\n{ce}'),
            ('message', message), ('patch', own_added_lines(patch))]


def scan_range(rev_args: list[str], needles: list[Needle]) -> list[str]:
    """Hit lines for every commit `git rev-list <rev_args>` names."""
    shas = _git('rev-list', '--reverse', *rev_args).split()
    hits = []
    for sha in shas:
        for part, text in commit_parts(sha):
            for label in find(text, needles):
                hits.append(f'{label}: commit {sha[:12]} ({part})')
    return hits


def _read_input(name: str) -> str:
    if name == '-':
        return sys.stdin.read()
    with open(name, encoding='utf-8', errors='replace') as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--range'] and len(argv) > 1:
        mode = 'range'
    elif argv[:1] == ['--file'] and len(argv) == 2:
        mode = 'file'
    else:
        print(USAGE, file=sys.stderr)
        return 2
    needles = load_needles()
    if not needles:
        print('private_values: no private values loaded (no .env, '
              'messages.db or private-values file); known-value check '
              'skipped', file=sys.stderr)
        return 0
    try:
        if mode == 'range':
            hits = scan_range(argv[1:], needles)
        else:
            name = '<stdin>' if argv[1] == '-' else argv[1]
            hits = [f'{label}: {name}'
                    for label in find(_read_input(argv[1]), needles)]
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or '').strip().splitlines()[:1]
        print(f'private_values: git failed, blocking: {" ".join(detail)}',
              file=sys.stderr)
        return 2
    except OSError as e:
        print(f'private_values: cannot read input, blocking: {e.strerror}',
              file=sys.stderr)
        return 2
    if not hits:
        return 0
    for hit in hits:
        print(hit)
    print('private_values: a known private value would be published. Remove '
          'it from the text (rewrite the commit if it is committed).',
          file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
