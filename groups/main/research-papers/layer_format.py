#!/usr/bin/env python3
"""Recognises a turn belonging to the guided-reading loop by its field labels.

The Q&A backstop files any substantive answer about a paper as a callout on that
paper's page. A layer explanation is a long substantive answer about a paper, so
it would be filed too — putting the same material on the page twice, and in the
wrong shape: a callout preserves the assistant's words, while the record exists
to preserve the reader's own.

The labels below are the contract between this repository and the external skill
that produces the format. They are declared HERE, where the build can see them
and tests can pin them; declaring them only in the skill would leave the backstop
matching on something no test in this repository could check.

Only the labels live here. What goes under them — the pedagogy, the voice, the
order the moves are driven in — belongs to the skill and is deliberately absent.
"""
import re

# The fixed field labels of a layer, in the order the format writes them.
LABELS = (
    '한 문장',
    '그림',
    '어떻게',
    '그림이 깨지는 곳',
    '새로 나온 말',
    '근거',
)

# Two labels can be reached by accident — a summary that opens "한 문장:" and
# closes with "근거:" is ordinary prose. Three distinct ones, each starting its
# own line, is the format.
MIN_LABELS = 3

# A label counts only at the head of a line, optionally wrapped in markdown
# emphasis and optionally quoted or bulleted. Mid-sentence the same words are
# just words: "이 논문을 한 문장: 으로 줄이면" is not a layer, and matching it
# would silence the backstop on a genuine answer.
_LINE_HEAD = r'^[ \t>*\-]*'
_LABEL = re.compile(
    _LINE_HEAD + r'(?:\*\*|__|\*)?\s*(' + '|'.join(
        re.escape(label) for label in LABELS) + r')\s*(?:\*\*|__|\*)?\s*:',
    re.MULTILINE)


def labels_in(text: str) -> set:
    """The layer field labels `text` uses, as line-leading fields."""
    if not text:
        return set()
    return set(_LABEL.findall(text))


def is_layer(text: str) -> bool:
    """Does this message carry the guided-reading layer format?

    Args:
        text: A message body, as stored.

    Returns:
        True when the message is one of the loop's own layer explanations. A
        genuine question asked while a descent is open carries none of the
        labels and is reported False, so turning the loop on does not turn
        ordinary Q&A saving off.
    """
    return len(labels_in(text)) >= MIN_LABELS
