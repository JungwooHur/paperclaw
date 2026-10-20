"""check-sensitive.sh --range and --text: the pattern scan of what a push or a
PR publishes, and the redacted report CI prints into a public log."""
import os
import re

import pytest

from leak_helpers import (SCRIPTS, Repo, clean_env, fake_arxiv, fake_email,
                          fake_group_jid, fake_mobile, fake_uuid,
                          pattern_secret)

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


@pytest.mark.parametrize("path", [
    "store/state.json",
    # The host-side list of papers a batch has not reached yet.
    "groups/main/research-papers/papers_backlog.json",
    # The host-side record of what the last batch resume was handed.
    "groups/main/research-papers/papers_sweep_state.json",
    # What an atomic write leaves behind when its rename fails.
    "groups/main/research-papers/papers_queue.json.tmp",
    "groups/main/research-papers/papers_backlog.json.tmp",
    "groups/main/research-papers/papers_sweep_state.json.tmp",
])
def test_forbidden_path(tmp_path, path):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit({path: "{}\n"}, "oops")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"])
    assert proc.returncode == 1
    assert "[path] forbidden file in commit" in proc.stderr
    assert path in proc.stderr


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


def test_non_ascii_paths_are_scanned_and_checked(tmp_path):
    # git quotes such a path ("docs/\\353...") unless names are read NUL-
    # separated; the quoted form matched no pathspec and no forbidden-path
    # rule, so both the token and the .db went through.
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit({"docs/노트.md": f"{pattern_secret()}\n",
                 "store/메시지.db": "x\n"}, "ko")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"], PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    assert "노트.md:1" in proc.stderr
    assert "[path] forbidden file in commit" in proc.stderr


def test_staged_non_ascii_path_is_scanned(tmp_path):
    repo = _repo(tmp_path)
    repo.commit()
    repo.write("docs/메모.md", f"{pattern_secret()}\n")
    repo.git("add", "docs/메모.md")
    proc = repo.run(CHECK + ["--staged"])
    assert proc.returncode == 1 and "secret/token material" in proc.stderr


def test_a_type_change_is_scanned(tmp_path):
    repo = _repo(tmp_path)
    os.symlink("elsewhere", os.path.join(repo.path, "cfg"))
    repo.git("add", "cfg")
    base = repo.commit(message="link")
    os.remove(os.path.join(repo.path, "cfg"))
    repo.commit({"cfg": f"{pattern_secret()}\n"}, "now a file")
    assert repo.run(CHECK + ["--range", f"{base}..HEAD"]).returncode == 1


@pytest.mark.parametrize("who", ["author", "committer"])
def test_commit_identity_must_be_noreply(tmp_path, who):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit(message="x", **{f"GIT_{who.upper()}_EMAIL": fake_email()})
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"], PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    assert f"[{who}]" in proc.stderr
    assert fake_email() not in proc.stdout + proc.stderr
    # GitHub's own committer, on a merge made in the web UI, is no person's
    repo.commit(message="y", GIT_COMMITTER_EMAIL="noreply@github.com")
    assert repo.run(CHECK + ["--range", "HEAD^..HEAD"]).returncode == 0


def test_staged_checks_the_identity_the_commit_will_get(tmp_path):
    repo = _repo(tmp_path)
    repo.commit()
    repo.write("a.txt", "fine\n")
    repo.git("add", "a.txt")
    assert repo.run(CHECK + ["--staged"]).returncode == 0
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        proc = repo.run(CHECK + ["--staged"], **{var: fake_email()})
        assert proc.returncode == 1, var


@pytest.mark.parametrize("line", [
    lambda: f"mail {fake_email()} or user@example.com",
    lambda: f"Co-authored-by: {fake_email()}, bot@users.noreply.github.com",
    lambda: f"jid {fake_group_jid()} at 1700000000",
    lambda: f"ids {fake_arxiv()} and 2401.12345",
    lambda: f"{fake_uuid()} next to 00000000-0000-0000-0000-000000000000",
])
def test_a_placeholder_excuses_only_itself(tmp_path, line):
    repo = _repo(tmp_path)
    proc = repo.run(CHECK + ["--text", "t"], line() + "\n")
    assert proc.returncode == 1


