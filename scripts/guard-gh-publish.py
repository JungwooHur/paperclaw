#!/usr/bin/env python3
"""PreToolUse hook: scan what a gh command would publish, before it runs.

A git hook sees commits only. A PR or issue title, a body, a comment or a review
goes from `gh` straight to GitHub, so "run check-pr-body.py first" was a rule in
prose, and prose gets skipped. Claude Code runs this before every Bash tool call
(.claude/settings.json); it denies a publishing gh command whose text fails
check-pr-body's scan: patterns, the owner's known private values, and the
paper-title oracle.

Publishing commands:
  gh pr create|new|edit|comment|review|merge|close|reopen, gh issue
  create|new|edit|comment|close|reopen, gh release create|edit, gh gist
  create|edit, gh repo create|edit, gh label create|edit, and every gh api call
  that writes: a method other than GET, or a field or --input without -X GET.
  GraphQL counts only when the document holds a mutation.

A publishing command whose text cannot be read is denied as well (fail closed):
command substitution, backticks, a variable, a heredoc, stdin by any name
(`--body-file -`, /dev/stdin, /dev/fd/N, a process substitution, a FIFO),
a missing file, --fill/--editor/--web/--recover, a file that an earlier part of
the same command line could write first, or a shell or interpreter running code
that mentions gh where this hook cannot read it (a pipe into bash, python -c).

Exit status is the hook protocol's: 0 allows the call, 2 denies it with the
reason on stderr, which names labels only, never the text that matched. A
command with no "gh" in it returns before anything but sys is imported.

PAPERCLAW_ALLOW_SENSITIVE=1 in front of the gh command skips the pattern scan,
as it does for the commit hooks; inherited from the environment it does not.
Known values and paper titles have no override.
"""

from __future__ import annotations

import sys

if __name__ == '__main__':
    # Before EVERY Bash call: decide "not gh" on the raw bytes with only sys
    # imported, a bare interpreter start, and only then pay for the rest.
    _RAW = sys.stdin.buffer.read()
    if b'gh' not in _RAW:
        sys.exit(0)

# pylint: disable=wrong-import-position
import base64  # noqa: E402
import dataclasses  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import stat  # noqa: E402

_MAYBE_GH = re.compile(r'\bgh\b')
# For what this parser cannot follow: deny only what may be a publishing call.
_LOOKS_PUBLISHING = re.compile(
    r'\bgh\b[^\n]*?\b(?:(?:pr|issue|release|gist|repo|label)\b[^\n]*?'
    r'\b(?:create|new|edit|comment|review|merge|close|reopen)\b|api\b)')

# A `$` or backtick inside single quotes is literal. They are swapped for these
# before tokenizing, so that a real expansion is a real `$` in a word, and
# swapped back before a word is read or scanned.
_LIT_DOLLAR, _LIT_TICK, _NOT_HEREDOC, _PROCSUB = (
    '', '', '', '')
_RESTORE = str.maketrans({_LIT_DOLLAR: '$', _LIT_TICK: '`',
                          _NOT_HEREDOC: '<'})

# Text-bearing flags per publishing subcommand: flag -> what it carries.
_T, _B, _F, _D = 'title', 'body', 'body file', 'description'
_CREATE = {'-t': _T, '--title': _T, '-b': _B, '--body': _B,
           '-F': _F, '--body-file': _F}
_COMMENT = {'-b': _B, '--body': _B, '-F': _F, '--body-file': _F}
_MERGE = {'-t': _T, '--subject': _T, '-b': _B, '--body': _B,
          '-F': _F, '--body-file': _F}
_RELEASE = {'-t': _T, '--title': _T, '-n': _B, '--notes': _B,
            '-F': _F, '--notes-file': _F}
_CLOSE = {'-c': _B, '--comment': _B}
_REPO = {'-d': _D, '--description': _D, '-h': 'homepage',
         '--homepage': 'homepage'}
_LABEL = {'-d': _D, '--description': _D}
_EDITOR = frozenset({'-e', '--editor', '-w', '--web'})


@dataclasses.dataclass(frozen=True)
class _Spec:
    """How one publishing gh subcommand carries its text.

    Attributes:
        flags: Text-bearing flag -> what it carries.
        valued: Other flags that take a value, so the value is no positional.
        blind: Flags that compose the text where this hook cannot see it.
        positional: 'name' when the first positional is published text,
            'files' when every positional is a file whose content is.
    """

    flags: dict[str, str]
    valued: frozenset[str] = frozenset()
    blind: frozenset[str] = frozenset()
    positional: str | None = None


