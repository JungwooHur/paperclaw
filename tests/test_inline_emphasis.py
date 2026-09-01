"""Markdown emphasis left in the text instead of applied to it.

The assembler sometimes writes `*중요*` as characters rather than turning it into
an italic span, so the asterisks show up on the page. Converting them is only
safe when the block is unambiguous: maths uses the same character, and an
unbalanced pair means the block was mangled, not emphasised.
"""
import inline_emphasis as ie


def para(text):
    return {"id": "b1", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text",
                                         "text": {"content": text},
                                         "plain_text": text}]}}


class TestWhatIsConverted:

    def test_one_balanced_pair_is_emphasis(self):
        assert ie.convert(para("이것은 *중요한* 부분입니다")) is not None

    def test_the_asterisks_are_gone_from_the_text(self):
        new = ie.convert(para("이것은 *중요한* 부분입니다"))
        assert ie.text_of(new) == "이것은 중요한 부분입니다"

    def test_the_enclosed_words_become_italic(self):
        new = ie.convert(para("이것은 *중요한* 부분입니다"))
        italic = [s for s in new["paragraph"]["rich_text"]
                  if s.get("annotations", {}).get("italic")]
        assert [s["text"]["content"] for s in italic] == ["중요한"]

    def test_text_around_it_is_kept_in_order(self):
        new = ie.convert(para("앞 *가운데* 뒤"))
        assert [s["text"]["content"]
                for s in new["paragraph"]["rich_text"]] == ["앞 ", "가운데", " 뒤"]


class TestWhatIsRefused:

    def test_a_block_with_no_asterisks_is_left_alone(self):
        assert ie.convert(para("평범한 문장입니다")) is None

    def test_an_unbalanced_asterisk_is_left_alone(self):
        # One asterisk means the block was mangled; guessing where the emphasis
        # ended would rewrite the sentence.
        assert ie.convert(para("이것은 *중요한 부분입니다")) is None

    def test_two_pairs_are_left_alone(self):
        assert ie.convert(para("*하나* 그리고 *둘*")) is None

    def test_latex_inside_the_pair_is_left_alone(self):
        # `*` and braces are maths characters. Touching a block that carries them
        # risks rewriting a formula, which is worse than leaving the asterisks.
        assert ie.convert(para("이는 *C_{\\rm min}을 넘어* 커집니다")) is None

    def test_a_bold_pair_is_left_alone(self):
        assert ie.convert(para("이것은 **굵게** 입니다")) is None

    def test_a_pair_spanning_two_spans_is_left_alone(self):
        block = para("")
        block["paragraph"]["rich_text"] = [
            {"type": "text", "text": {"content": "앞 *가운"}, "plain_text": "앞 *가운"},
            {"type": "text", "text": {"content": "데* 뒤"}, "plain_text": "데* 뒤"},
        ]
        assert ie.convert(block) is None

    def test_a_heading_is_converted_too(self):
        block = para("제목 *강조* 입니다")
        block["type"] = "heading_3"
        block["heading_3"] = block.pop("paragraph")
        assert ie.convert(block) is not None
