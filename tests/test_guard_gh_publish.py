"""The PreToolUse guard: which gh commands publish, what text they carry, and
the decision. Exit 0 allows the Bash call; exit 2 denies it."""
import base64
import json
import os
import subprocess

import pytest

from leak_helpers import (REPO, SCRIPTS, apply_env, clean_env, known_value,
                          load_script, pattern_secret)

guard = load_script("guard-gh-publish.py")
HOOK = ["python3", os.path.join(SCRIPTS, "guard-gh-publish.py")]


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    """A working directory with body files, and an environment with no real
    private values, no Notion and one known private value."""
    apply_env(monkeypatch, clean_env(tmp_path))
    (tmp_path / "private-values").write_text(known_value() + "\n")
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "clean.md").write_text("## Summary\nTidy the scanner.\n")
    (work / "leak.md").write_text(f"## Summary\n{pattern_secret()}\n")
    (work / "private.md").write_text(f"see {known_value()}\n")
    (work / "sub" / "clean.md").write_text(f"{pattern_secret()}\n")
    # leaky where the command starts, clean one directory down
    (work / "swap.md").write_text(f"{pattern_secret()}\n")
    (work / "sub" / "swap.md").write_text("clean\n")
    (work / "secret.json").write_text(
        json.dumps({"body": pattern_secret()}) + "\n")
    os.symlink("/dev/stdin", work / "stdin.md")
    return str(work)


@pytest.mark.parametrize("command", [
    "ls -la",
    "git status && echo gh",
    "git checkout gh-pages",
    "gh pr view 12",
    "gh pr list --json title,body",
    "gh api repos/o/r/pulls/1",
    "gh pr create --title 'Tidy the scanner' --body-file clean.md",
    "gh api -X PATCH repos/o/r/pulls/1 -F body=@clean.md",
    "gh pr comment 5 --body 'Looks good'",
    "gh pr merge 5 --squash",
    "git push -u origin feat && gh pr create --title t --body-file clean.md",
    "gh pr create --title t --body x 2>&1 | tail -3",
    "git commit -m \"$(cat <<'EOF'\nit's a note about gh pr create\nEOF\n)\"",
    "cat <<'EOF'\ngh pr comment 1 --body \"$(cat leak.md)\"\nEOF",
    "echo $((1<<2)) && gh pr view",
    # a $ inside single quotes is not an expansion
    "gh pr create --title 'feat: x' --body 'Saves $5 per run'",
    "gh pr comment 5 --body \\$5",
    # read-only GraphQL publishes nothing
    "gh api graphql -f query='query($owner: String!, $repo: String!) "
    "{ repository(owner: $owner, name: $repo) { id } }' -f owner=o -f repo=r",
    "gh api graphql -f query='\n{\n  repository(owner: \"'$OWNER'\", "
    "name: \"'$REPO'\") { id }\n}'",
    "gh api graphql -f query='{ viewer { login } }'",
    # a GET with fields sends them as a query string
    "gh api -X GET repos/o/r/pulls -f state=open",
    "gh api repos/o/r/contents/README.md",
    # a commit message's heredoc, inside \"$(...)\", is data
    "git commit -m \"$(cat <<'EOF'\nfix(guard): read the \"body's\" "
    "fields of gh api calls\n\nBody text.\nEOF\n)\"",
    # a cd in a background job or a pipeline leaves this shell where it is
    "cd sub & gh pr create --title t --body-file clean.md",
    "cd sub | gh pr create --title t --body-file clean.md",
    # read-only commands earlier in the line
    "git status && gh pr create --title t --body-file clean.md",
    "python3 scripts/check-pr-body.py --file clean.md && "
    "gh pr create --title t --body-file clean.md",
    "grep -n 'gh pr create' CLAUDE.md",
    "echo gh pr comment 1 --body-file missing.md",
])
def test_allowed(cwd, command):
    assert guard.guard(command, cwd) == 0


