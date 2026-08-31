"""Make the paper-processing scripts importable from the tests.

They live in the group folder rather than a package directory, because the
container mounts that folder and runs them in place. Tests reach them the same
way the scripts reach each other.
"""
import os
import sys

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "groups", "main", "research-papers",
)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
