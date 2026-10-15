#!/usr/bin/env python3
"""PreToolUse hook: scan what a gh command would publish, before it runs.

A git hook sees commits only. A PR or issue title, a body, a comment or a review
goes from `gh` straight to GitHub, so "run check-pr-body.py first" was a rule in
prose, and prose gets skipped. Claude Code runs this before every Bash tool call
(.claude/settings.json); it denies a publishing gh command whose text fails
check-pr-body's scan: patterns, the owner's known private values, and the
paper-title oracle.

Publishing commands:
  gh pr create|edit|comment|review|merge, gh issue create|edit|comment,
  gh release create|edit, and gh api with a field flag (-f/-F/--field/
  --raw-field/--input) against pulls, issues, comments or graphql.

A publishing command whose text cannot be read is denied as well (fail closed):
command substitution, backticks, a variable, a heredoc, stdin (`--body-file -`,
`-F key=@-`, `--input -`), a missing file, --fill/--editor/--web/--recover, or a
file that an earlier part of the same command line could write first.

Exit status is the hook protocol's: 0 allows the call, 2 denies it with the
reason on stderr, which names labels only, never the text that matched. A
command that is not gh returns at once, before anything heavy is imported.

PAPERCLAW_ALLOW_SENSITIVE=1 in front of the gh command skips the pattern scan,
as it does for the commit hooks. Known values and paper titles have no override.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys

_MAYBE_GH = re.compile(r'\bgh\b')
# For lines this parser cannot follow: deny only what may be a publishing call.
_LOOKS_PUBLISHING = re.compile(
    r'\bgh\b[^\n]*?\b(?:(?:pr|issue|release)\b[^\n]*?'
    r'\b(?:create|edit|comment|review|merge)\b|api\b)')

# Text-bearing flags per publishing subcommand: flag -> what it carries.
_T, _B, _F = 'title', 'body', 'body file'
_CREATE = {'-t': _T, '--title': _T, '-b': _B, '--body': _B,
           '-F': _F, '--body-file': _F}
_COMMENT = {'-b': _B, '--body': _B, '-F': _F, '--body-file': _F}
_MERGE = {'-t': _T, '--subject': _T, '-b': _B, '--body': _B,
          '-F': _F, '--body-file': _F}
_RELEASE = {'-t': _T, '--title': _T, '-n': _B, '--notes': _B,
            '-F': _F, '--notes-file': _F}
_TEXT_FLAGS = {
    ('pr', 'create'): _CREATE, ('pr', 'edit'): _CREATE,
    ('pr', 'comment'): _COMMENT, ('pr', 'review'): _COMMENT,
    ('pr', 'merge'): _MERGE,
    ('issue', 'create'): _CREATE, ('issue', 'edit'): _CREATE,
    ('issue', 'comment'): _COMMENT,
    ('release', 'create'): _RELEASE, ('release', 'edit'): _RELEASE,
}
# Flags that make gh compose or collect the text somewhere this hook cannot see.
_EDITOR = {'-e', '--editor', '-w', '--web'}
_BLIND_FLAGS = {
    ('pr', 'create'): _EDITOR | {'-f', '--fill', '--fill-first',
                                 '--fill-verbose', '--recover'},
    ('pr', 'comment'): _EDITOR,
    ('issue', 'create'): _EDITOR | {'--recover'},
    ('issue', 'comment'): _EDITOR,
    ('release', 'create'): {'--notes-from-tag'},
    ('release', 'edit'): {'--notes-from-tag'},
}

_API_FIELDS = {'-f': 'raw', '--raw-field': 'raw', '-F': 'typed',
               '--field': 'typed', '--input': 'input'}
_API_VALUED = {'-X', '--method', '-H', '--header', '-q', '--jq', '-t',
               '--template', '--hostname', '-p', '--preview', '--cache'}
_API_TARGET = re.compile(r'(^|/)(pulls|issues|comments|graphql)(/|$|\?)')

# Wrappers that run their arguments as a command, with the flags that take a
# value of their own.
_WRAPPERS = {
    'env': {'-u', '--unset', '-C', '--chdir'},
    'command': set(), 'builtin': set(), 'exec': {'-a'}, 'nohup': set(),
    'time': {'-f', '-o'}, 'nice': {'-n'}, 'stdbuf': {'-i', '-o', '-e'},
    'timeout': {'-s', '--signal', '-k', '--kill-after'},
    'sudo': {'-u', '-g', '-C', '-D', '-h', '-p', '-r', '-t', '-U'},
}
_SHELLS = {'bash', 'sh', 'dash', 'zsh', 'ksh'}
# Programs that do not rewrite a file unless redirected, which is checked
# separately. Anything else that runs before a gh command reading a file could
# change that file between this scan and the read. git and gh can write a file
# they are given by name (git checkout -- f), so naming the file is enough.
_NO_WRITE = {'cd', 'pushd', 'popd', 'true', 'false', ':', 'echo', 'printf',
             'test', '[', 'ls', 'pwd', 'sleep', 'cat', 'head', 'tail', 'grep',
             'rg', 'wc', 'jq', 'git', 'gh', 'export', 'unset', 'which', 'type',
             'date'}
_NAMES_FILES = {'git', 'gh'}

_PUNCT = ';&|()<>\n'
_OPS = re.compile(r'&>>|&>|>>|>\||>&|<&|<<<|<<-|<<|<>|\|\||&&|;;|\|&|'
                  r'[;&|()<>\n]')
_WRITE_OPS = {'>', '>>', '>|', '&>', '&>>', '<>'}
_ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_HEREDOC_AT = re.compile(
    r'<<(-?)[ \t]*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([A-Za-z_][A-Za-z0-9_.-]*))'
    r'(?=[\s;&|()<>]|$)')
_MAX_DEPTH = 4
_ORACLE_SECONDS = 120


class Unreadable(Exception):
    """A publishing command whose text this hook cannot read."""


@dataclasses.dataclass
class _Simple:
    """One simple command of a command line."""

    cwd: str | None
    words: list[str] = dataclasses.field(default_factory=list)
    writes: list[str] = dataclasses.field(default_factory=list)
    heredoc: bool = False


@dataclasses.dataclass
class Publication:
    """What one publishing gh command would send.

    Attributes:
        what: The command, e.g. "gh pr create".
        texts: (label, text) pairs; a label never contains the text.
        reads: Absolute paths of the files the text comes from.
        patterns: False when PAPERCLAW_ALLOW_SENSITIVE=1 skips the pattern scan.
    """

    what: str
    texts: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    reads: list[str] = dataclasses.field(default_factory=list)
    patterns: bool = True


def _strip_heredocs(command: str) -> str:
    """Drops heredoc bodies, which are data, not commands.

    Their lines would otherwise be parsed as commands, and one apostrophe in a
    body makes the whole line unparseable. The `<<TAG` itself stays, so the
    command it feeds is still seen to take a heredoc.

    Raises:
        Unreadable: A heredoc this parser cannot delimit, in a line that looks
            like it publishes: the lines it swallowed may be commands to bash.
    """
    out, tags, quote, i, n = [], [], None, 0, len(command)
    while i < n:
        c = command[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == '\\' and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        elif quote == '"':
            quote = None if c == '"' else quote
        elif c in '\'"':
            quote = c
        elif command.startswith('<<<', i):
            out.append('<<<')
            i += 3
            continue
        elif command.startswith('<<', i):
            m = _HEREDOC_AT.match(command, i)
            if not m and _LOOKS_PUBLISHING.search(command):
                raise Unreadable('a heredoc delimiter this hook cannot parse')
            if m:
                tags.append((m.group(2) or m.group(3) or m.group(4),
                             bool(m.group(1))))
                out.append(m.group(0))
                i = m.end()
                continue
        elif c == '\n' and tags:
            out.append('\n')
            i += 1
            for tag, dash in tags:
                while True:
                    if i >= n:
                        if _LOOKS_PUBLISHING.search(command):
                            raise Unreadable(f'heredoc {tag} never ends')
                        break
                    j = command.find('\n', i)
                    j = n if j < 0 else j
                    line, i = command[i:j], j + 1
                    if (line.lstrip('\t') if dash else line) == tag:
                        break
            tags = []
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _resolve(path: str, cwd: str | None) -> str | None:
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd, path)) if cwd else None


def _simple_commands(command: str, cwd: str | None) -> list[_Simple]:
    """Splits a command line on && || ; | & newline and parentheses.

    Tracks the working directory through `cd`, restoring it when a subshell
    closes.

    Raises:
        ValueError: The line cannot be tokenized (an unclosed quote).
    """
    import shlex  # pylint: disable=import-outside-toplevel
    lex = shlex.shlex(command.replace('\\\n', ' '), posix=True,
                      punctuation_chars=_PUNCT)
    lex.whitespace = ' \t\r'
    lex.whitespace_split = True
    lex.commenters = ''
    tokens = list(lex)

    cmds: list[_Simple] = []
    stack: list[str | None] = []
    cur = _Simple(cwd)
    pending = None

    def finish() -> None:
        nonlocal cur, cwd
        if cur.words or cur.writes or cur.heredoc:
            cmds.append(cur)
            cwd = _after_cd(cur.words, cwd)
        cur = _Simple(cwd)

    for tok in tokens:
        if tok and all(c in _PUNCT for c in tok):
            for op in _OPS.findall(tok):
                if op in ('<<', '<<-'):
                    cur.heredoc, pending = True, 'skip'
                elif op in _WRITE_OPS:
                    pending = 'write'
                elif op in ('>&', '<&'):
                    pending = 'dup'
                elif op in ('<', '<<<'):
                    pending = 'skip'
                elif op == '(':
                    finish()
                    stack.append(cwd)
                elif op == ')':
                    finish()
                    cwd = stack.pop() if stack else cwd
                    cur = _Simple(cwd)
                else:
                    finish()
            continue
        if pending == 'write' or (pending == 'dup' and
                                  not re.fullmatch(r'\d+-?|-', tok)):
            cur.writes.append(tok)
        elif pending is None:
            cur.words.append(tok)
        pending = None
    finish()
    return cmds


def _after_cd(words: list[str], cwd: str | None) -> str | None:
    """The working directory after a simple command."""
    _, argv = _argv(words)
    if not argv or argv[0] not in ('cd', 'pushd', 'popd'):
        return cwd
    args = [a for a in argv[1:] if a not in ('-L', '-P', '--')]
    if argv[0] == 'popd' or (args and (args[0] == '-' or '$' in args[0])):
        return None
    return _resolve(args[0] if args else '~', cwd)


def _argv(words: list[str]) -> tuple[dict[str, str], list[str]]:
    """Assignments, and the program with its arguments past any wrapper."""
    assigns: dict[str, str] = {}
    i = 0
    while i < len(words) and _ASSIGN.match(words[i]):
        key, value = words[i].split('=', 1)
        assigns[key] = value
        i += 1
    while i < len(words) and os.path.basename(words[i]) in _WRAPPERS:
        name = os.path.basename(words[i])
        i += 1
        while i < len(words) and words[i].startswith('-') and words[i] != '-':
            i += 2 if words[i] in _WRAPPERS[name] else 1
        while name == 'env' and i < len(words) and _ASSIGN.match(words[i]):
            key, value = words[i].split('=', 1)
            assigns[key] = value
            i += 1
        if name == 'timeout':
            i += 1
    return assigns, words[i:]


def _literal(value: str, label: str) -> str:
    if '$' in value or '`' in value:
        raise Unreadable(f'{label} holds a shell expansion ($, `), so its '
                         'final text is unknown here')
    return value


def _read_file(path: str, cwd: str | None, label: str,
               pub: Publication) -> None:
    """Adds a file's text to `pub` under `label`, or raises Unreadable."""
    if path == '-':
        raise Unreadable(f'{label} reads stdin')
    _literal(path, label)
    resolved = _resolve(path, cwd)
    if resolved is None:
        raise Unreadable(f'{label} is relative to an unknown directory')
    try:
        with open(resolved, encoding='utf-8', errors='replace') as f:
            pub.texts.append((f'{label} {path}', f.read()))
    except OSError as e:
        raise Unreadable(f'{label} {path} cannot be read ({e.strerror})') from e
    pub.reads.append(resolved)