_SPECS = {
    ('pr', 'create'): _Spec(_CREATE, blind=_EDITOR | {
        '-f', '--fill', '--fill-first', '--fill-verbose', '--recover'}),
    ('pr', 'edit'): _Spec(_CREATE),
    ('pr', 'comment'): _Spec(_COMMENT, blind=_EDITOR),
    ('pr', 'review'): _Spec(_COMMENT),
    ('pr', 'merge'): _Spec(_MERGE),
    ('pr', 'close'): _Spec(_CLOSE),
    ('pr', 'reopen'): _Spec(_CLOSE),
    ('issue', 'create'): _Spec(_CREATE, blind=_EDITOR | {'--recover'}),
    ('issue', 'edit'): _Spec(_CREATE),
    ('issue', 'comment'): _Spec(_COMMENT, blind=_EDITOR),
    ('issue', 'close'): _Spec(_CLOSE, valued=frozenset({'-r', '--reason'})),
    ('issue', 'reopen'): _Spec(_CLOSE),
    ('release', 'create'): _Spec(_RELEASE,
                                 blind=frozenset({'--notes-from-tag'})),
    ('release', 'edit'): _Spec(_RELEASE, blind=frozenset({'--notes-from-tag'})),
    ('gist', 'create'): _Spec({'-d': _D, '--desc': _D},
                              valued=frozenset({'-f', '--filename'}),
                              blind=frozenset({'-w', '--web'}),
                              positional='files'),
    ('gist', 'edit'): _Spec({'-d': _D, '--desc': _D, '-a': _F, '--add': _F},
                            valued=frozenset({'-f', '--filename', '-r',
                                              '--remove'})),
    ('repo', 'create'): _Spec(_REPO, valued=frozenset({
        '-g', '--gitignore', '-l', '--license', '-p', '--template', '-r',
        '--remote', '-s', '--source', '-t', '--team'}), positional='name'),
    ('repo', 'edit'): _Spec(_REPO),
    ('label', 'create'): _Spec(_LABEL, valued=frozenset({'-c', '--color'}),
                               positional='name'),
    ('label', 'edit'): _Spec(dict(_LABEL, **{'-n': 'name', '--name': 'name'}),
                             valued=frozenset({'-c', '--color'})),
}
_ALIASES = {'new': 'create'}

_API_FIELDS = {'-f': 'raw', '--raw-field': 'raw', '-F': 'typed',
               '--field': 'typed', '--input': 'input'}
_API_VALUED = {'-X', '--method', '-H', '--header', '-q', '--jq', '-t',
               '--template', '--hostname', '-p', '--preview', '--cache'}
_READ_METHODS = {'GET', 'HEAD'}

# Wrappers that run their arguments as a command, with the flags that take a
# value of their own, how many positionals precede the command, flags whose
# value is a shell command, and flags that change the directory.
_WRAPPERS = {
    'env': {'-u', '--unset', '-C', '--chdir', '-S', '--split-string'},
    'command': set(), 'builtin': set(), 'exec': {'-a'}, 'nohup': set(),
    'time': {'-f', '-o'}, 'nice': {'-n'}, 'stdbuf': {'-i', '-o', '-e'},
    'timeout': {'-s', '--signal', '-k', '--kill-after'},
    'sudo': {'-u', '-g', '-C', '-D', '--chdir', '-h', '-p', '-r', '-t', '-U'},
    'doas': {'-u', '-C'}, 'setsid': set(), 'chronic': set(),
    'ionice': {'-c', '--class', '-n', '--classdata', '-p', '--pid', '-P',
               '--pgid', '-u', '--uid'},
    'taskset': set(), 'flock': {'-w', '--timeout', '-E',
                                '--conflict-exit-code'},
    'watch': {'-n', '--interval'},
}
_WRAPPER_POSITIONALS = {'timeout': 1, 'taskset': 1, 'flock': 1}
_WRAPPER_SHELL = {'flock': {'-c', '--command'}, 'env': {'-S', '--split-string'}}
_WRAPPER_CHDIR = {'env': {'-C', '--chdir'}, 'sudo': {'-D', '--chdir'}}
# Words that open a compound command; what follows them is the command.
_RESERVED = {'{', '}', '!', 'if', 'then', 'else', 'elif', 'fi', 'do', 'done',
             'while', 'until'}
_SHELLS = {'bash', 'sh', 'dash', 'zsh', 'ksh'}
# Programs that run code from their arguments or stdin, where a gh call they
# make cannot be parsed here.
_EXECUTORS = re.compile(
    r'(python|pypy)[0-9.]*|node(js)?|deno|bun|perl|ruby|php|lua[0-9.]*|'
    r'osascript|pwsh|powershell|ssh|su|script|parallel|busybox|tclsh|expect')
# Programs that never run their arguments, so a "gh" among them is just a word.
_NON_EXEC = {'echo', 'printf', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack',
             'git', 'man', 'which', 'whereis', 'type', 'cat', 'less', 'more',
             'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'tr', 'sed', 'awk',
             'jq', 'diff', 'test', '[', 'ls', 'stat', 'file', 'tee'}