@pytest.mark.parametrize("command", [
    "gh pr create --title t --body-file leak.md",
    "gh api -X PATCH repos/o/r/pulls/1 -F body=@leak.md",
    "gh api repos/o/r/issues/3/comments --field body=@leak.md",
    "gh api graphql -f query=@leak.md -F body=@leak.md",
    f"gh pr comment 5 --body '{pattern_secret()}'",
    f"gh pr create --title '{pattern_secret()}' --body x",
    f"gh pr create -t t -b'{pattern_secret()}'",
    f"gh issue create --title=t --body='{pattern_secret()}'",
    f"gh pr review 5 --comment -b '{pattern_secret()}'",
    f"gh pr merge 5 --squash --subject '{pattern_secret()}'",
    "gh issue comment 7 --body-file private.md",
    f"timeout 60 gh pr comment 1 --body '{pattern_secret()}'",
    f"bash -c \"gh pr comment 1 --body '{pattern_secret()}'\"",
    "cd sub && gh pr create --title t --body-file clean.md",
    "gh pr -R o/r create --title t --body-file leak.md",
    # gh's own aliases and the close/reopen comment
    f"gh pr new --title t --body '{pattern_secret()}'",
    f"gh issue new --title t --body '{pattern_secret()}'",
    f"gh pr close 5 --comment '{pattern_secret()}'",
    f"gh pr close 5 -c '{pattern_secret()}'",
    f"gh issue close 5 --comment '{pattern_secret()}'",
    f"gh issue reopen 5 --comment '{pattern_secret()}'",
    f"gh pr reopen 5 --comment '{pattern_secret()}'",
    # every gh api call that writes, whatever the endpoint
    f"gh api -X PUT repos/o/r/contents/n.md -f message='{pattern_secret()}' "
    "-f content=aGk=",
    "gh api -X PUT repos/o/r/contents/n.md -f message=m -f content="
    + base64.b64encode(pattern_secret().encode()).decode(),
    f"gh api -X POST repos/o/r/git/commits -f message='{pattern_secret()}' "
    "-f tree=abc",
    f"gh api -X PATCH repos/o/r/releases/1 -f body='{pattern_secret()}'",
    f"gh api gists -f description='{pattern_secret()}'",
    "gh api repos/o/r/issues/1/comments --input secret.json",
    "gh api graphql -f query='mutation { addComment(input: {subjectId: "
    f"\"x\", body: \"{pattern_secret()}\"}}) {{ clientMutationId }} }}'",
    "gh api graphql -f query='mutation($b: String!) { x(body: $b) }' "
    f"-f b='{pattern_secret()}'",
    "gh gist create --public --desc d leak.md",
    f"gh gist create --desc '{pattern_secret()}' clean.md",
    f"gh repo edit -d '{pattern_secret()}'",
    f"gh label create x -d '{pattern_secret()}'",
    "gh api graphql -f query='query { a } mutation { b(body: \"x\") }' "
    f"-f operationName=b -f note='{pattern_secret()}'",
    # a $ in single quotes is literal, and the text is still scanned
    f"gh pr comment 5 --body '$5 {pattern_secret()}'",
    # code a shell or an interpreter runs
    "bash <<'EOF'\ngh pr create --title t --body-file leak.md\nEOF",
    f"bash -s <<'EOF'\ngh issue comment 1 --body '{pattern_secret()}'\nEOF",
    f"bash <<< \"gh pr comment 1 --body '{pattern_secret()}'\"",
    # programs that run a command named in their arguments
    f"flock /tmp/l gh pr create --title t --body '{pattern_secret()}'",
    f"setsid gh pr create --title t --body '{pattern_secret()}'",
    f"if true; then gh pr comment 1 --body '{pattern_secret()}'; fi",
    f"{{ gh pr comment 1 --body '{pattern_secret()}'; }}",
    # the cd that does run first
    "cd sub; gh pr create --title t --body-file clean.md",
    "cd sub && echo ok | cat && gh pr create --title t --body-file clean.md",
])
def test_denied_for_content(cwd, command):
    assert guard.guard(command, cwd) == 2


