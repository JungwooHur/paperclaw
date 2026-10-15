"""The pre-push hook: it reads git's ref lines, scans exactly the commits the
push publishes, and does so before typecheck and tests, which it then skips."""
import os

import pytest

from leak_helpers import (REPO, SCRIPTS, Repo, clean_env, known_value,
                          pattern_secret)

HOOK = os.path.join(REPO, ".husky", "pre-push")
ZERO = "0" * 40


@pytest.fixture
def repo(tmp_path):
    """A repo whose scripts/ is this checkout's, with npm and npx stubbed to
    record that they ran."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in ("npm", "npx"):
        stub = stubs / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "$STUB_LOG"\n')
        stub.chmod(0o755)
    env = clean_env(tmp_path, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                    STUB_LOG=str(tmp_path / "stub.log"))
    r = Repo(tmp_path / "r", env)
    os.symlink(SCRIPTS, os.path.join(r.path, "scripts"))
    r.git("remote", "add", "origin", str(tmp_path / "remote.git"))
    r.log = tmp_path / "stub.log"
    return r


def _push(repo, *lines, **env):
    stdin = "".join(" ".join(line) + "\n" for line in lines)
    return repo.run(["sh", "-e", HOOK, "origin", "url"], stdin, **env)


def _ran(repo):
    return repo.log.read_text().splitlines() if repo.log.exists() else []


def _branch(sha, remote_sha=ZERO, ref="refs/heads/feature"):
    return (ref, sha, ref, remote_sha)


def test_clean_push_runs_the_checks(repo):
    head = repo.commit({"a.txt": "fine\n"})
    proc = _push(repo, _branch(head))
    assert proc.returncode == 0, proc.stderr
    assert _ran(repo) == ["npm run typecheck", "npx vitest run"]


def test_leak_blocks_before_typecheck(repo):
    head = repo.commit({"a.txt": f"{pattern_secret()}\n"})
    proc = _push(repo, _branch(head))
    assert proc.returncode != 0
    assert "push blocked" in proc.stderr
    assert _ran(repo) == []


def test_delete_is_skipped(repo):
    repo.commit({"a.txt": f"{pattern_secret()}\n"})
    proc = _push(repo, ("(delete)", ZERO, "refs/heads/gone", "f" * 40))
    assert proc.returncode == 0, proc.stderr


def test_existing_branch_scans_from_the_remote_tip(repo):
    published = repo.commit({"a.txt": f"{pattern_secret()}\n"})
    head = repo.commit({"b.txt": "fine\n"})
    assert _push(repo, _branch(head, published)).returncode == 0
    # the same commits as a new branch: nothing is on the remote yet
    assert _push(repo, _branch(head)).returncode != 0


def test_new_branch_excludes_what_the_remote_has(repo):
    published = repo.commit({"a.txt": f"{pattern_secret()}\n"})
    repo.git("update-ref", "refs/remotes/origin/main", published)
    head = repo.commit({"b.txt": "fine\n"})
    assert _push(repo, _branch(head)).returncode == 0


def test_every_ref_line_is_scanned(repo):
    clean = repo.commit({"a.txt": "fine\n"})
    leak = repo.commit({"b.txt": f"{pattern_secret()}\n"})
    proc = _push(repo, _branch(clean, ref="refs/heads/one"),
                 _branch(leak, clean, ref="refs/heads/two"))
    assert proc.returncode != 0
    assert _ran(repo) == []


def test_override_covers_patterns_not_known_values(repo, tmp_path):
    base = repo.commit()
    head = repo.commit({"a.txt": f"{pattern_secret()}\n"})
    assert _push(repo, _branch(head, base),
                 PAPERCLAW_ALLOW_SENSITIVE="1").returncode == 0
    (tmp_path / "private-values").write_text(known_value() + "\n")
    head = repo.commit({"b.txt": f"{known_value()}\n"})
    proc = _push(repo, _branch(head, base), PAPERCLAW_ALLOW_SENSITIVE="1")
    assert proc.returncode != 0
    assert "private-values:1" in proc.stdout
    assert known_value() not in proc.stdout + proc.stderr
