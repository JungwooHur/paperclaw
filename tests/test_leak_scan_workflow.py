"""The CI leak scan: its log is public, and a PR title is untrusted text.

So every scanning step must redact, and a `${{ }}` expression may only ever be
the value of an env: entry; inside a run: script it would be pasted into the
shell and executed.
"""
import os
import re

from leak_helpers import REPO

WORKFLOW = os.path.join(REPO, ".github", "workflows", "leak-scan.yml")


def _steps():
    """Each step's lines, split on the `- ` that starts it."""
    steps, cur = [], None
    for line in open(WORKFLOW, encoding="utf-8").read().splitlines():
        if re.match(r"^\s+- (name|uses|run):", line):
            cur = [line]
            steps.append(cur)
        elif cur is not None:
            cur.append(line)
    return steps


def test_expressions_only_as_env_values():
    for line in open(WORKFLOW, encoding="utf-8").read().splitlines():
        if "${{" in line and not line.lstrip().startswith("#"):
            assert re.fullmatch(r"\s+[A-Z_]+: \$\{\{ [\w.]+ \}\}", line), line


def test_every_scan_redacts():
    scans = [s for s in _steps() if any("check-sensitive.sh" in ln for ln in s)]
    assert len(scans) == 2
    for step in scans:
        assert any(re.fullmatch(r"\s+PAPERCLAW_REDACT: '1'", ln) for ln in step)
