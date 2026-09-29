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
