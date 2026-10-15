"""The known-value check: the owner's real values never reach a commit.

It matches values, not shapes, so these tests pin what counts as a value and
which forms of it are matched, and that a hit names the source but never the
value itself.
"""
import os
import re
import sqlite3

import pytest

from leak_helpers import REPO, SCRIPTS, Repo, clean_env, known_value, load_script

pv = load_script("private_values.py")
CLI = ["python3", os.path.join(SCRIPTS, "private_values.py")]


def _uuid():
    return "-".join(("1a2b3c4d", "5e6f", "7a8b", "9c0d", "1e2f3a4b5c6d"))


def _jid_number():
    return "8210" + "4455" + "6677"


def test_every_container_secret_is_private():
    # The .env keys handed to the container are the ones that matter most; a
    # key added there must be checked here without anyone remembering to.
    src = open(os.path.join(REPO, "src", "container-runner.ts")).read()
    block = re.search(r"CONTAINER_SECRET_KEYS = \[(.*?)\]", src, re.S).group(1)
    ts_keys = re.findall(r"'([A-Z0-9_]+)'", block)
    assert ts_keys and tuple(ts_keys) == pv.CONTAINER_SECRET_KEYS
    for key in ts_keys:
        assert pv.is_private_key(key) == (key != "OUTPUT_LANGUAGE"), key


@pytest.mark.parametrize("key, private", [
    ("CHAT_JID", True), ("SOME_API_KEY", True), ("X_COOKIE", True),
    ("NOTION_OTHER_DB", True), ("SPACE_ID", True), ("DB_PASSWORD", True),
    ("ASSISTANT_NAME", False), ("LOG_LEVEL", False), ("TZ", False),
])
def test_private_key_names(key, private):
    assert pv.is_private_key(key) is private


def test_uuid_matches_dashed_and_undashed_any_case():
    needles = pv.variants("x", _uuid())
    for form in (_uuid(), _uuid().replace("-", ""), _uuid().upper()):
        assert pv.find(f"id: {form}.", needles) == ["x"]


def test_jid_also_matches_its_number():
    needles = pv.variants("x", _jid_number() + "@s.whatsapp.net")
    assert pv.find(f"call +{_jid_number()}", needles) == ["x"]


def test_email_is_case_insensitive():
    needles = pv.variants("x", "Owner.Name" + "@" + "mail.test")
    assert pv.find("owner.name" + "@" + "MAIL.TEST", needles) == ["x"]


def test_short_values_are_ignored():
    assert pv.variants("x", "abc1234") == []


def test_merge_contributes_only_lines_added_against_every_parent():
    patch = "\n".join([
        "diff --cc f.txt",
        "@@@ -1,1 -1,1 +1,3 @@@",
        "+ from-first-parent-side",
        " +from-second-parent-side",
        "++resolution",
    ])
    assert pv.own_added_lines(patch).splitlines() == [
        "diff --cc f.txt", "resolution"]


def _sources(tmp_path):
    (tmp_path / "test.env").write_text("\n".join([
        "# comment",
        f"NOTION_TOKEN='{known_value()}'",
        "OUTPUT_LANGUAGE=Korean-language-output",
        "ASSISTANT_NAME=NotASecretAtAll",
        "SHORT_TOKEN=abc",
    ]))
    (tmp_path / "private-values").write_text(f"# mine\n\n{_uuid()}\n")
    db = sqlite3.connect(tmp_path / "messages.db")
    db.execute("CREATE TABLE registered_groups (jid TEXT PRIMARY KEY)")
    db.execute("INSERT INTO registered_groups VALUES (?)",
               (_jid_number() + "@s.whatsapp.net",))
    db.commit()
    db.close()


def test_load_needles_reads_all_three_sources(tmp_path, monkeypatch):
    _sources(tmp_path)
    for k, v in clean_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    labels = {n.label for n in pv.load_needles()}
    assert labels == {".env:NOTION_TOKEN", "private-values:3",
                      "messages.db:registered_groups.jid#1"}
    texts = " ".join(n.text for n in pv.load_needles())
    assert "Korean-language-output" not in texts
    assert "NotASecretAtAll" not in texts


