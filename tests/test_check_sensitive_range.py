"""check-sensitive.sh --range and --text: the pattern scan of what a push or a
PR publishes, and the redacted report CI prints into a public log."""
import os
import re

from leak_helpers import SCRIPTS, Repo, clean_env, pattern_secret

CHECK = ["bash", os.path.join(SCRIPTS, "check-sensitive.sh")]


def _repo(tmp_path):
    return Repo(tmp_path / "r", clean_env(tmp_path))


def test_new_branch_scans_only_what_the_remote_lacks(tmp_path):
    repo = _repo(tmp_path)
    published = repo.commit({"old.txt": f"{pattern_secret()}\n"}, "old")
    repo.git("update-ref", "refs/remotes/origin/main", published)
    repo.git("checkout", "-q", "-b", "feature")
    head = repo.commit({"new.txt": "fine\n"}, "new")
    new_branch = CHECK + ["--range", head, "--not", "--remotes=origin"]
    assert repo.run(new_branch).returncode == 0
    head = repo.commit({"new.txt": f"fine\n{pattern_secret()}\n"}, "leak")
    proc = repo.run(CHECK + ["--range", head, "--not", "--remotes=origin"])
    assert proc.returncode == 1
    assert "new.txt" in proc.stderr and "old.txt" not in proc.stderr


def test_commit_message_is_scanned_including_hash_lines(tmp_path):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit(message=f"subject\n\n# {pattern_secret()}")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"])
    assert proc.returncode == 1 and "(message)" in proc.stderr


def test_forbidden_path(tmp_path):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit({"store/state.json": "{}\n"}, "oops")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"])
    assert proc.returncode == 1
    assert "[path] forbidden file in commit" in proc.stderr
    assert "store/state.json" in proc.stderr


def test_merge_contributes_only_its_own_resolution(tmp_path):
    repo = _repo(tmp_path)
    repo.commit({"a.txt": "one\n", "b.txt": "x\n"})
    repo.git("checkout", "-q", "-b", "side")
    repo.commit({"a.txt": f"one\n{pattern_secret()}\n"}, "side")
    repo.git("checkout", "-q", "main")
    repo.commit({"b.txt": "y\n"}, "main")
    repo.git("merge", "-q", "--no-edit", "side")
    only_merge = CHECK + ["--range", "-n", "1", "HEAD"]
    assert repo.run(only_merge).returncode == 0
    # An evil merge: a line in the resolution that neither parent has. a.txt
    # is touched too, so the side's line shows in the combined diff, as a
    # line one parent already has.
    repo.git("reset", "-q", "--hard", "HEAD^")
    repo.git("merge", "-q", "--no-commit", "side")
    repo.write("a.txt", f"one\n{pattern_secret()}\nresolved\n")
    repo.write("b.txt", f"y\nclean\n{pattern_secret()}\n")
    repo.git("add", "a.txt", "b.txt")
    repo.commit(message="merge side")
    proc = repo.run(only_merge, PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    short = repo.git("rev-parse", "--short", "HEAD")
    assert re.findall(r"✗ \[(.+?)\] (\S+)", proc.stderr) == [
        ("secret/token material", f"{short}:b.txt:3")]


def test_redacted_report_gives_file_and_line_never_the_text(tmp_path):
    repo = _repo(tmp_path)
    base = repo.commit({"a.txt": "1\n2\n3\n4\n"})
    repo.commit({"a.txt": f"1\n2\n3\n4\n5\n{pattern_secret()}\n"}, "leak")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"], PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    short = repo.git("rev-parse", "--short", "HEAD")
    assert f"✗ [secret/token material] {short}:a.txt:6" in proc.stderr
    assert pattern_secret() not in proc.stdout + proc.stderr
    # without redaction the line itself is shown, numbered as in the file
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"])
    assert f"6:{pattern_secret()}" in proc.stderr


def test_override_and_bad_range(tmp_path):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit({"a.txt": f"{pattern_secret()}\n"})
    rng = CHECK + ["--range", f"{base}..HEAD"]
    assert repo.run(rng, PAPERCLAW_ALLOW_SENSITIVE="1").returncode == 0
    assert repo.run(CHECK + ["--range", "no-such-rev"]).returncode != 0


def test_text_mode_reads_a_pipe_and_heading_lines(tmp_path):
    # --msg skips '#' lines (git comments) and needs a regular file, so a PR
    # body piped to it was never scanned at all. --text is the PR-body mode.
    repo = _repo(tmp_path)
    proc = repo.run(CHECK + ["--text", "PR body"], f"## {pattern_secret()}\n",
                    PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    assert "✗ [secret/token material] PR body:1" in proc.stderr
    assert pattern_secret() not in proc.stderr
    assert repo.run(CHECK + ["--text", "t"], "a clean title\n").returncode == 0


def test_msg_mode_is_unchanged(tmp_path):
    repo = _repo(tmp_path)
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"subject\n# {pattern_secret()}\n")
    assert repo.run(CHECK + ["--msg", str(msg)]).returncode == 0
    msg.write_text(f"subject {pattern_secret()}\n")
    proc = repo.run(CHECK + ["--msg", str(msg)])
    assert proc.returncode == 1 and pattern_secret() in proc.stderr