# Programs that do not rewrite a file unless redirected, which is checked
# separately. Anything else that runs before a gh command reading a file could
# change that file between this scan and the read.
_NO_WRITE = {'cd', 'pushd', 'popd', 'true', 'false', ':', 'echo', 'printf',
             'test', '[', 'ls', 'pwd', 'sleep', 'cat', 'head', 'tail', 'grep',
             'rg', 'wc', 'jq', 'export', 'unset', 'which', 'type', 'date'}
# git subcommands that leave the work tree alone (checkout, stash, pull, merge
# and the like rewrite it).
_GIT_READ_ONLY = {
    'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'branch',
    'remote', 'push', 'fetch', 'add', 'commit', 'config', 'describe',
    'blame', 'grep', 'shortlog', 'rev-list', 'cat-file', 'merge-base', 'tag',
    'var', 'ls-remote', 'reflog', 'name-rev', 'for-each-ref', 'symbolic-ref',
    'check-ignore', 'help', 'version', 'count-objects'}
_GIT_VALUED = {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}
# gh groups that do not run extension code; within them, the actions that
# write local files.
_GH_GROUPS = {'pr', 'issue', 'release', 'api', 'label', 'gist', 'repo',
              'search', 'run', 'workflow', 'status', 'browse', 'auth',
              'secret', 'variable', 'cache', 'ruleset', 'project', 'alias'}
_GH_WRITES = {'checkout', 'clone', 'sync', 'fork', 'download', 'cp',
              'install', 'upgrade', 'set'}
_NAMES_FILES = {'git', 'gh'}

_PUNCT = ';&|()<>\n'
_OPS = re.compile(r'&>>|&>|>>|>\||>&|<&|<<<|<<-|<<|<>|\|\||&&|;;|\|&|'
                  r'[;&|()<>\n]')
_WRITE_OPS = {'>', '>>', '>|', '&>', '&>>', '<>'}
_ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_HEREDOC_AT = re.compile(
    r'<<(-?)[ \t]*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([A-Za-z_][A-Za-z0-9_.-]*))'
    r'(?=[\s;&|()<>]|$)')
_MUTATION = re.compile(r'(?<![\w$])mutation\b')
_MAX_DEPTH = 4
_ORACLE_SECONDS = 120


class Unreadable(Exception):
    """A publishing command whose text this hook cannot read."""


@dataclasses.dataclass
class _Simple:
    """One simple command of a command line.

    Attributes:
        cwd: Its working directory, None when unknown.
        words: Its words, unquoted, with the literal-`$` sentinels.
        writes: Targets of its output redirections.
        heredoc: Whether it takes a heredoc.
        bodies: (text, quoted delimiter) of each heredoc it takes.
        herestrings: The words it takes as here-strings.
        stdin: Files its stdin is redirected from.
        piped_in: Whether its stdin is a pipe.
    """

    cwd: str | None
    words: list[str] = dataclasses.field(default_factory=list)
    writes: list[str] = dataclasses.field(default_factory=list)
    heredoc: bool = False
    bodies: list[tuple[str, bool]] = dataclasses.field(default_factory=list)
    herestrings: list[str] = dataclasses.field(default_factory=list)
    stdin: list[str] = dataclasses.field(default_factory=list)
    piped_in: bool = False


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


def _restore(word: str) -> str:
    return word.translate(_RESTORE)


def _expands(word: str) -> bool:
    """Whether the shell would expand something in `word`."""
    return '$' in word or '`' in word or _PROCSUB in word


