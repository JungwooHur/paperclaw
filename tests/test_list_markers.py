"""A list item that kept the bullet character from its markdown source.

Notion draws the bullet for a list block, so a `•` left inside the text renders
a second one beside it. The same leftover appears as `*` or `-` depending on
what the source used.
"""
import list_markers as lm


def item(text, kind="bulleted_list_item"):
    return {"id": "b1", "type": kind,
            kind: {"rich_text": [{"type": "text", "text": {"content": text},
                                  "plain_text": text}]}}


class TestSpottingALeftoverMarker:

    def test_a_bullet_character_is_a_leftover(self):
        assert lm.has_marker(item("• L – 손실"))

    def test_an_asterisk_is_too(self):
        assert lm.has_marker(item("* L – 손실"))

    def test_a_numbered_item_is_checked_as_well(self):
        assert lm.has_marker(item("• 첫째", "numbered_list_item"))

    def test_an_ordinary_item_has_none(self):
        assert not lm.has_marker(item("L – 손실"))

    def test_a_paragraph_is_not_a_list_item(self):
        block = item("• L – 손실")
        block["type"] = "paragraph"
        block["paragraph"] = block.pop("bulleted_list_item")
        assert not lm.has_marker(block)

    def test_a_minus_sign_before_a_number_is_not_a_marker(self):
        # "- 3 dB 감소" opens with arithmetic, not a bullet. Stripping it would
        # silently change what the line says.
        assert not lm.has_marker(item("- 3 dB 감소했습니다"))

    def test_a_dash_before_words_is_a_marker(self):
        assert lm.has_marker(item("- 첫 번째 항목"))

    def test_a_bullet_in_the_middle_is_left_alone(self):
        assert not lm.has_marker(item("항목 A • 항목 B"))


class TestStrippingIt:

    def test_the_marker_goes_and_the_text_stays(self):
        stripped = lm.strip_marker(item("• L – 손실"))
        assert lm.text_of(stripped) == "L – 손실"

    def test_the_block_keeps_its_kind(self):
        stripped = lm.strip_marker(item("• 첫째", "numbered_list_item"))
        assert stripped["type"] == "numbered_list_item"

    def test_an_item_with_no_marker_is_not_rewritten(self):
        assert lm.strip_marker(item("L – 손실")) is None

    def test_formatting_after_the_marker_survives(self):
        block = item("• ")
        block["bulleted_list_item"]["rich_text"] = [
            {"type": "text", "text": {"content": "• "}, "plain_text": "• "},
            {"type": "equation", "equation": {"expression": "L(N)"},
             "plain_text": "L(N)"},
            {"type": "text", "text": {"content": " 은 손실"},
             "plain_text": " 은 손실"},
        ]
        stripped = lm.strip_marker(block)
        kinds = [s["type"] for s in stripped["bulleted_list_item"]["rich_text"]]
        assert "equation" in kinds
        assert lm.text_of(stripped).startswith("L(N)")

    def test_a_marker_alone_in_its_span_removes_that_span(self):
        block = item("")
        block["bulleted_list_item"]["rich_text"] = [
            {"type": "text", "text": {"content": "• "}, "plain_text": "• "},
            {"type": "text", "text": {"content": "본문"}, "plain_text": "본문"},
        ]
        stripped = lm.strip_marker(block)
        assert len(stripped["bulleted_list_item"]["rich_text"]) == 1
