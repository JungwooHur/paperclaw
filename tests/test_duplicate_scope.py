"""Which headings count as copies of one another.

An unlabelled heading can only be a duplicate of another under the same parent,
and only when the two titles are the same title. The key used to compare them
kept the first 16 characters of the normalised title, so two sibling titles that
merely START alike collided: "Settings for model A" and "Settings
for model B" both became `settingsformodel`. Papers repeat
their hyperparameter sentences from one stage to the next, so the bodies matched
too, and the healer — which deduplicates on its own every cycle — would archive
a real subsection.
"""
import heal_verify as hv
import verify_sections as vs

SHARED = ("To train the model we set the learning rate to three times ten to the "
          "minus six, the KL coefficient to 0.001 and the sampling temperature to "
          "one; each step holds 32 unique questions for a batch size of 512.")


def block(kind, text, bid):
    return {"type": kind, "id": bid,
            kind: {"rich_text": [{"type": "text", "plain_text": text,
                                  "text": {"content": text}}]}}


def page(second_title, second_body=SHARED):
    return [
        block("heading_2", "Training (학습)", "h0"),
        block("heading_3", "Settings for model A (학습 세부 사항)", "h1"),
        block("paragraph", SHARED, "p1"),
        block("heading_3", second_title, "h2"),
        block("paragraph", second_body, "p2"),
    ]


def duplicated(blocks):
    return [occ for occ in vs.duplicate_groups(vs.group_sections(blocks)).values()
            if len(occ) > 1]


class TestSiblingsThatStartAlike:

    def test_they_are_not_copies(self):
        blocks = page("Settings for model B (첫 번째 단계)")
        assert duplicated(blocks) == []

    def test_the_healer_archives_nothing(self):
        blocks = page("Settings for model B (첫 번째 단계)")
        assert hv.dedupe_duplicates("page", blocks, apply=False) == 0


def reappended(copy_title):
    """A copy that landed after another section — how a re-upload looks. (A
    copy directly under its original is absorbed as a heading echo instead.)"""
    return page("Settings for model B (첫 번째 단계)") + [
        block("heading_3", copy_title, "h3"),
        block("paragraph", SHARED, "p3"),
    ]


class TestARealCopy:

    def test_the_same_title_twice_is_still_caught(self):
        blocks = reappended("Settings for model A (학습 세부 사항)")
        assert len(duplicated(blocks)) == 1

    def test_the_translation_in_brackets_does_not_split_them(self):
        # The Korean half is written afresh on every upload, so it may differ
        # between two copies of the same section.
        blocks = reappended("Settings for model A (학습 상세)")
        assert len(duplicated(blocks)) == 1

    def test_the_healer_archives_the_copy(self):
        blocks = reappended("Settings for model A (학습 세부 사항)")
        assert hv.dedupe_duplicates("page", blocks, apply=False) == 2


class TestLetteredAppendixSubsections:
    """`A-A Contributions`, `A-B Attention pattern`, … are seven different
    subsections of appendix A. `section_key` read every one of them as `A`, so
    the healer saw section A seven times and archived six real subsections —
    thirty blocks on one page — without comparing a word of their bodies."""

    def test_each_has_its_own_key(self):
        assert vs.section_key("A-A Contributions (기여)") == "A-A"
        assert vs.section_key("A-B Attention pattern (어텐션 패턴)") == "A-B"

    def test_an_ieee_subsection_is_unchanged(self):
        assert vs.section_key("V-A Subtask instructions (하위작업 지시사항)") == "V-A"

    def test_a_bare_appendix_letter_is_unchanged(self):
        assert vs.section_key("A. Proofs (증명)") == "A"

    def test_the_healer_archives_none_of_them(self):
        # A body in front, as on a real page — without it the healer's own
        # "never archive most of the page" cap hides the fault.
        blocks = [block("paragraph", f"본문 문단 {n}입니다.", f"b{n}") for n in range(12)]
        blocks.append(block("heading_1", "A Appendix (부록)", "h0"))
        for n, (label, body) in enumerate([
                ("A-A Contributions (기여)", "우리는 이 연구에 이렇게 기여했습니다. " * 6),
                ("A-B Attention pattern (어텐션 패턴)", "어텐션 패턴은 다음과 같습니다. " * 6),
                ("A-C Training details (학습 세부)", "학습은 이렇게 진행했습니다. " * 6)]):
            blocks += [block("heading_2", label, f"h{n + 1}"),
                       block("paragraph", body, f"p{n + 1}")]
        assert hv.dedupe_duplicates("page", blocks, apply=False) == 0


class TestANumberedCopyNeedsAMatchingBody:
    """Even when two headings share a key, archiving one on the key alone is
    how a mis-keyed label destroyed real content. A re-appended copy repeats its
    body; a different section does not."""

    def test_same_key_different_bodies_is_not_archived(self):
        blocks = [block("heading_1", "3 Method (방법)", "h1"),
                  block("paragraph", "첫 번째 방법의 본문입니다. " * 8, "p1"),
                  block("heading_1", "4 Results (결과)", "h2"),
                  block("paragraph", "결과를 설명하는 본문입니다. " * 8, "p2"),
                  block("heading_1", "3 Evaluation (평가)", "h3"),
                  block("paragraph", "평가 절차는 전혀 다른 내용입니다. " * 8, "p3")]
        assert hv.dedupe_duplicates("page", blocks, apply=False) == 0

    def test_a_real_numbered_copy_is_still_archived(self):
        body = "첫 번째 방법의 본문입니다. " * 8
        blocks = [block("heading_1", "3 Method (방법)", "h1"),
                  block("paragraph", body, "p1"),
                  block("heading_1", "4 Results (결과)", "h2"),
                  block("paragraph", "결과를 설명하는 본문입니다. " * 8, "p2"),
                  block("heading_1", "3 Method (방법)", "h3"),
                  block("paragraph", body, "p3")]
        assert hv.dedupe_duplicates("page", blocks, apply=False) == 2
