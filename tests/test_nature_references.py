"""Reading a bibliography from a Nature article page.

These papers have no arXiv source, so the whole citation pipeline — which reads
LaTeXML HTML — has nothing to work with and both pages ended up with no
reference list at all. Nature publishes the bibliography even where the full
text is paywalled, which is enough to inject the list and link the body's
`[N]` markers.

What it is NOT enough for is alignment: the article page carries no in-body
citation anchors, so there is nothing to check the page's numbering against. It
is used only where the numbering is the paper's own, which is the normal case
for a translation made from the paper itself.
"""
import nature_source as ns


PAGE = '''
<ol>
<li class="c-article-references__item" data-counter="1"><p class="c-article-references__text" id="ref-CR1">Author, A. <i>A first work.</i> PhD thesis (1994)</p></li>
<li class="c-article-references__item" data-counter="2"><p class="c-article-references__text" id="ref-CR2">Second, B. A second work &amp; more. In <i>A Venue</i> (2006)</p></li>
<li class="c-article-references__item" data-counter="11"><p class="c-article-references__text" id="ref-CR11">Third, C. A third work (2006)</p></li>
</ol>
'''


class TestReadingTheList:

    def test_every_entry_is_found(self):
        assert len(ns.parse_bibliography(PAGE)) == 3

    def test_entries_are_numbered_as_the_paper_cites_them(self):
        assert [e["num"] for e in ns.parse_bibliography(PAGE)] == [1, 2, 11]

    def test_the_markup_inside_an_entry_is_dropped(self):
        first = ns.parse_bibliography(PAGE)[0]
        assert "<i>" not in first["text"] and "A first work" in first["text"]

    def test_entities_are_decoded(self):
        second = ns.parse_bibliography(PAGE)[1]
        assert "&amp;" not in second["text"] and "&" in second["text"]

    def test_the_label_is_the_number(self):
        assert [e["label"] for e in ns.parse_bibliography(PAGE)] == ["1", "2", "11"]

    def test_a_page_without_a_bibliography_yields_nothing(self):
        assert ns.parse_bibliography("<p>본문뿐입니다</p>") == []


class TestRecognisingTheSource:

    def test_a_nature_article_url_is_one(self):
        assert ns.article_url("https://www.nature.com/articles/xxxxxxxxx")

    def test_the_http_form_too(self):
        assert ns.article_url("http://nature.com/articles/yyyyyyyyy")

    def test_another_publisher_is_not(self):
        assert ns.article_url("https://example.org/abs/paper") is None

    def test_nothing_is_not(self):
        assert ns.article_url("") is None


class TestLinkingByNumber:
    """With no source body to align against, the only honest mapping is the
    identity one: `[7]` means entry 7. That holds when the page's numbering is
    the paper's own, and the injected list makes it checkable by eye."""

    ENTRIES = [{"num": 1, "label": "1", "text": "A. Author. A first work."},
               {"num": 7, "label": "7", "text": "B. Second. A seventh work."}]

    def test_a_marker_maps_to_its_own_number(self):
        assert ns.identity_mapping("연구 [7] 참고", self.ENTRIES) == [7]

    def test_a_group_maps_each_member(self):
        assert ns.identity_mapping("연구 [1, 7] 참고", self.ENTRIES) == [1, 7]

    def test_a_number_with_no_entry_is_dropped(self):
        # Linking it would point at nothing; leaving it plain is honest.
        assert ns.identity_mapping("연구 [99] 참고", self.ENTRIES) == []

    def test_a_block_with_no_markers_maps_nothing(self):
        assert ns.identity_mapping("평범한 문단입니다.", self.ENTRIES) == []

    def test_a_range_is_expanded(self):
        entries = [{"num": n, "label": str(n), "text": f"work {n}"}
                   for n in (1, 2, 3)]
        assert ns.identity_mapping("연구 [1-3] 참고", entries) == [1, 2, 3]
