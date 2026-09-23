#!/usr/bin/env python3
"""Keeps a second copy of a scan from starting while one is running.

The Q&A backstop has two invocations — the five-minute timer, and the trigger
that fires when a reply goes out — and the healer is saturated often enough that
they overlap. Two copies each read a page, each see no callout for a pair, and
each file one. A lock on only one of the two invocations cannot prevent that,
because the other walks straight into it.

The lock therefore lives in the script rather than in whatever started it.

Imports nothing but the standard library.
"""
import fcntl
import os


def acquire(path: str):
    """Take the lock, or None if another run holds it.

    A lock that cannot be taken at all — an unwritable path — is reported as
    taken rather than as held by someone else. Failing to lock must never become
    a reason to skip the work; the checks behind it still stand.
    """
    try:
        handle = open(path, 'w')
    except OSError:
        return _UNLOCKED
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release(handle) -> None:
    """Give the lock back. Safe to call with whatever `acquire` returned."""
    if handle is None or handle is _UNLOCKED:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class _Unlocked:
    """Stands for "no lock was possible here", which is not "someone else has it"."""


_UNLOCKED = _Unlocked()
