"""check-pr-body.py: the scan behind both the hand-run audit and the gh guard.

It used to pipe the body to `check-sensitive.sh --msg /dev/stdin`, which exits
0 for anything that is not a regular file, so its pattern check never ran.
These tests pin that each of its three checks fires, and that a report carries
labels, never the text it found.
"""
import json
import os
import stat
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


def test_generic_headings_and_partial_words_are_no_title_hit(
        tmp_path, monkeypatch):
    # A code-only body was blocked for "model architecture", a heading every
    # paper has; and a substring match on letters ran across word boundaries.
    cpb = _scanner(tmp_path, monkeypatch)
    titles = ["Model architecture", "Problem Formulation",
              "Results and Discussion", "Calibration Under Drift Data", TITLE]
    text = ("The container image changes; the model architecture setting "
            "(AGENT_MODEL) is untouched.\nWe discuss results and discussion "
            "of recalibration under drift datasets.\n")
    assert cpb.scan(text, titles, []) == []
    hit = cpb.scan(f"fixes {TITLE.upper()}!\n", titles, [])
    assert [k for k, _ in hit] == ["paper content"]


def test_an_inherited_override_is_not_passed_to_the_pattern_scan(
        tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch, PAPERCLAW_ALLOW_SENSITIVE="1")
    assert "PAPERCLAW_ALLOW_SENSITIVE" not in cpb._pattern_env()
    kinds = [k for k, _ in cpb.scan(pattern_secret(), [], [])]
    assert kinds == ["patterns"]


def test_cli_still_honours_the_override_it_is_run_with(tmp_path):
    env = clean_env(tmp_path, PAPERCLAW_ALLOW_SENSITIVE="1")
    proc = subprocess.run(CLI + ["--title", pattern_secret()], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0


def test_titles_are_cached_privately_and_refreshed(tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch, NOTION_TOKEN="fake-token-123456",
                   NOTION_RESEARCH_DB="fake-db-1")
    calls, spawned = [], []

    def fetch():
        calls.append(1)
        return [TITLE]

    def refresh():
        spawned.append(1)

    assert cpb.section_titles(fetch, refresh) == [TITLE]
    assert cpb.section_titles(fetch, refresh) == [TITLE]
    assert len(calls) == 1 and not spawned
    path = cpb.title_cache_path()
    assert path.startswith(str(tmp_path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # stale: served from the cache while a refresh runs in the background
    with open(path) as f:
        data = json.load(f)
    data["at"] -= cpb.TITLE_CACHE_FRESH + 1
    with open(path, "w") as f:
        json.dump(data, f)
    assert cpb.section_titles(fetch, refresh) == [TITLE]
    assert len(calls) == 1 and len(spawned) == 1
    # too old, or for another database: fetched again before answering
    data["at"] -= cpb.TITLE_CACHE_MAX_AGE
    with open(path, "w") as f:
        json.dump(data, f)
    cpb.section_titles(fetch, refresh)
    assert len(calls) == 2
    monkeypatch.setenv("NOTION_RESEARCH_DB", "fake-db-2")
    cpb.section_titles(fetch, refresh)
    assert len(calls) == 3


def test_short_text_needs_no_oracle(tmp_path, monkeypatch):
    cpb = _scanner(tmp_path, monkeypatch)
    assert not cpb.could_hold_a_title("feat: add a guard")
    assert cpb.could_hold_a_title(TITLE)