@pytest.mark.parametrize("command", [
    "gh pr create --title t --body \"$(cat clean.md)\"",
    "gh pr create --title t --body $(cat clean.md)",
    "gh pr create --title t --body \"`cat clean.md`\"",
    "gh pr create --title t --body \"$BODY\"",
    "gh pr create --title t --body-file - <<'EOF'\nfine\nEOF",
    "gh pr create --title t --body-file -",
    "gh pr create --title t --body-file missing.md",
    "gh pr create --fill",
    "gh pr create --title t --web",
    "gh pr comment 5 --editor",
    "gh api repos/o/r/pulls/1 -F body=@-",
    "gh api repos/o/r/pulls/1 --input -",
    "cat > clean.md <<'EOF'\nnew\nEOF\n"
    "gh pr create --title t --body-file clean.md",
    "python3 make_body.py && gh pr create --title t --body-file clean.md",
    "git checkout -- clean.md && gh pr create --title t --body-file clean.md",
    "echo x | xargs gh pr comment 1 --body",
    "gh pr create --title \"it's open",
    "cat <<EOF\nnever closed\ngh pr comment 1 --body-file clean.md",
    "cat <<\"E\"OF\nx\nEOF\ngh pr comment 1 --body-file leak.md",
    # stdin and process substitution by another name
    "gh pr create --title t --body-file /dev/stdin < leak.md",
    "cat leak.md | gh pr create --title t --body-file /dev/stdin",
    "gh api repos/o/r/issues/1/comments -F body=@/dev/stdin < leak.md",
    "gh api repos/o/r/pulls/1 -X PATCH --input /dev/fd/0 < secret.json",
    "gh pr create --title t --body-file <(cat leak.md)",
    "gh pr create --body-file <(cat leak.md) --title t",
    "gh pr create --title t --body-file stdin.md < leak.md",
    "gh pr create --title t --body-file",
    "gh gist create --desc d",
    "gh gist create - < leak.md",
    # a shell reading code this hook cannot see
    "echo \"gh pr comment 1 --body x\" | bash",
    "bash <<EOF\ngh pr comment 1 --body \"$X\"\nEOF",
    "G=gh; $G pr create --title t --body x",
    "find . -maxdepth 1 -name b.md -exec gh pr comment 1 --body-file {} \\;",
    "python3 -c \"import subprocess; subprocess.run(['gh','pr','comment',"
    "'1','--body-file','b.md'])\"",
    "bash -c 'gh pr comment 1 --body \"$X\"'",
    "gh api graphql -f query=\"$Q\"",
    "gh api graphql -f query=\"{ a } $M\" -f operationName=b",
    # an earlier command that can rewrite the body file
    "git checkout feat && gh pr create --title t --body-file clean.md",
    "git stash pop; gh pr create --title t --body-file clean.md",
    "git pull && gh pr create --title t --body-file clean.md",
    "gh pr checkout 5 && gh pr create --title t --body-file clean.md",
    "npm run build && gh pr create --title t --body-file clean.md",
])
def test_unreadable_fails_closed(cwd, command):
    assert guard.guard(command, cwd) == 2


def test_a_background_or_piped_cd_does_not_move_the_read(cwd):
    # swap.md leaks where the line starts; sub/swap.md is clean. bash runs a
    # `cd` before `&` or `|` in a subshell, so gh reads the leaky one.
    for sep in ("&", "|"):
        command = f"cd sub {sep} gh pr create --title t --body-file swap.md"
        assert guard.guard(command, cwd) == 2, sep
    assert guard.guard(
        "cd sub && gh pr create --title t --body-file swap.md", cwd) == 0


def test_cd_resolves_the_body_file(cwd):
    # clean.md in the starting directory is clean; sub/clean.md is not. The
    # file gh reads is the one after the cd.
    assert guard.guard("gh pr create --title t --body-file clean.md", cwd) == 0
    assert guard.guard("cd sub && gh pr create --title t --body-file clean.md",
                       cwd) == 2
    assert guard.guard(
        "(cd sub) && gh pr create --title t --body-file clean.md", cwd) == 0


def test_override_skips_patterns_only(cwd):
    allow = "PAPERCLAW_ALLOW_SENSITIVE=1 "
    assert guard.guard(allow + "gh pr create --title t --body-file leak.md",
                       cwd) == 0
    assert guard.guard(allow + "gh pr create --title t --body-file private.md",
                       cwd) == 2


