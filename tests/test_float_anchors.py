"""Where a figure or table is inserted on the page.

The anchor is the first body block that mentions the number. A caption is a body
block too, and captions cross-reference each other — so one figure's caption
saying "compare with Figure 13" claimed Figure 13's place, and Figure 13 landed
directly under Figure 1, chapters away from the text about it.
"""
import extract_paper_figures as ef


def para(text):
    return {"id": "b-" + text[:8], "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


class TestRecognisingACaptionBlock:

    def test_a_figure_caption_is_one(self):
        assert ef.caption_number("Figure 1: Language modeling 성능은") == ("figure", 1)

    def test_the_short_word_too(self):
        assert ef.caption_number("Fig. 4: 왼쪽은") == ("figure", 4)

    def test_a_table_caption_is_one(self):
        assert ef.caption_number("Table 3: Fits to the data") == ("table", 3)

    def test_the_korean_word_too(self):
        assert ef.caption_number("그림 2: 성능은") == ("figure", 2)

    def test_a_body_sentence_is_not_a_caption(self):
        # It opens with the same words but goes straight into prose, and it is
        # exactly the mention a figure should be anchored to.
        assert ef.caption_number("Figure 13에서 우리는 성능을 보여줍니다") is None

    def test_ordinary_prose_is_not(self):
        assert ef.caption_number("우리는 성능을 측정했습니다") is None


class TestAnchoringPastOtherCaptions:

    def test_a_cross_reference_inside_another_caption_is_skipped(self):
        blocks = [para("Figure 1: 성능은 향상됩니다. 비교는 Figure 13을 참조하십시오."),
                  para("본문 문단입니다."),
                  para("Figure 13에서 우리는 batch size를 낮춰 학습했습니다.")]
        assert ef._anchor_for(13, blocks) == blocks[2]["id"]

    def test_a_figure_still_anchors_to_its_own_caption(self):
        blocks = [para("아무 문단"), para("Figure 5: 이것은 다섯 번째 그림입니다.")]
        assert ef._anchor_for(5, blocks) == blocks[1]["id"]

    def test_an_ordinary_first_mention_still_wins(self):
        blocks = [para("Figure 7에서 보듯이 성능이 오릅니다."),
                  para("Figure 7: 일곱 번째 그림.")]
        assert ef._anchor_for(7, blocks) == blocks[0]["id"]

    def test_a_table_caption_does_not_claim_a_figure(self):
        blocks = [para("Table 2: 수치는 Figure 9와 비교하십시오."),
                  para("Figure 9에서 우리는 이를 보여줍니다.")]
        assert ef._anchor_for(9, blocks) == blocks[1]["id"]

    def test_with_only_a_cross_reference_it_falls_through(self):
        # Nothing but another caption mentions it, so there is no honest anchor
        # and the caller's own fallback decides — better than a wrong place.
        blocks = [para("Figure 1: 비교는 Figure 13을 참조하십시오.")]
        assert ef._anchor_for(13, blocks) is None
