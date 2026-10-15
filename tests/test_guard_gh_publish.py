"""The PreToolUse guard: which gh commands publish, what text they carry, and
the decision. Exit 0 allows the Bash call; exit 2 denies it."""
import json
import os
import subprocess

import pytest

from leak_helpers import (SCRIPTS, apply_env, clean_env, known_value,
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
    "cat > clean.md <<'EOF'\nnew\nEOF\ngh pr create --title t --body-file clean.md",
    "python3 make_body.py && gh pr create --title t --body-file clean.md",
    "git checkout -- clean.md && gh pr create --title t --body-file clean.md",
    "echo x | xargs gh pr comment 1 --body",
    "gh pr create --title \"it's open",
    "cat <<EOF\nnever closed\ngh pr comment 1 --body-file clean.md",
    "cat <<\"E\"OF\nx\nEOF\ngh pr comment 1 --body-file leak.md",
])
def test_unreadable_fails_closed(cwd, command):
    assert guard.guard(command, cwd) == 2


def test_cd_resolves_the_body_file(cwd):
    # clean.md in the starting directory is clean; sub/clean.md is not. The
    # file gh reads is the one after the cd.
    assert guard.guard("gh pr create --title t --body-file clean.md", cwd) == 0
    assert guard.guard("cd sub && gh pr create --title t --body-file clean.md",
                       cwd) == 2
    assert guard.guard("(cd sub) && gh pr create --title t --body-file clean.md",
                       cwd) == 0


def test_override_skips_patterns_only(cwd):
    allow = "PAPERCLAW_ALLOW_SENSITIVE=1 "
    assert guard.guard(allow + "gh pr create --title t --body-file leak.md",
                       cwd) == 0
    assert guard.guard(allow + "gh pr create --title t --body-file private.md",
                       cwd) == 2


def _hook(cwd, command):
    event = {"tool_name": "Bash", "tool_input": {"command": command},
             "cwd": cwd}
    return subprocess.run(HOOK, input=json.dumps(event), capture_output=True,
                          text=True, env=dict(os.environ))


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
