"""Reading the source's bibliography out of arXiv's HTML.

Two shapes exist. A numeric bibliography gives each entry an id like `bib.bib7`
and cites it as `[7]`. An alphabetic one — the style that labels entries by
author initials and year — gives ids like `bib.bibx7` and cites `[ACDE12]`. The
`x` is the only structural difference, and missing it makes a paper look like it
has no bibliography at all.
"""
import link_references as lr


NUMERIC = '''
<ul class="ltx_biblist">
<li id="bib.bib1" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
<span class="ltx_bibblock">A. Author. A title. In Venue, 2020.</span></li>
<li id="bib.bib2" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[2]</span>
<span class="ltx_bibblock">B. Author. Another title. In Venue, 2021.</span></li>
</ul>
'''

ALPHA = '''
<ul class="ltx_biblist">
<li id="bib.bibx1" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[ACDE12]</span>
<span class="ltx_bibblock">A. Author, B. Second, and C. Third.</span>
<span class="ltx_bibblock">On the origin of a thing.</span></li>
<li id="bib.bibx2" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[VSP + 17]</span>
<span class="ltx_bibblock">D. Fourth et al. A later title.</span></li>
</ul>
'''


class TestReadingEitherShape:

    def test_a_numeric_bibliography_is_read(self):
        assert len(lr.parse_bibliography(NUMERIC)) == 2

    def test_an_alphabetic_bibliography_is_read_too(self):
        assert len(lr.parse_bibliography(ALPHA)) == 2

    def test_entries_keep_the_order_of_their_ids(self):
        entries = lr.parse_bibliography(ALPHA)
        assert [e["num"] for e in entries] == [1, 2]

    def test_the_entry_text_survives(self):
        first = lr.parse_bibliography(ALPHA)[0]
        assert "On the origin of a thing" in first["text"]


class TestTheLabelTheBodyCites:

    def test_an_alphabetic_entry_carries_its_own_label(self):
        entries = lr.parse_bibliography(ALPHA)
        assert [e["label"] for e in entries] == ["ACDE12", "VSP + 17"]

    def test_a_numeric_entry_is_labelled_by_its_number(self):
        entries = lr.parse_bibliography(NUMERIC)
        assert [e["label"] for e in entries] == ["1", "2"]

    def test_the_label_is_not_part_of_the_entry_text(self):
        # It is written back as the marker, so leaving it in the text would
        # print it twice.
        first = lr.parse_bibliography(ALPHA)[0]
        assert not first["text"].startswith("[ACDE12]")


class TestFindingCitationsInTheSource:

    def test_alphabetic_anchors_are_found(self):
        html = '<section id="S1"><h2>1 Intro</h2>' \
               '<a href="#bib.bibx1">x</a><a href="#bib.bibx2">y</a></section>'
        assert lr.source_citation_sequence(html) == {"1": [1, 2]}

    def test_numeric_anchors_still_are(self):
        html = '<section id="S1"><h2>1 Intro</h2>' \
               '<a href="#bib.bib3">x</a></section>'
        assert lr.source_citation_sequence(html) == {"1": [3]}


class TestMatchingLabelsInTheBody:
    """The body cites `[ACDE12]`; the match has to be exact but tolerant of the
    spacing the translation introduces."""

    def test_a_label_is_found_where_the_body_cites_it(self):
        pat = lr.label_pattern(["ACDE12"])
        assert [m.group(1) for m in pat.finditer("이는 [ACDE12] 에서 보입니다")] \
            == ["ACDE12"]

    def test_spacing_inside_the_brackets_is_tolerated(self):
        pat = lr.label_pattern(["VSP + 17"])
        found = [m.group(1) for m in pat.finditer("[VSP+17] 와 [VSP + 17] 참고")]
        assert len(found) == 2

    def test_doubled_brackets_still_match(self):
        # The assembler writes `[ [RNSS18] ]` often enough to matter.
        pat = lr.label_pattern(["RNSS18"])
        assert pat.search("[ [RNSS18] ]") is not None

    def test_an_unknown_label_is_not_matched(self):
        pat = lr.label_pattern(["ACDE12"])
        assert pat.search("이는 [ZZZZ99] 입니다") is None

    def test_a_label_is_not_matched_inside_a_longer_one(self):
        pat = lr.label_pattern(["VSP17"])
        assert pat.search("[XVSP17]") is None

    def test_no_labels_makes_a_pattern_that_matches_nothing(self):
        assert lr.label_pattern([]).search("[ACDE12]") is None