def _prepare(command: str) -> tuple[str, list[tuple[str, bool]]]:
    """Readies a command line for tokenizing.

    Heredoc bodies are data, not commands: they are cut out (one apostrophe in
    a body would make the line unparseable) and returned, so a shell that reads
    one can still be followed. The `<<TAG` stays. A literal `$` or backtick
    (single-quoted, or escaped) becomes a sentinel. `$(` opens a fresh quoting
    context, as in the shell, so a heredoc inside "$(...)" is found too.

    Returns:
        The line, and (text, quoted delimiter) for each heredoc that the
        tokenizer will see as an operator, in order.

    Raises:
        Unreadable: A heredoc this parser cannot delimit, in a line that looks
            like it publishes: the lines it swallowed may be commands to bash.
    """
    looks = bool(_LOOKS_PUBLISHING.search(command))
    out: list[str] = []
    bodies: list[tuple[str, bool]] = []
    tags: list[tuple[str, bool, bool, bool]] = []
    stack: list[list] = []  # [quote outside the $( ... ), open parens]
    quote, i, n = None, 0, len(command)
    while i < n:
        c = command[i]
        if quote == "'":
            if c == "'":
                quote = None
            out.append({'$': _LIT_DOLLAR, '`': _LIT_TICK}.get(c, c))
            i += 1
            continue
        if c == '\\' and i + 1 < n:
            nxt = command[i + 1]
            if nxt in '$`':
                out.append(_LIT_DOLLAR if nxt == '$' else _LIT_TICK)
            else:
                out.append(command[i:i + 2])
            i += 2
            continue
        if command.startswith('$(', i) and not command.startswith('$((', i):
            stack.append([quote, 0])
            quote = None
            out.append('$(')
            i += 2
            continue
        if quote == '"':
            if c == '"':
                quote = None
            out.append(c)
            i += 1
            continue
        if c in '\'"':
            quote = c
        elif c == '(' and stack:
            stack[-1][1] += 1
        elif c == ')' and stack:
            if stack[-1][1]:
                stack[-1][1] -= 1
            else:
                quote = stack.pop()[0]
        elif command.startswith('<<<', i):
            out.append('<<<')
            i += 3
            continue
        elif command.startswith('<<', i):
            m = _HEREDOC_AT.match(command, i)
            if m:
                tag = m.group(2) or m.group(3) or m.group(4)
                quoted = m.group(4) is None or m.group(0).find('\\') >= 0
                # Inside "...$(...)" the tokenizer sees one quoted word, no
                # operator, so the body must not be handed to a command.
                seen = all(saved is None for saved, _ in stack)
                tags.append((tag, bool(m.group(1)), quoted, seen))
                out.append(m.group(0))
                i = m.end()
                continue
            if looks:
                raise Unreadable('a heredoc delimiter this hook cannot parse')
            out.append(_NOT_HEREDOC * 2)  # an arithmetic shift, $((1<<2))
            i += 2
            continue
        elif c == '\n' and tags:
            out.append('\n')
            i += 1
            for tag, dash, quoted, seen in tags:
                lines = []
                while True:
                    if i >= n:
                        if looks:
                            raise Unreadable(f'heredoc {tag} never ends')
                        break
                    j = command.find('\n', i)
                    j = n if j < 0 else j
                    line, i = command[i:j], j + 1
                    if (line.lstrip('\t') if dash else line) == tag:
                        break
                    lines.append(line)
                if seen:
                    bodies.append(('\n'.join(lines), quoted))
            tags = []
            continue
        out.append(c)
        i += 1
    return ''.join(out), bodies


def _resolve(path: str, cwd: str | None) -> str | None:
    path = os.path.expanduser(_restore(path))
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd, path)) if cwd else None


