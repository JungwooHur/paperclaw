"""check-pr-body.py: the scan behind both the hand-run audit and the gh guard.

It used to pipe the body to `check-sensitive.sh --msg /dev/stdin`, which exits
0 for anything that is not a regular file, so its pattern check never ran.
These tests pin that each of its three checks fires, and that a report carries
labels, never the text it found.
"""
import os
import subprocess

from leak_helpers import (SCRIPTS, apply_env, clean_env, known_value,
                          load_script, pattern_secret)

CLI = ["python3", os.path.join(SCRIPTS, "check-pr-body.py")]
TITLE = "Adaptive Widget Calibration Under Drift"


def _scanner(tmp_path, monkeypatch, **extra):
    apply_env(monkeypatch, clean_env(tmp_path, **extra))
    return load_script("check-pr-body.py")


def test_pattern_hit_is_reported_by_label_and_line(tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch)
    problems = cpb.scan(f"intro\n## {pattern_secret()}\n", [], [], "body")
    assert [k for k, _ in problems] == ["patterns"]
    assert "body:2" in problems[0][1]
    assert pattern_secret() not in problems[0][1]


def test_known_value_and_paper_title(tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch)
    (tmp_path / "private-values").write_text(known_value() + "\n")
    needles = cpb.private_values.load_needles()
    text = f"one\nfixes the {TITLE.lower()} section\n{known_value()}\n"
    problems = dict(cpb.scan(text, [TITLE], needles))
    assert problems["private value"] == "private-values:1"
    assert "line 2" in problems["paper content"]
    for detail in problems.values():
        assert TITLE.lower() not in detail.lower()
        assert known_value() not in detail


def test_override_skips_patterns_only(tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch)
    (tmp_path / "private-values").write_text(known_value() + "\n")
    needles = cpb.private_values.load_needles()
    text = f"{pattern_secret()} {known_value()}"
    kinds = [k for k, _ in cpb.scan(text, [], needles, patterns=False)]
    assert kinds == ["private value"]


def test_notion_credentials_come_from_env_file_but_not_to_subprocess(
        tmp_path, monkeypatch):
    token = "fake-notion-token-" + "y" * 12
    (tmp_path / "test.env").write_text(
        f"NOTION_TOKEN={token}\nNOTION_RESEARCH_DB=fake-db-id-123\n")
    cpb = _scanner(tmp_path, monkeypatch)
    cpb.load_notion_env()
    assert os.environ["NOTION_TOKEN"] == token
    env = cpb._pattern_env()
    assert "NOTION_TOKEN" not in env and "NOTION_RESEARCH_DB" not in env
    assert env["PAPERCLAW_REDACT"] == "1"


def test_cli_title_and_file(tmp_path):
    env = clean_env(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("## Summary\nTidy the scanner.\n")

    def run(*args):
        return subprocess.run(CLI + list(args), env=env, capture_output=True,
                              text=True)

    assert run("--file", str(body), "--title", "chore: tidy").returncode == 0
    proc = run("--file", str(body), "--title", f"chore: {pattern_secret()}")
    assert proc.returncode == 1
    assert "title: patterns" in proc.stderr
    assert pattern_secret() not in proc.stdout + proc.stderr