def _hook(cwd, command, **env):
    event = {"tool_name": "Bash", "tool_input": {"command": command},
             "cwd": cwd}
    return subprocess.run(HOOK, input=json.dumps(event), capture_output=True,
                          text=True, env=dict(os.environ, **env))


def test_an_inherited_override_does_not_skip_the_scan(cwd):
    # Only the prefix on the gh command itself is a deliberate override; a
    # session started from a shell that exported it would skip every check.
    proc = _hook(cwd, "gh pr create --title t --body-file leak.md",
                 PAPERCLAW_ALLOW_SENSITIVE="1")
    assert proc.returncode == 2


def test_a_command_without_gh_imports_nothing_heavy():
    # The hook runs before every Bash call, so "not gh" must cost a bare
    # interpreter start, not json, re and dataclasses. (The cwd is neutral:
    # a pytest directory is named after the test, which says "gh".)
    event = json.dumps({"tool_input": {"command": "ls -la"}, "cwd": "/"})
    proc = subprocess.run(["python3", "-X", "importtime"] + HOOK[1:],
                          input=event, capture_output=True, text=True)
    assert proc.returncode == 0
    imported = {line.rsplit("|", 1)[-1].strip()
                for line in proc.stderr.splitlines() if "|" in line}
    assert not imported & {"json", "dataclasses", "shlex"}, imported


def test_hook_denial_names_labels_never_values(cwd):
    proc = _hook(cwd, "gh issue comment 7 --body-file private.md")
    assert proc.returncode == 2
    assert "private-values:1" in proc.stderr
    proc = _hook(cwd, f"gh pr comment 5 --body '{pattern_secret()}'")
    assert proc.returncode == 2
    assert "secret/token material" in proc.stderr
    for out in (proc.stdout, proc.stderr):
        assert pattern_secret() not in out and known_value() not in out


def test_hook_protocol_edges(cwd):
    assert _hook(cwd, "ls").returncode == 0
    proc = subprocess.run(HOOK, input="not json", capture_output=True,
                          text=True)
    assert proc.returncode == 0
    proc = subprocess.run(HOOK, input=json.dumps({"tool_input": {}}),
                          capture_output=True, text=True)
    assert proc.returncode == 0


def _settings_hook():
    path = os.path.join(REPO, ".claude", "settings.json")
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    (entry,) = [e for e in settings["hooks"]["PreToolUse"]
                if e["matcher"] == "Bash"]
    (hook,) = entry["hooks"]
    return hook


def test_settings_run_the_guard_on_every_bash_call(cwd, tmp_path):
    hook = _settings_hook()
    assert hook["timeout"] >= 120  # the paper-title oracle takes ~20 s
    event = json.dumps({"tool_input": {
        "command": "gh pr comment 1 --body-file leak.md"}, "cwd": cwd})

    def run(project_dir):
        return subprocess.run(["sh", "-c", hook["command"]], input=event,
                              capture_output=True, text=True,
                              env=dict(os.environ,
                                       CLAUDE_PROJECT_DIR=project_dir))

    assert run(REPO).returncode == 2
    # An older checkout has no guard script. The hook must allow then, not
    # deny every Bash call, `git checkout` back included.
    assert run(str(tmp_path)).returncode == 0


def test_settings_hook_starts_no_python_for_a_command_without_gh(tmp_path):
    # Python compiles the whole guard (no .pyc for a script) before its own
    # fast path runs, ~30 ms on every Bash call; the shell check costs ~3.
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "python.log"
    (stubs / "python3").write_text(f'#!/bin/sh\necho ran >> "{log}"\n')
    (stubs / "python3").chmod(0o755)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=REPO,
               PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")
    for command, ran in (("ls -la", False), ("gh pr view 5", True)):
        event = json.dumps({"tool_input": {"command": command}, "cwd": "/"})
        proc = subprocess.run(["sh", "-c", _settings_hook()["command"]],
                              input=event, capture_output=True, text=True,
                              env=env)
        assert proc.returncode == 0
        assert log.exists() == ran, command