def _action(args: list[str]) -> int | None:
    """Index of the subcommand's action in `gh <sub> [-R repo] <action> ...`."""
    i = 1
    while i < len(args) and args[i].startswith('-'):
        i += 2 if args[i] in ('-R', '--repo') else 1
    return i if i < len(args) else None


def _split_flag(arg: str, short_valued: set[str]) -> tuple[str, str | None]:
    """'--x=v' -> ('--x', 'v'); '-tv' -> ('-t', 'v') for a valued short flag."""
    if arg.startswith('--') and '=' in arg:
        name, value = arg.split('=', 1)
        return name, value
    if (len(arg) > 2 and arg[0] == '-' and arg[1] != '-'
            and arg[:2] in short_valued):
        return arg[:2], arg[2:].removeprefix('=')
    return arg, None


def _gh_text(args: list[str], cwd: str | None) -> Publication | None:
    """The publication a `gh <args>` call makes, or None for a read-only one."""
    at = _action(args)
    if at is None:
        return None
    key = (args[0], args[at])
    flags = _TEXT_FLAGS.get(key)
    if flags is None:
        return None
    blind = _BLIND_FLAGS.get(key, set())
    what = 'gh ' + ' '.join(key)
    pub = Publication(what)
    short_valued = {f for f in flags if not f.startswith('--')}
    i = at + 1
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == '--':
            break
        name, value = _split_flag(arg, short_valued)
        bundled = re.fullmatch(r'-([A-Za-z]{2,})', arg) and value is None
        if name in blind or (bundled and any(f'-{c}' in blind for c in arg[1:])):
            raise Unreadable(f'{what} {arg} composes the text where this hook '
                             'cannot read it')
        kind = flags.get(name)
        if kind is None:
            continue
        if value is None:
            if i >= len(args):
                continue
            value = args[i]
            i += 1
        if kind == _F:
            _read_file(value, cwd, kind, pub)
        else:
            pub.texts.append((kind, _literal(value, kind)))
    return pub