def _simple_commands(command: str, cwd: str | None,
                     bodies: list[tuple[str, bool]]) -> list[_Simple]:
    """Splits a prepared command line into simple commands, in run order.

    Splits on && || ; | & newline and parentheses. Tracks the working directory
    through `cd`, restoring it when a subshell closes; a `cd` in a pipeline or
    a background job runs in a subshell of its own, so it moves nothing. The
    commands of a `$(...)` or `<(...)` come before the command they are part
    of, which continues after them.

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
    frames: list[tuple[_Simple | None, str | None]] = []
    cur = _Simple(cwd)
    pending = None
    next_body = 0

    def finish(sep: str) -> None:
        nonlocal cur, cwd
        if (cur.words or cur.writes or cur.heredoc or cur.herestrings
                or cur.stdin):
            cmds.append(cur)
            if not cur.piped_in and sep not in ('&', '|', '|&'):
                cwd = _after_cd(cur.words, cwd)
        cur = _Simple(cwd, piped_in=sep in ('|', '|&'))

    for tok in tokens:
        if tok and all(c in _PUNCT for c in tok):
            for op in _OPS.findall(tok):
                if op in ('<<', '<<-'):
                    cur.heredoc, pending = True, 'skip'
                    if next_body < len(bodies):
                        cur.bodies.append(bodies[next_body])
                        next_body += 1
                elif op == '<<<':
                    pending = 'herestring'
                elif op in _WRITE_OPS:
                    pending = 'write'
                elif op in ('>&', '<&'):
                    pending = 'dup'
                elif op == '<':
                    pending = 'stdin'
                elif op == '(' and pending in ('stdin', 'write'):
                    # <(...) or >(...): a file name that is a command's pipe
                    cur.words.append(_PROCSUB)
                    pending = None
                    frames.append((cur, cwd))
                    cur = _Simple(cwd)
                elif op == '(' and cur.words and cur.words[-1].endswith('$'):
                    frames.append((cur, cwd))  # $(...) inside a word
                    cur = _Simple(cwd)
                elif op == '(':
                    finish('(')
                    frames.append((None, cwd))
                elif op == ')':
                    finish(')')
                    outer, saved = frames.pop() if frames else (None, cwd)
                    cwd = saved
                    cur = outer if outer is not None else _Simple(cwd)
                else:
                    finish(op)
            continue
        if pending == 'write' or (pending == 'dup' and
                                  not re.fullmatch(r'\d+-?|-', tok)):
            cur.writes.append(tok)
        elif pending == 'stdin':
            cur.stdin.append(tok)
        elif pending == 'herestring':
            cur.herestrings.append(tok)
        elif pending is None:
            cur.words.append(tok)
        pending = None
    finish('')
    return cmds


def _after_cd(words: list[str], cwd: str | None) -> str | None:
    """The working directory after a simple command."""
    _, argv, _ = _argv(words)
    if not argv or argv[0] not in ('cd', 'pushd', 'popd'):
        return cwd
    args = [a for a in argv[1:] if a not in ('-L', '-P', '--')]
    if argv[0] == 'popd' or (args and (args[0] == '-' or _expands(args[0]))):
        return None
    return _resolve(args[0] if args else '~', cwd)


def _argv(words: list[str]
          ) -> tuple[dict[str, str], list[str], str | None]:
    """Assignments, the program with its arguments past any wrapper, and the
    directory a wrapper moves to (None when it does not)."""
    assigns: dict[str, str] = {}
    chdir = None
    i = 0
    while i < len(words) and words[i] in _RESERVED:
        i += 1
    while i < len(words) and _ASSIGN.match(words[i]):
        key, value = words[i].split('=', 1)
        assigns[key] = value
        i += 1
    while i < len(words) and os.path.basename(words[i]) in _WRAPPERS:
        name = os.path.basename(words[i])
        i += 1
        for _ in range(2 if name == 'flock' else 1):
            while i < len(words) and words[i].startswith('-') and \
                    words[i] != '-':
                flag = words[i]
                value = words[i + 1] if i + 1 < len(words) else ''
                if flag in _WRAPPER_SHELL.get(name, ()):
                    return assigns, ['sh', '-c', value], chdir
                if flag in _WRAPPER_CHDIR.get(name, ()):
                    chdir = value
                i += 2 if flag in _WRAPPERS[name] else 1
            while name == 'env' and i < len(words) and _ASSIGN.match(words[i]):
                key, value = words[i].split('=', 1)
                assigns[key] = value
                i += 1
            if not _:
                i += _WRAPPER_POSITIONALS.get(name, 0)
    return assigns, words[i:], chdir


def _literal(value: str, label: str) -> str:
    """`value` as the shell passes it, or Unreadable if it expands."""
    if _PROCSUB in value:
        raise Unreadable(f'{label} is a process substitution')
    if _expands(value):
        raise Unreadable(f'{label} holds a shell expansion ($, `), so its '
                         'final text is unknown here')
    return _restore(value)


def _read_file(path: str, cwd: str | None, label: str,
               pub: Publication) -> str:
    """Adds a file's text to `pub` under `label` and returns it.

    Raises:
        Unreadable: The file is stdin by any name, a device, a FIFO, missing,
            or relative to an unknown directory: whatever gh reads from it
            later is not what this hook could read now.
    """
    if path == '-':
        raise Unreadable(f'{label} reads stdin')
    path = _literal(path, label)
    resolved = _resolve(path, cwd)
    if resolved is None:
        raise Unreadable(f'{label} is relative to an unknown directory')
    real = os.path.realpath(resolved)
    if real.startswith(('/dev/', '/proc/', '/sys/')):
        raise Unreadable(f'{label} {path} is a device or process file '
                         '(stdin, by another name)')
    try:
        if not stat.S_ISREG(os.stat(resolved).st_mode):
            raise Unreadable(f'{label} {path} is not a regular file')
        with open(resolved, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError as e:
        raise Unreadable(f'{label} {path} cannot be read ({e.strerror})') from e
    pub.texts.append((f'{label} {path}', text))
    pub.reads.append(resolved)
    return text


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
    key = (args[0], _ALIASES.get(args[at], args[at]))
    spec = _SPECS.get(key)
    if spec is None:
        return None
    what = 'gh ' + ' '.join(key)
    pub = Publication(what)
    short_valued = {f for f in (*spec.flags, *spec.valued)
                    if not f.startswith('--')}
    positionals, given = [], set()
    i = at + 1
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == '--':
            positionals += args[i:]
            break
        name, value = _split_flag(arg, short_valued)
        bundled = re.fullmatch(r'-([A-Za-z]{2,})', arg) and value is None
        if name in spec.blind or (
                bundled and any(f'-{c}' in spec.blind for c in arg[1:])):
            raise Unreadable(f'{what} {arg} composes the text where this hook '
                             'cannot read it')
        kind = spec.flags.get(name)
        if kind is None:
            if name in spec.valued and value is None:
                i += 1
            elif not arg.startswith('-') or arg == '-':
                positionals.append(arg)
            continue
        if value is None:
            if i >= len(args):
                raise Unreadable(f'{what} {arg} has no value this hook can '
                                 'see')
            value = args[i]
            i += 1
        given.add(kind)
        if kind == _F:
            _read_file(value, cwd, kind, pub)
        else:
            pub.texts.append((kind, _literal(value, kind)))
    if spec.positional == 'name' and positionals:
        pub.texts.append(('name', _literal(positionals[0], 'name')))
    elif spec.positional == 'files':
        if not positionals or '-' in positionals:
            raise Unreadable(f'{what} reads stdin')
        for path in positionals:
            _read_file(path, cwd, 'file', pub)
    if key == ('gist', 'edit') and not given:
        raise Unreadable(f'{what} opens an editor')
    return pub


def _unbase64(text: str) -> str | None:
    """`text` base64-decoded, when it is base64 (a contents API body)."""
    compact = re.sub(r'\s+', '', text)
    if len(compact) < 8 or not re.fullmatch(r'[A-Za-z0-9+/_-]+=*', compact):
        return None
    alt = b'-_' if re.search(r'[-_]', compact) else None
    try:
        raw = base64.b64decode(compact + '=' * (-len(compact) % 4),
                               altchars=alt)
    except ValueError:
        return None
    return raw.replace(b'\0', b'').decode('utf-8', 'replace')


def _json_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


def _add_input(path: str, cwd: str | None, pub: Publication):
    """Reads an --input file; returns its JSON, or None when it is not JSON.

    The JSON's strings are scanned decoded too: an escape such as \\u0040 hides
    a pattern from the raw text, and a `content` field may be base64.
    """
    text = _read_file(path, cwd, '--input', pub)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    pub.texts.append(('--input decoded', '\n'.join(_json_strings(data))))
    if isinstance(data, dict) and isinstance(data.get('content'), str):
        decoded = _unbase64(data['content'])
        if decoded:
            pub.texts.append(('--input content, base64-decoded', decoded))
    return data


def _add_field(kind: str, value: str, cwd: str | None,
               pub: Publication) -> str:
    """Adds one gh api field's text to `pub`; returns the field's name."""
    field, _, text = value.partition('=')
    field = _literal(field, 'a field name')
    if kind == 'typed' and text.startswith('@'):
        text = _read_file(text[1:], cwd, f'field {field} from', pub)
    else:
        label = f'field {field}'
        text = _literal(text, label)
        pub.texts.append((label, text))
    if field == 'content':
        decoded = _unbase64(text)
        if decoded:
            pub.texts.append((f'field {field}, base64-decoded', decoded))
    return field


def _reads_only(document: str) -> bool:
    """Whether a GraphQL document can only read.

    Its start must be literal and a query, and no `mutation` may appear: then
    an expansion later in it can at most add a second operation, which
    GraphQL refuses to run without an operationName, checked separately.
    """
    start = re.sub(r'^(?:\s+|#[^\n]*)*', '', document)
    if not (start.startswith('{') or re.match(r'query\b', start)):
        return False
    return not _MUTATION.search(_restore(document))


def _graphql(fields: list[tuple[str, str]],
             cwd: str | None) -> Publication | None:
    """The publication a `gh api graphql` call makes, or None for a query."""
    probe = Publication('gh api graphql')
    names = [_restore(v.partition('=')[0]) for k, v in fields if k != 'input']
    document = None
    for kind, value in fields:
        name, _, text = value.partition('=')
        if kind == 'input':
            data = _add_input(value, cwd, probe)
            if isinstance(data, dict) and 'operationName' not in data:
                document = data.get('query') if isinstance(
                    data.get('query'), str) else None
        elif _restore(name) == 'query':
            if kind == 'typed' and text.startswith('@'):
                document = _read_file(text[1:], cwd, 'field query from', probe)
            else:
                document = text
    if (document is not None and 'operationName' not in names
            and _reads_only(document)):
        return None
    pub = Publication('gh api graphql')
    for kind, value in fields:
        if kind == 'input':
            _add_input(value, cwd, pub)
        else:
            _add_field(kind, value, cwd, pub)
    return pub


def _api_text(args: list[str], cwd: str | None) -> Publication | None:
    """The publication a `gh api <args>` call makes, or None."""
    endpoint, method, fields = None, None, []
    short_valued = {f for f in (_API_VALUED | set(_API_FIELDS))
                    if not f.startswith('--')}
    i = 1
    while i < len(args):
        arg = args[i]
        i += 1
        name, value = _split_flag(arg, short_valued)
        if name in _API_FIELDS or name in _API_VALUED:
            if value is None:
                if i >= len(args):
                    if name in _API_FIELDS:
                        raise Unreadable(f'gh api {name} has no value this '
                                         'hook can see')
                    continue
                value = args[i]
                i += 1
            if name in _API_FIELDS:
                fields.append((_API_FIELDS[name], value))
            elif name in ('-X', '--method'):
                method = value
        elif arg.startswith('-') and arg != '-':
            continue
        elif endpoint is None:
            endpoint = arg
    if endpoint is None:
        return None
    if method is not None:
        verb = _literal(method, 'gh api -X').upper()
    else:
        verb = 'POST' if fields else 'GET'
    if _restore(endpoint).lstrip('/').split('?')[0] == 'graphql':
        return _graphql(fields, cwd)
    if verb in _READ_METHODS:
        return None
    pub = Publication(f'gh api {verb}')
    pub.texts.append(('endpoint', _literal(endpoint, 'gh api endpoint')))
    for kind, value in fields:
        if kind == 'input':
            _add_input(value, cwd, pub)
        else:
            _add_field(kind, value, cwd, pub)
    return pub


def _gh_call(argv: list[str], cmd: _Simple, assigns: dict[str, str],
             cwd: str | None) -> Publication | None:
    """The publication of `gh ...` in `cmd`, or None for a read-only call."""
    if len(argv) < 2:
        return None
    if argv[1] == 'api':
        pub = _api_text(argv[1:], cwd)
    else:
        pub = _gh_text(argv[1:], cwd)
    if pub is None:
        return None
    if cmd.heredoc or cmd.herestrings:
        raise Unreadable(f'{pub.what} takes a heredoc')
    pub.patterns = _restore(assigns.get('PAPERCLAW_ALLOW_SENSITIVE', '')) != '1'
    return pub


def _shell(cmd: _Simple, argv: list[str], looks: bool,
           depth: int) -> list[Publication]:
    """What a shell run by `cmd` publishes: its -c string, or its stdin."""
    i, from_stdin = 1, False
    while i < len(argv) and argv[i][:1] in ('-', '+') and argv[i] != '-':
        if re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*', argv[i]):
            if i + 1 >= len(argv):
                return []
            return publications(_restore(argv[i + 1]), cmd.cwd, depth + 1)
        from_stdin |= 's' in argv[i][1:] and argv[i][0] == '-'
        i += 2 if argv[i] in ('-o', '+o', '-O', '+O') else 1
    if i < len(argv) and argv[i] != '-' and not from_stdin:
        return []  # a script file, which is not on this line
    found = []
    for body, quoted in cmd.bodies:
        if not quoted and _expands(body) and _LOOKS_PUBLISHING.search(body):
            raise Unreadable('a shell reads a heredoc whose expansions are '
                             'unknown here')
        found += publications(body, cmd.cwd, depth + 1)
    for code in cmd.herestrings:
        if _expands(code) and _LOOKS_PUBLISHING.search(_restore(code)):
            raise Unreadable('a shell reads a here-string whose expansions '
                             'are unknown here')
        found += publications(_restore(code), cmd.cwd, depth + 1)
    if (cmd.piped_in or cmd.stdin) and looks:
        raise Unreadable('a shell reads commands from stdin, which this hook '
                         'cannot see')
    return found


def _command(cmd: _Simple, looks: bool, depth: int) -> list[Publication]:
    """What one simple command publishes through gh."""
    assigns, argv, chdir = _argv(cmd.words)
    if not argv:
        return []
    cwd = _resolve(chdir, cmd.cwd) if chdir is not None else cmd.cwd
    if _expands(argv[0]) and _LOOKS_PUBLISHING.search(
            'gh ' + ' '.join(argv[1:])):
        raise Unreadable('the program is a shell expansion, and what follows '
                         'it reads like a gh call')
    prog = os.path.basename(_restore(argv[0]))
    if prog == 'xargs' and 'gh' in map(os.path.basename, argv):
        raise Unreadable('xargs feeds gh from stdin')
    if prog == 'eval':
        return publications(' '.join(map(_restore, argv[1:])), cwd, depth + 1)
    if prog in _SHELLS:
        return _shell(cmd, argv, looks, depth)
    if prog == 'gh':
        pub = _gh_call(argv, cmd, assigns, cwd)
        return [pub] if pub else []
    if prog not in _NON_EXEC:
        # A program that runs a command given as its arguments: find -exec,
        # flock, ssh, anything not listed as a wrapper.
        for j in range(1, len(argv)):
            if os.path.basename(argv[j]) == 'gh':
                pub = _gh_call(argv[j:], cmd, assigns, cwd)
                if pub:
                    return [pub]
    if _EXECUTORS.fullmatch(prog):
        code = ' '.join([*argv[1:], *(b for b, _ in cmd.bodies),
                         *cmd.herestrings])
        if _LOOKS_PUBLISHING.search(_restore(code)):
            raise Unreadable(f'{prog} runs code that mentions gh, which this '
                             'hook cannot follow')
    return []


def publications(command: str, cwd: str | None,
                 depth: int = 0) -> list[Publication]:
    """Everything a command line would publish through gh.

    Args:
        command: The Bash tool call's command line.
        cwd: The directory it starts in.
        depth: Nesting of `bash -c` / `eval` strings and heredocs.

    Returns:
        One Publication per publishing gh command.

    Raises:
        Unreadable: A publishing command's text cannot be known before it runs.
    """
    if depth > _MAX_DEPTH:
        raise Unreadable('the command nests shells too deeply to read')
    looks = bool(_LOOKS_PUBLISHING.search(command))
    try:
        prepared, bodies = _prepare(command)
        cmds = _simple_commands(prepared, cwd, bodies)
    except ValueError as e:
        if looks:
            raise Unreadable(f'the command line cannot be parsed ({e})') from e
        return []
    found = [(k, pub) for k, cmd in enumerate(cmds)
             for pub in _command(cmd, looks, depth)]
    for k, pub in found:
        _check_writers(cmds[:k], pub)
    return [pub for _, pub in found]


def _only_reads(argv: list[str]) -> bool:
    """Whether a command leaves files alone (redirections aside)."""
    prog = os.path.basename(_restore(argv[0]))
    if prog in _NO_WRITE:
        return True
    if prog == 'git':
        i = 1
        while i < len(argv) and argv[i].startswith('-'):
            i += 2 if argv[i] in _GIT_VALUED else 1
        return i < len(argv) and argv[i] in _GIT_READ_ONLY
    if prog == 'gh':
        at = _action(argv[1:]) if len(argv) > 1 else None
        return (len(argv) > 1 and argv[1] in _GH_GROUPS
                and (at is None or argv[1 + at] not in _GH_WRITES))
    # The scan itself, run by hand before publishing.
    return (re.fullmatch(r'python[0-9.]*', prog) is not None
            and len(argv) > 1
            and os.path.basename(argv[1]) == 'check-pr-body.py')


def _check_writers(before: list[_Simple], pub: Publication) -> None:
    """Denies a file read that an earlier command could change first."""
    if not pub.reads:
        return
    names = {os.path.basename(p) for p in pub.reads}
    for cmd in before:
        for target in cmd.writes:
            resolved = _resolve(target, cmd.cwd)
            if resolved in pub.reads or os.path.basename(target) in names:
                raise Unreadable(f'an earlier command writes {target}, which '
                                 f'{pub.what} then reads')
        _, argv, _ = _argv(cmd.words)
        if not argv:
            continue
        prog = os.path.basename(_restore(argv[0]))
        if not _only_reads(argv):
            raise Unreadable(f'{prog} runs before {pub.what} in the same line '
                             'and could change the file it reads')
        if prog not in _NAMES_FILES:
            continue
        for word in argv[1:]:
            if (_resolve(word, cmd.cwd) in pub.reads
                    or os.path.basename(_restore(word)) in names):
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
        target=lambda: result.append(scanner.section_titles()), daemon=True)
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
            'Write the text to a file in its own tool call, then publish '
            'with a literal --title and --body-file <file> (gh api: '
            '-F body=@<file>), in a call of its own.',
        ])
    texts = [(pub, label, text) for pub in pubs for label, text in pub.texts]
    if not texts:
        return 0
    scanner = _load_scanner()
    scanner.load_notion_env()
    # A text too short to hold any usable title needs no oracle.
    long_enough = any(scanner.could_hold_a_title(text) for _, _, text in texts)
    titles = _titles(scanner) if long_enough else []
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
        'Rewrite it: placeholders (<arxiv-id>, "paper A", section 4) '
        'instead of specific papers, and no secrets or personal '
        'identifiers.',
        'Only if every PATTERN hit is a false positive, prefix the gh command '
        'with PAPERCLAW_ALLOW_SENSITIVE=1. Private values and paper titles '
        'have no override.',
    ])


def main(raw: bytes | None = None) -> int:
    """Reads the hook event (or `raw`) and returns the exit status."""
    try:
        if raw is None:
            raw = sys.stdin.buffer.read()
        event = json.loads(raw or b'{}')
        command = (event.get('tool_input') or {}).get('command') or ''
        cwd = event.get('cwd') or os.getcwd()
    except (ValueError, AttributeError, OSError):
        return 0
    if not isinstance(command, str) or not _MAYBE_GH.search(command):
        return 0
    if not isinstance(cwd, str):
        cwd = None
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
    sys.exit(main(_RAW))
