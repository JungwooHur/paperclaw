"""Citations that arrived glued to the word before them.

Nature sets citations as superscripts. A translation made from the rendered
paper flattens them into the text, so the body reads `Method11,12` and
`…구성한다20` — the number looks like part of the word, and nothing can link it
because only bracketed markers are recognised.

The repair inserts brackets and nothing else. Every character that was there
before is still there, in the same order, which is what makes it safe to run on
a page a person is reading.
"""
import superscript_citations as sc


def bare(text):
    """The text with every bracket and space removed.

    The repair inserts only brackets and a space, so this is what must be
    identical before and after — a sharper statement than "undo the edit",
    which cannot tell an inserted bracket from one the paper already had.
    """
    import re
    return re.sub(r"[\[\]\s]", "", text)


class TestWhatItConverts:

    def test_a_single_glued_number(self):
        assert sc.convert("제안된 기법8는", 62) == "제안된 기법 [8]는"

    def test_a_comma_group(self):
        assert sc.convert("(ABC)11,12는", 62) == "(ABC) [11,12]는"

    def test_a_range_keeps_its_dash(self):
        assert sc.convert("도달했다13–15 [3].", 62) == "도달했다 [13–15] [3]."

    def test_after_a_korean_syllable(self):
        assert sc.convert("구성한다20", 62) == "구성한다 [20]"

    def test_several_in_one_line(self):
        out = sc.convert("항목4, 항목5, 항목6에서", 62)
        assert out == "항목 [4], 항목 [5], 항목 [6]에서"


class TestWhatItRefuses:

    def test_a_number_beyond_the_bibliography(self):
        assert sc.convert("결과는 99였다", 62) == "결과는 99였다"

    def test_a_number_that_is_not_glued(self):
        assert sc.convert("우리는 20 개를 썼다", 62) == "우리는 20 개를 썼다"

    def test_a_year(self):
        assert sc.convert("(1994)에 발표되었다", 62) == "(1994)에 발표되었다"

    def test_a_board_size(self):
        # `19` after the `x` is glued and in range, and it is not a citation.
        assert sc.convert("격자는 19x19이다", 62) == "격자는 19x19이다"

    def test_a_decimal(self):
        assert sc.convert("승률은 57.0이었다", 62) == "승률은 57.0이었다"

    def test_something_already_bracketed(self):
        assert sc.convert("이전 연구 [13] 와", 62) == "이전 연구 [13] 와"

    def test_an_identifier_with_a_trailing_number(self):
        # `conv2d` and the like: a letter follows the digits.
        assert sc.convert("우리는 conv2d를 썼다", 62) == "우리는 conv2d를 썼다"


class TestItOnlyInserts:

    def test_nothing_but_brackets_and_a_space_is_added(self):
        for text in ("(ABC)11,12는 좋다", "항목4, 항목5", "도달했다13–15 [3].",
                     "평범한 문장입니다.", "승률은 57.0이었다"):
            assert bare(sc.convert(text, 62)) == bare(text), text

    def test_running_it_twice_changes_nothing_more(self):
        once = sc.convert("(ABC)11,12는", 62)
        assert sc.convert(once, 62) == once
