"""Aligning citations paragraph by paragraph.

Some translations renumber citations LOCALLY — the markers restart at [1] in
every paragraph — so the number carries no global meaning and a whole-section
alignment can never verify anything. What survives translation is the ORDER
within a paragraph, and a paragraph is small enough that matching counts is real
evidence rather than a coincidence.
"""
import link_references as lr


class TestReadingParagraphsFromTheSource:

    HTML = '''
    <section id="S1"><h2>1 Intro</h2>
      <p class="ltx_p">first <a href="#bib.bibx69">x</a> only</p>
      <p class="ltx_p">nothing here</p>
      <p class="ltx_p">two <a href="#bib.bibx80">a</a> and <a href="#bib.bibx5">b</a></p>
      <section id="S1.SS1"><h3>1.1 Sub</h3>
        <p class="ltx_p">nested <a href="#bib.bibx51">c</a></p>
      </section>
    </section>
    <section id="S2"><h2>2 Next</h2>
      <p class="ltx_p">later <a href="#bib.bibx7">d</a></p>
    </section>
    '''

    def test_paragraphs_are_grouped_by_section(self):
        found = lr.source_citation_paragraphs(self.HTML)
        assert sorted(found) == ["1", "2"]

    def test_only_paragraphs_carrying_citations_are_kept(self):
        # A paragraph with no citation cannot be aligned against anything, and
        # keeping it would throw the two sequences out of step.
        assert lr.source_citation_paragraphs(self.HTML)["1"][0] == [69]

    def test_a_paragraph_keeps_its_citations_in_order(self):
        assert lr.source_citation_paragraphs(self.HTML)["1"][1] == [80, 5]

    def test_a_nested_subsection_belongs_to_its_parent(self):
        # The page numbers subsections separately; the source's own section is
        # the whole thing, nested parts included.
        assert lr.source_citation_paragraphs(self.HTML)["1"][2] == [51]

    def test_a_later_section_is_not_swallowed(self):
        assert lr.source_citation_paragraphs(self.HTML)["2"] == [[7]]


class TestPairingParagraphs:

    def test_matching_counts_map_position_by_position(self):
        page = [[("b1", 1), ("b1", 2)]]
        assert lr.align_paragraphs(page, [[80, 5]]) == [[("b1", 80), ("b1", 5)]]

    def test_a_paragraph_whose_count_differs_is_skipped(self):
        page = [[("b1", 1)], [("b2", 1), ("b2", 2)]]
        assert lr.align_paragraphs(page, [[69], [80]]) == [[("b1", 69)], []]

    def test_a_section_with_a_different_number_of_paragraphs_aligns_nothing(self):
        # One paragraph split or merged in translation puts everything after it
        # out of step, and a count that then matches matches by accident.
        page = [[("b1", 1)], [("b2", 1)]]
        assert lr.align_paragraphs(page, [[69]]) == []

    def test_nothing_on_either_side_aligns_nothing(self):
        assert lr.align_paragraphs([], []) == []