def _api_text(args: list[str], cwd: str | None) -> Publication | None:
    """The publication a `gh api <args>` call makes, or None."""
    endpoint, fields = None, []
    short_valued = {f for f in (_API_VALUED | set(_API_FIELDS))
                    if not f.startswith('--')}
    i = 1
    while i < len(args):
        arg = args[i]
        i += 1
        name, value = _split_flag(arg, short_valued)
        if name in _API_FIELDS or name in _API_VALUED:
            if value is None and i < len(args):
                value = args[i]
                i += 1
            if name in _API_FIELDS and value is not None:
                fields.append((_API_FIELDS[name], value))
        elif arg.startswith('-') and arg != '-':
            continue
        elif endpoint is None:
            endpoint = arg
    if not fields:
        return None
    if endpoint is None:
        return None
    if '$' in endpoint or '`' in endpoint:
        raise Unreadable('gh api endpoint holds a shell expansion')
    if not _API_TARGET.search(endpoint):
        return None
    pub = Publication('gh api')
    for kind, value in fields:
        if kind == 'input':
            _read_file(value, cwd, '--input', pub)
            continue
        field, _, text = value.partition('=')
        if kind == 'typed' and text.startswith('@'):
            _read_file(text[1:], cwd, f'field {field} from', pub)
        else:
            pub.texts.append((f'field {field}', _literal(text, f'field {field}')))
    return pub