def test_file_hit_names_the_source_never_the_value(tmp_path):
    _sources(tmp_path)
    leak = tmp_path / "body.md"
    leak.write_text(f"see {known_value()} and {_uuid().replace('-', '')}\n")
    proc = Repo(tmp_path / "r", clean_env(tmp_path)).run(CLI + ["--file", str(leak)])
    assert proc.returncode == 1
    assert ".env:NOTION_TOKEN" in proc.stdout
    assert "private-values:3" in proc.stdout
    out = proc.stdout + proc.stderr
    assert known_value() not in out and _uuid().replace("-", "") not in out


def test_clean_file_and_no_values(tmp_path):
    clean = tmp_path / "body.md"
    clean.write_text("nothing private here\n")
    repo = Repo(tmp_path / "r", clean_env(tmp_path))
    proc = repo.run(CLI + ["--file", str(clean)])
    assert proc.returncode == 0 and "no private values" in proc.stderr
    _sources(tmp_path)
    assert repo.run(CLI + ["--file", str(clean)]).returncode == 0


def test_range_scans_patch_message_and_identity(tmp_path):
    _sources(tmp_path)
    repo = Repo(tmp_path / "r", clean_env(tmp_path))
    base = repo.commit({"a.txt": "base\n"})
    repo.commit({"a.txt": f"base\n{known_value()}\n"}, "add a line")
    repo.commit(message=f"mention {_uuid()}")
    owner = "Owner.Name" + "@" + "mail.test"
    with open(tmp_path / "private-values", "a") as f:
        f.write(owner + "\n")
    repo.commit(message="by the owner", GIT_AUTHOR_EMAIL=owner)
    proc = repo.run(CLI + ["--range", f"{base}..HEAD"])
    assert proc.returncode == 1
    parts = re.findall(r"commit [0-9a-f]+ \((\w+)\)", proc.stdout)
    assert sorted(parts) == ["author", "message", "patch"]
    out = proc.stdout + proc.stderr
    assert known_value() not in out and owner not in out


def test_range_ignores_a_value_being_removed(tmp_path):
    _sources(tmp_path)
    repo = Repo(tmp_path / "r", clean_env(tmp_path))
    leaked = repo.commit({"a.txt": f"{known_value()}\n"})
    repo.commit({"a.txt": "redacted\n"}, "remove it")
    assert repo.run(CLI + ["--range", f"{leaked}..HEAD"]).returncode == 0


def test_range_merge_counts_its_own_resolution_only(tmp_path):
    _sources(tmp_path)
    repo = Repo(tmp_path / "r", clean_env(tmp_path))
    repo.commit({"a.txt": "one\n", "b.txt": "x\n"})
    repo.git("checkout", "-q", "-b", "side")
    repo.commit({"a.txt": f"one\n{known_value()}\n"}, "side")
    repo.git("checkout", "-q", "main")
    repo.commit({"b.txt": "y\n"}, "main")
    repo.git("merge", "-q", "--no-edit", "side")
    only_merge = CLI + ["--range", "-n", "1", "HEAD"]
    assert repo.run(only_merge).returncode == 0
    repo.git("reset", "-q", "--hard", "HEAD^")
    repo.git("merge", "-q", "--no-commit", "side")
    repo.write("a.txt", f"one\n{known_value()}\n{_uuid()}\n")
    repo.git("add", "a.txt")
    repo.commit(message="evil merge")
    proc = repo.run(only_merge)
    assert proc.returncode == 1
    assert proc.stdout.strip().splitlines() == [
        f"private-values:3: commit {repo.git('rev-parse', 'HEAD')[:12]} (patch)"]


def test_bad_range_blocks(tmp_path):
    _sources(tmp_path)
    repo = Repo(tmp_path / "r", clean_env(tmp_path))
    repo.commit()
    assert repo.run(CLI + ["--range", "no-such-rev"]).returncode == 2
