"""The pre-push hook: it reads git's ref lines, scans exactly what the push
publishes (commits, tags, ref names), and does so before typecheck and tests,
which it then skips.

The temporary repo has no scripts/ of its own on purpose. From a worktree git
runs the MAIN checkout's hook (core.hooksPath is shared and absolute) inside
the worktree, so the hook must find its scanners next to itself, not in cwd.
"""
import os

import pytest

from leak_helpers import (REPO, Repo, clean_env, fake_arxiv, fake_email,
                          known_value, pattern_secret)

HOOK = os.path.join(REPO, ".husky", "pre-push")
ZERO = "0" * 40


@pytest.fixture
def repo(tmp_path):
    """A repo with no scripts/ of its own, with npm and npx stubbed to record
    that they ran."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in ("npm", "npx"):
        stub = stubs / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "$STUB_LOG"\n')
        stub.chmod(0o755)
    env = clean_env(tmp_path, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                    STUB_LOG=str(tmp_path / "stub.log"))
    r = Repo(tmp_path / "r", env)
    r.git("remote", "add", "origin", str(tmp_path / "remote.git"))
    r.log = tmp_path / "stub.log"
    return r


def _push(repo, *lines, remote="origin", **env):
    stdin = "".join(" ".join(line) + "\n" for line in lines)
    return repo.run(["sh", "-e", HOOK, remote, "url"], stdin, **env)


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


def test_scanners_come_from_the_hook_not_the_cwd(repo):
    assert not os.path.exists(os.path.join(repo.path, "scripts"))
    head = repo.commit({"a.txt": "fine\n"})
    proc = _push(repo, _branch(head))
    assert proc.returncode == 0, proc.stderr
    head = repo.commit({"b.txt": f"{pattern_secret()}\n"})
    assert _push(repo, _branch(head)).returncode != 0


def _tag(repo, name, target, message, **env):
    repo.run(["git", "tag", "-a", name, "-m", message, target], **env)
    sha = repo.git("rev-parse", f"refs/tags/{name}")
    return (f"refs/tags/{name}", sha, f"refs/tags/{name}", ZERO)


def test_annotated_tags_are_scanned(repo):
    head = repo.commit({"a.txt": "fine\n"})
    repo.git("update-ref", "refs/remotes/origin/main", head)
    assert _push(repo, _tag(repo, "v1", head, "release")).returncode == 0
    proc = _push(repo, _tag(repo, "v2", head, f"notes {pattern_secret()}"))
    assert proc.returncode != 0
    assert _ran(repo) == ["npm run typecheck", "npx vitest run"]  # v1 only
    proc = _push(repo, _tag(repo, "v3", head, "ok",
                            GIT_COMMITTER_EMAIL=fake_email()))
    assert proc.returncode != 0 and "[tagger]" in proc.stderr
    # a tag of a tag is peeled, and every level is scanned
    inner = _tag(repo, "v4", head, f"inner {pattern_secret()}")
    outer = _tag(repo, "v5", inner[1], "outer")
    assert _push(repo, outer).returncode != 0


def test_a_ref_at_a_blob_or_tree_is_refused(repo):
    head = repo.commit({"a.txt": "fine\n"})
    blob = repo.run(["git", "hash-object", "-w", "--stdin"],
                    f"token={pattern_secret()}").stdout.strip()
    tree = repo.git("rev-parse", f"{head}^{{tree}}")
    for sha in (blob, tree):
        proc = _push(repo, ("refs/tags/odd", sha, "refs/tags/odd", ZERO))
        assert proc.returncode != 0 and "cannot scan" in proc.stderr


def test_the_ref_name_is_scanned(repo):
    head = repo.commit({"a.txt": "fine\n"})
    ref = f"refs/heads/notes-{fake_arxiv()}"
    proc = _push(repo, _branch(head, ref=ref))
    assert proc.returncode != 0 and "ref name" in proc.stderr


def test_a_remote_never_fetched_excludes_what_any_remote_has(repo, tmp_path):
    published = repo.commit({"a.txt": f"{pattern_secret()}\n"})
    repo.git("update-ref", "refs/remotes/origin/main", published)
    repo.git("remote", "add", "fork", str(tmp_path / "fork.git"))
    head = repo.commit({"b.txt": "fine\n"})
    proc = _push(repo, _branch(head), remote="fork")
    assert proc.returncode == 0, proc.stderr


def test_a_rebased_branch_skips_what_the_remote_already_has(repo):
    base = repo.commit({"a.txt": "fine\n"})
    old_tip = repo.commit({"b.txt": "mine\n"})
    repo.git("reset", "-q", "--hard", base)
    on_main = repo.commit({"c.txt": f"{pattern_secret()}\n"})
    repo.git("update-ref", "refs/remotes/origin/main", on_main)
    new_tip = repo.commit({"b.txt": "mine\n"})
    proc = _push(repo, _branch(new_tip, old_tip))
    assert proc.returncode == 0, proc.stderr