@pytest.mark.parametrize("line", [
    "mail user@example.com", "bot@users.noreply.github.com",
    "ids 2401.12345 and 2401.12345v2",
    "00000000-0000-0000-0000-000000000000",
])
def test_placeholders_alone_pass(tmp_path, line):
    repo = _repo(tmp_path)
    assert repo.run(CHECK + ["--text", "t"], line + "\n").returncode == 0


def test_international_phone_forms(tmp_path):
    repo = _repo(tmp_path)
    local = fake_mobile()[2:]  # 1099998888
    for sep in (" ", "-", "."):
        for lead in ("+82 ", "+82-", "+82"):
            text = f"call {lead}{local[:2]}{sep}{local[2:6]}{sep}{local[6:]}"
            proc = repo.run(CHECK + ["--text", "t"], text + "\n")
            assert proc.returncode == 1, text


@pytest.mark.parametrize("files", [
    {"a.cfg": b"cfg\0\ntoken=" + pattern_secret().encode() + b"\n"},
    {".gitattributes": "*.json -diff\n",
     "data.json": f'{{"token": "{pattern_secret()}"}}\n'},
    {"n.txt": f"token={pattern_secret()}\n".encode("utf-16")},
])
def test_binary_looking_content_is_scanned(tmp_path, files):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit(files, "binary")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"])
    assert proc.returncode == 1 and "secret/token material" in proc.stderr


def test_self_excluded_paths_still_get_the_secret_scan(tmp_path):
    repo = _repo(tmp_path)
    repo.commit()
    for path in (".husky/pre-push", "vendor/lib/conf.py", "poetry.lock",
                 ".env.example"):
        head = repo.commit({path: f"TOKEN={pattern_secret()}\n"})
        proc = repo.run(CHECK + ["--range", f"{head}^..{head}"])
        assert proc.returncode == 1, path
    # ...and only that: the other patterns are why they are excluded
    head = repo.commit({"vendor/lib/x.py": f"# {fake_email()}\n"})
    assert repo.run(CHECK + ["--range", f"{head}^..{head}"]).returncode == 0


def test_path_names_are_scanned_and_kept_out_of_a_redacted_log(tmp_path):
    repo = _repo(tmp_path)
    base = repo.commit()
    repo.commit({f"docs/{fake_arxiv()}-notes.md": "benign\n",
                 f"papers/{fake_email()}.pdf": "x\n"}, "names")
    proc = repo.run(CHECK + ["--range", f"{base}..HEAD"], PAPERCLAW_REDACT="1")
    assert proc.returncode == 1
    assert "(paths)" in proc.stderr and "[path] forbidden" in proc.stderr
    for value in (fake_arxiv(), fake_email()):
        assert value not in proc.stdout + proc.stderr


def test_tag_mode_scans_message_and_tagger(tmp_path):
    repo = _repo(tmp_path)
    head = repo.commit()
    repo.git("tag", "-a", "v1", "-m", "release", head)
    assert repo.run(CHECK + ["--tag", repo.git("rev-parse", "v1")]
                    ).returncode == 0
    repo.git("tag", "-a", "v2", "-m", f"notes {pattern_secret()}", head)
    proc = repo.run(CHECK + ["--tag", repo.git("rev-parse", "v2")],
                    PAPERCLAW_REDACT="1")
    assert proc.returncode == 1 and pattern_secret() not in proc.stderr
    repo.run(["git", "tag", "-a", "v3", "-m", "ok", head],
             GIT_COMMITTER_EMAIL=fake_email())
    proc = repo.run(CHECK + ["--tag", repo.git("rev-parse", "v3")])
    assert proc.returncode == 1 and "[tagger]" in proc.stderr
