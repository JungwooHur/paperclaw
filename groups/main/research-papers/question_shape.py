#!/usr/bin/env python3
"""Whether a message is a question worth filing as a Q&A.

The same judgement is needed in two places — the backstop that scans recent
exchanges, and the writer every Q&A goes through — so it lives in neither. When
only the backstop had it, a message asking for the paper to be PROCESSED was
written onto that paper as though it were a question about it: the writer
validated nothing at all.

Imports nothing but `re`, like `reference_section`, so a healer can never fail to
load because of it.
"""
import re

# A message can carry the path of the file it arrived with. That path says
# nothing about whether the message asks anything, and it is long — long enough
# on its own to push a bare request past the length below, which is exactly how
# "정리해" came to be filed as a question.
_ATTACHMENT = re.compile(r"\n*\s*\[(?:첨부[^\]]*|attached[^\]]*)\]\s*$", re.I)

# Below this, a message with no question marker of any kind is taken as a remark.
LONG_ENOUGH = 60


def message_body(content: str) -> str:
    """The message with its attachment line removed."""
    return _ATTACHMENT.sub("", content or "").strip()


def is_question_like(content: str) -> bool:
    """Filter out imperative commands (추가해, 정리하자, 번역해, 찾아줘, ...)
    which aren't Q&A-worthy. Questions typically: end with '?', contain
    '뭐/무엇/왜/어떻게/어디', or ask for an explanation."""
    c = message_body(content)
    if not c: return False
    if len(c) < 10: return False
    # Imperative paper-management commands — skip
    imperative_tails = [
        "정리하자", "정리해", "정리해줘", "추가해", "추가해줘",
        "찾아줘", "찾아봐", "번역해", "번역해줘", "올려줘", "저장해",
    ]
    cl = c.lower()
    if any(cl.endswith(t) for t in imperative_tails):
        return False
    if "?" in c:
        return True
    # A sentence that CLOSES like a statement is a remark, not a question. One such
    # ("…그 결과를 보여준다.") was filed as a Q&A, so the callout asked nothing.
    if re.search(r"(다|네|군|구나|음|임|죠|네요|습니다)\.?$", c):
        return False
    # Explicit "I want this explained" markers. The old rule ended here with
    # `len(c) >= 60 and "해" not in c[-4:]`, which rejected a real question purely
    # because it happened to end on a syllable containing 해 ("…까지는 이해했어") —
    # a question about the paper then never reached Notion at all.
    if re.search(r"(왜|뭐야|무엇|어떻게|어디|누구|언제|얼마|차이|의미|설명해|알려줘|뜻이야"
                 r"|이해가 안|이해 안|모르겠|헷갈|궁금|무슨|어느|은지|는지|을까|ㄹ까)", c):
        return True
    return len(c) >= LONG_ENOUGH
