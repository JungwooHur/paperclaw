"""Citations that arrive as LaTeX keys instead of numbers.

The translation source is NotebookLM's own indexed text, and it leaves
`\\cite{key}` unresolved — the body reads `[ author2024method, shortkey]` where the
published HTML reads `[3, 7]`. Nothing matched those, so a freshly translated
paper had citations the linker could not see.
"""
import link_references as lr


class TestRecognisingAKeyGroup:

    def test_a_single_key_is_a_citation(self):
        assert [m.group(1) for m in lr._CITE.finditer("이는 [ shortkey] 에서")] \
            == ["shortkey"]

    def test_a_group_of_keys_is_one_citation(self):
        found = [m.group(1) for m in
                 lr._CITE.finditer("이는 [ author2024method, shortkey] 입니다")]
        assert found == ["author2024method, shortkey"]

    def test_a_number_is_still_a_citation(self):
        assert [m.group(1) for m in lr._CITE.finditer("이는 [3, 7] 입니다")] \
            == ["3, 7"]

    def test_a_key_group_expands_to_its_members(self):
        assert lr.expand(" author2024method, shortkey") == ["author2024method",
                                                      "shortkey"]

    def test_a_number_group_still_expands_to_numbers(self):
        assert lr.expand("1-3") == [1, 2, 3]

    def test_a_bracketed_word_that_is_not_a_key_is_ignored(self):
        # `[see below]` is prose. A key has no spaces inside it.
        assert not lr._CITE.search("이는 [see below] 참고")

    def test_a_bracketed_dotted_number_is_not_a_citation(self):
        # A paper id sometimes opens the text; it is not a reference.
        assert not lr._CITE.search("[12.34] A paper title")


class TestRenumberingKeepsProseIntact:

    def spans(self, text):
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    def test_a_key_becoming_a_number_is_not_a_prose_change(self):
        assert lr.same_prose(self.spans("이는 [ shortkey] 에서"),
                             self.spans("이는 [7] 에서"))

    def test_a_key_group_becoming_numbers_is_not_either(self):
        assert lr.same_prose(self.spans("[ author2024method, shortkey] 입니다"),
                             self.spans("[3, 7] 입니다"))

    def test_losing_a_word_around_it_is_still_caught(self):
        assert not lr.same_prose(self.spans("이는 [ shortkey] 에서"),
                                 self.spans("이는 [7]"))
