"""The commands the group memory tells the agent to run must exist.

`groups/main/CLAUDE.md` is the only instruction the container agent reads before
driving a descent, and it names flags in prose. A renamed flag there fails at the
worst moment — mid-session, with the reader waiting — and prose cannot notice
that it has gone stale. So the doc is checked against the parsers themselves.
"""
import os
import re
import subprocess
import sys

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "groups", "main", "research-papers",
)
DOC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "groups", "main", "CLAUDE.md",
)

# A command invocation in the doc, and every long flag that follows it up to the
# next shell command or blank line.
# The backslash-newline alternative must come FIRST: a bare "[^\n]" would
# swallow the backslash and stop at the newline, silently dropping every
# flag on a continuation line — including the one that guards against
# writing a descent to the wrong paper.
_CALL = re.compile(r"python3 (\w+\.py)((?:\\\n|[^\n#])*)")
_FLAG = re.compile(r"--[a-z][a-z-]+")


def documented_flags():
    """{script: {flags the doc tells the agent to pass}}."""
    found = {}
    for script, tail in _CALL.findall(open(DOC, encoding="utf-8").read()):
        found.setdefault(script, set()).update(_FLAG.findall(tail))
    return found


def real_flags(script):
    """The long flags a script's own parser accepts."""
    helped = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), "--help"],
        capture_output=True, text=True, check=True, timeout=60)
    return set(_FLAG.findall(helped.stdout))


class TestTheDocumentedCommandsAreReal:

    def test_the_doc_names_at_least_one_command(self):
        # Guards the check itself: a regex that stops matching would otherwise
        # turn this whole file green by finding nothing to verify.
        assert documented_flags()

    def test_every_flag_the_doc_passes_is_a_flag_the_script_accepts(self):
        for script, flags in documented_flags().items():
            accepted = real_flags(script)
            unknown = flags - accepted
            assert not unknown, "%s does not accept %s" % (script,
                                                           sorted(unknown))