def publications(command: str, cwd: str | None,
                 depth: int = 0) -> list[Publication]:
    """Everything a command line would publish through gh.

    Args:
        command: The Bash tool call's command line.
        cwd: The directory it starts in.
        depth: Nesting of `bash -c` / `eval` strings.

    Returns:
        One Publication per publishing gh command.

    Raises:
        Unreadable: A publishing command's text cannot be known before it runs.
    """
    if depth > _MAX_DEPTH:
        raise Unreadable('the command nests shells too deeply to read')
    try:
        cmds = _simple_commands(_strip_heredocs(command), cwd)
    except ValueError as e:
        if _LOOKS_PUBLISHING.search(command):
            raise Unreadable(f'the command line cannot be parsed ({e})') from e
        return []
    found: list[tuple[int, Publication]] = []
    for k, cmd in enumerate(cmds):
        assigns, argv = _argv(cmd.words)
        if not argv:
            continue
        prog = os.path.basename(argv[0])
        if prog == 'xargs' and 'gh' in map(os.path.basename, argv):
            raise Unreadable('xargs feeds gh from stdin')
        if prog == 'eval':
            found += [(k, p) for p in
                      publications(' '.join(argv[1:]), cmd.cwd, depth + 1)]
            continue
        if prog in _SHELLS:
            flag = next((j for j, a in enumerate(argv[1:], 1)
                         if re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*', a)), None)
            if flag is not None and flag + 1 < len(argv):
                found += [(k, p) for p in
                          publications(argv[flag + 1], cmd.cwd, depth + 1)]
            continue
        if prog != 'gh' or len(argv) < 2:
            continue
        if argv[1] == 'api':
            pub = _api_text(argv[1:], cmd.cwd)
        else:
            pub = _gh_text(argv[1:], cmd.cwd)
        if pub is None:
            continue
        if cmd.heredoc:
            raise Unreadable(f'{pub.what} takes a heredoc')
        pub.patterns = assigns.get('PAPERCLAW_ALLOW_SENSITIVE') != '1'
        found.append((k, pub))
    for k, pub in found:
        _check_writers(cmds[:k], pub)
    return [pub for _, pub in found]


def _check_writers(before: list[_Simple], pub: Publication) -> None:
    """Denies a file read that an earlier command could change after the scan."""
    if not pub.reads:
        return
    names = {os.path.basename(p) for p in pub.reads}
    for cmd in before:
        for target in cmd.writes:
            resolved = _resolve(target, cmd.cwd)
            if resolved in pub.reads or os.path.basename(target) in names:
                raise Unreadable(f'an earlier command writes {target}, which '
                                 f'{pub.what} then reads')
        _, argv = _argv(cmd.words)
        prog = os.path.basename(argv[0]) if argv else ''
        if prog and prog not in _NO_WRITE:
            raise Unreadable(f'{prog} runs before {pub.what} in the same line '
                             'and could change the file it reads')
        if prog not in _NAMES_FILES:
            continue
        for word in argv[1:]:
            if (_resolve(word, cmd.cwd) in pub.reads
                    or os.path.basename(word) in names):
                raise Unreadable(f'{prog} touches {word} before {pub.what} '
                                 'reads it')


def _load_scanner():
    import importlib.util  # pylint: disable=import-outside-toplevel
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    spec = importlib.util.spec_from_file_location(
        'check_pr_body', os.path.join(here, 'check-pr-body.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _titles(scanner) -> list[str]:
    """The paper-title oracle, bounded so the hook never times out open."""
    import threading  # pylint: disable=import-outside-toplevel
    result: list[list[str]] = []
    worker = threading.Thread(
        target=lambda: result.append(scanner.known_section_titles()),
        daemon=True)
    worker.start()
    worker.join(_ORACLE_SECONDS)
    return result[0] if result else []


def _deny(lines: list[str]) -> int:
    print('\n'.join(lines), file=sys.stderr)
    return 2


def guard(command: str, cwd: str | None) -> int:
    """The hook's decision for one command line: 0 allow, 2 deny."""
    try:
        pubs = publications(command, cwd)
    except Unreadable as e:
        return _deny([
            f'guard-gh-publish: blocked, because {e}. This is a PUBLIC repo, '
            'so what gh publishes is scanned first, and this text cannot be.',
            'Write the text to a file in its own tool call, then publish with '
            'a literal --title and --body-file <file> (gh api: -F body=@<file>).',
        ])
    texts = [(pub, label, text) for pub in pubs for label, text in pub.texts]
    if not texts:
        return 0
    scanner = _load_scanner()
    scanner.load_notion_env()
    titles = _titles(scanner)
    needles = scanner.private_values.load_needles()
    problems = []
    for pub, label, text in texts:
        for kind, detail in scanner.scan(text, titles, needles, label,
                                         patterns=pub.patterns):
            problems.append(f'  ✗ {pub.what} {label}: {kind} — {detail}')
    if not problems:
        return 0
    return _deny([
        'guard-gh-publish: blocked — this text must not go to a PUBLIC repo:',
        *problems,
        'Rewrite it: placeholders (<arxiv-id>, "paper A", section 4) instead of '
        'specific papers, and no secrets or personal identifiers.',
        'Only if every PATTERN hit is a false positive, prefix the gh command '
        'with PAPERCLAW_ALLOW_SENSITIVE=1. Private values and paper titles '
        'have no override.',
    ])


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or '{}')
        command = (event.get('tool_input') or {}).get('command') or ''
        cwd = event.get('cwd') or os.getcwd()
    except (ValueError, AttributeError, OSError):
        return 0
    if not isinstance(command, str) or not _MAYBE_GH.search(command):
        return 0
    try:
        return guard(command, cwd)
    except Exception as e:  # pylint: disable=broad-except
        # Never block an ordinary command on a bug here, and never let a bug
        # wave a publishing one through unscanned.
        if _LOOKS_PUBLISHING.search(command):
            return _deny([f'guard-gh-publish: internal error '
                          f'({type(e).__name__}); not publishing unscanned.'])
        return 0


if __name__ == '__main__':
    sys.exit(main())