NESTED = '''
<ul class="ltx_biblist">
<li id="bib.bibx3" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[DGV<sup class="ltx_sup"><span class="ltx_text">+</span></sup>18]</span>
<span class="ltx_bibblock">M. First, S. Second, et al.</span></li>
<li id="bib.bibx4" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[Fou]</span>
<span class="ltx_bibblock">The Common Crawl Foundation.</span></li>
<li id="bib.bibx5" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[RRBS19a]</span>
<span class="ltx_bibblock">J. Third. A disambiguated year.</span></li>
</ul>
'''


class TestLabelsWithMarkupInside:
    """`[DGV+18]` is written with the `+` in a `<sup>`, so the label's own span
    contains another one. Stopping at the first closing tag truncates the label
    and leaves the rest of it stranded in the entry text."""

    def test_a_label_wrapping_a_superscript_is_read_whole(self):
        labels = [e["label"] for e in lr.parse_bibliography(NESTED)]
        assert labels[0] == "DGV+18"

    def test_the_stranded_remainder_does_not_stay_in_the_text(self):
        first = lr.parse_bibliography(NESTED)[0]
        assert not first["text"].lstrip().startswith("18]")

    def test_a_label_with_no_year_is_read(self):
        assert lr.parse_bibliography(NESTED)[1]["label"] == "Fou"

    def test_a_disambiguating_letter_is_kept(self):
        assert lr.parse_bibliography(NESTED)[2]["label"] == "RRBS19a"


class TestRelinkingAfterTheListIsRebuilt:
    """`--force` writes a new reference list, so every existing citation link
    points at a block that is about to be archived. A linker that skips spans
    which already carry a link leaves the whole body pointing at nothing."""

    def test_a_marker_already_linked_is_offered_for_relinking(self):
        span = {"type": "text", "text": {"content": "[ACDE12]",
                                         "link": {"url": "https://old"}},
                "plain_text": "[ACDE12]"}
        assert lr.is_stale_marker(span, {"ACDE12"})

    def test_a_link_on_ordinary_prose_is_left_alone(self):
        span = {"type": "text", "text": {"content": "see the appendix",
                                         "link": {"url": "https://x"}},
                "plain_text": "see the appendix"}
        assert not lr.is_stale_marker(span, {"ACDE12"})

    def test_a_marker_for_an_unknown_label_is_left_alone(self):
        span = {"type": "text", "text": {"content": "[ZZZZ99]",
                                         "link": {"url": "https://old"}},
                "plain_text": "[ZZZZ99]"}
        assert not lr.is_stale_marker(span, {"ACDE12"})

    def test_an_unlinked_span_is_not_a_stale_marker(self):
        span = {"type": "text", "text": {"content": "[ACDE12]"},
                "plain_text": "[ACDE12]"}
        assert not lr.is_stale_marker(span, {"ACDE12"})


class TestFindingTheEntriesAlreadyOnThePage:
    """Relinking keeps the list that is there and re-points the body at it, so
    it has to recognise which block holds which entry — by the marker the block
    opens with, in whichever style the paper uses."""

    ENTRIES = [{"num": 1, "label": "ACDE12", "text": "A. Author."},
               {"num": 2, "label": "VSP + 17", "text": "D. Fourth."},
               {"num": 3, "label": "3", "text": "C. Third."}]

    def test_an_alphabetic_marker_finds_its_entry(self):
        found = lr.ref_ids_from_texts({"b1": "[ACDE12] A. Author."}, self.ENTRIES)
        assert found == {1: "b1"}

    def test_spacing_in_the_marker_does_not_matter(self):
        found = lr.ref_ids_from_texts({"b2": "[VSP+17] D. Fourth."}, self.ENTRIES)
        assert found == {2: "b2"}

    def test_a_numeric_marker_still_finds_its_entry(self):
        found = lr.ref_ids_from_texts({"b3": "[3] C. Third."}, self.ENTRIES)
        assert found == {3: "b3"}

    def test_a_block_that_is_not_an_entry_is_ignored(self):
        assert lr.ref_ids_from_texts({"b9": "본문 문단입니다"}, self.ENTRIES) == {}

    def test_an_unknown_marker_is_ignored(self):
        assert lr.ref_ids_from_texts({"b9": "[ZZ99] Someone."}, self.ENTRIES) == {}
