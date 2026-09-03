"""Pairing a page's sections with the source's, before citations are aligned.

The source numbers its sections at the top level — `IV` — while the translated
page carries the subsections it is made of: `IV-A`, `IV-B`, `IV-C`. Nothing
matched, so every section was skipped and a paper came out with its reference
list injected and not one citation linked.
"""
import link_references as lr


class TestFindingTheSourceSectionAPageSectionBelongsTo:

    KEYS = {"I", "II", "III", "IV", "V", "VI", "VII"}

    def test_a_section_matches_itself(self):
        assert lr.source_key_for("IV", self.KEYS) == "IV"

    def test_a_roman_subsection_folds_into_its_parent(self):
        assert lr.source_key_for("IV-A", self.KEYS) == "IV"

    def test_a_dotted_subsection_folds_too(self):
        assert lr.source_key_for("5.1", {"5", "6"}) == "5"

    def test_a_deeper_subsection_folds_all_the_way(self):
        assert lr.source_key_for("5.1.2", {"5"}) == "5"

    def test_an_appendix_letter_folds(self):
        assert lr.source_key_for("A.3", {"A", "B"}) == "A"

    def test_a_section_the_source_does_not_have_folds_to_nothing(self):
        assert lr.source_key_for("Z-A", self.KEYS) is None

    def test_a_heading_with_no_key_is_not_folded(self):
        assert lr.source_key_for("", self.KEYS) is None


class TestFoldingTheSlots:
    """Subsections are contiguous and in reading order, so the parent's citation
    sequence is simply theirs joined — which is what the source records."""

    def test_subsections_join_in_reading_order(self):
        slots = {"IV-A": [("b1", 1)], "IV-B": [("b2", 2), ("b3", 3)]}
        folded = lr.fold_slots(slots, {"IV"})
        assert folded == {"IV": [("b1", 1), ("b2", 2), ("b3", 3)]}

    def test_a_parent_with_its_own_citations_comes_first(self):
        slots = {"V": [("b0", 9)], "V-A": [("b1", 1)]}
        assert lr.fold_slots(slots, {"V"}) == {"V": [("b0", 9), ("b1", 1)]}

    def test_a_section_that_already_matches_is_untouched(self):
        slots = {"II": [("b1", 4)]}
        assert lr.fold_slots(slots, {"II"}) == {"II": [("b1", 4)]}

    def test_a_section_the_source_lacks_is_dropped(self):
        # Keeping it would only produce a section that can never be paired.
        slots = {"II": [("b1", 4)], "Z": [("b2", 5)]}
        assert lr.fold_slots(slots, {"II"}) == {"II": [("b1", 4)]}
