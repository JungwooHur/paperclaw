"""Where the injected reference list starts, and what counts as body.

Six modules ask this one question before they touch a page: the structural
audit, the maths healer, back-matter stripping and the three float injectors.
Getting it wrong does not fail loudly — it silently sweeps an appendix into
"everything after the references", or drops a figure below the bibliography.
"""
import reference_section as rs


def para(text):
    return {"type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def heading(text):
    return {"type": "heading_1",
            "heading_1": {"rich_text": [{"plain_text": text}]}}


def entries(count, start=1):
    return [para(f"[{n}] Author, A. A paper title. 2024.")
            for n in range(start, start + count)]


class TestLooksLikeList:
    def test_recognises_a_run_of_english_numbered_entries(self):
        assert rs.looks_like_list(entries(3)) is True

    def test_rejects_a_translated_bibliography(self):
        korean = [para(f"[{n}] 저자, 논문 제목, 2024년.") for n in (1, 2, 3)]
        assert rs.looks_like_list(korean) is False

    def test_rejects_a_run_too_short_to_be_a_list(self):
        assert rs.looks_like_list(entries(2)) is False

    def test_tolerates_one_stray_paragraph_among_entries(self):
        run = entries(9) + [para("Note added by hand.")]
        assert rs.looks_like_list(run) is True


class TestBodyBlocks:
    def test_excludes_the_reference_list_and_its_heading(self):
        body = [heading("1 Introduction"), para("본문 한 문단.")]
        blocks = body + [heading("References")] + entries(4)
        assert rs.body_blocks(blocks) == body

    def test_returns_everything_when_no_list_was_injected(self):
        blocks = [heading("1 Introduction"), para("본문 한 문단.")]
        assert rs.body_blocks(blocks) == blocks

    def test_a_heading_before_the_bibliography_is_not_its_start(self):
        """An appendix that merely precedes the references is still body.

        Collecting every paragraph to the end of the page made any earlier
        heading match, so acting on it swept that appendix away.
        """
        appendix = [heading("Appendix G"), para("부록 본문 한 문단.")]
        blocks = [heading("1 Introduction"), para("본문.")] + appendix
        blocks += [heading("References")] + entries(5)
        assert rs.body_blocks(blocks)[-2:] == appendix


class TestBodyEndAnchor:
    def test_points_at_the_last_block_before_the_reference_list(self):
        last = para("본문의 마지막 문단.")
        last["id"] = "last-body-block"
        blocks = [heading("1 Introduction"), last, heading("References")]
        blocks += entries(3)
        assert rs.body_end_anchor(blocks) == "last-body-block"

    def test_is_none_when_the_page_has_no_reference_list(self):
        blocks = [heading("1 Introduction"), para("본문.")]
        assert rs.body_end_anchor(blocks) is None


class TestAnAlphabeticBibliography:
    """Not every paper numbers its references.

    An author-initials style writes `[ACDE12]` where a numeric one writes `[1]`.
    The boundary has to recognise both, because everything downstream depends on
    it: a bibliography the boundary cannot see is body, and back-matter
    stripping cuts from the `References` heading to the end of the page.
    """

    def test_an_alphabetic_entry_is_an_entry(self):
        assert rs.is_entry(para("[ACDE12] A. Author. A title."))

    def test_a_label_with_spaces_is_too(self):
        assert rs.is_entry(para("[VSP + 17] D. Fourth et al."))

    def test_a_numeric_entry_still_is(self):
        assert rs.is_entry(para("[1] A. Author. A title."))

    def test_a_translated_entry_is_still_rejected(self):
        # A Korean bibliography is the thing the healers exist to remove; only
        # the injected English one is protected.
        assert not rs.is_entry(
            para("[ACDE12] 저자. 어떤 제목. 학회, 2012."))

    def test_a_sentence_opening_with_a_bracket_is_not_an_entry(self):
        assert not rs.is_entry(
            para("[see below] for the derivation of this bound"))

    def test_a_list_of_alphabetic_entries_looks_like_one(self):
        items = [para("[ACDE12] A. Author. A title."),
                 para("[BXY13] B. Second. Another."),
                 para("[CZW14] C. Third. A third.")]
        assert rs.looks_like_list(items)

    def test_a_label_with_a_disambiguating_letter_is_an_entry(self):
        assert rs.is_entry(para("[RRBS19a] J. Third. A paper. 2019."))

    def test_a_label_with_no_year_is_an_entry(self):
        assert rs.is_entry(para("[Fou] The Common Crawl Foundation. Common crawl."))

    def test_a_superscript_style_label_is_an_entry(self):
        assert rs.is_entry(para("[DGV+18] M. First, S. Second, et al. A title."))

    def test_a_bracketed_english_aside_is_still_not_an_entry(self):
        assert not rs.is_entry(para("[see below] for the derivation of this bound"))


class TestAnAuthorYearBibliography:
    """A third label style, and the one that made a page run away.

    Some papers label entries `[Agrawal et al. (2016)]`. The boundary did not
    recognise those, so the tool could not see the list it had just written —
    and the five-minute healer appended a fresh copy on every cycle. One page
    reached thirty-seven copies of its own bibliography and nothing else.
    """

    def test_an_author_year_entry_is_an_entry(self):
        assert rs.is_entry(para("[Agrawal et al. (2016)] Pulkit Agrawal, "
                                "Ashvin V Nair. Learning to poke. 2016."))

    def test_an_ampersand_between_authors_is_fine(self):
        assert rs.is_entry(para("[Allgöwer & Zheng (2012)] Frank Allgöwer and "
                                "Alex Zheng. Nonlinear control. 2012."))

    def test_a_non_ascii_name_is_fine(self):
        assert rs.is_entry(para("[Bergström (2019)] Anna Bergström. A title."))

    def test_the_earlier_styles_still_work(self):
        assert rs.is_entry(para("[7] A. Author. A title. 2020."))
        assert rs.is_entry(para("[ACDE12] A. Author. A title. 2012."))
        assert rs.is_entry(para("[Fou] The Common Crawl Foundation."))

    def test_a_translated_entry_is_still_rejected(self):
        assert not rs.is_entry(para("[Agrawal et al. (2016)] 저자. 어떤 제목."))

    def test_a_bracketed_aside_without_a_number_is_not_an_entry(self):
        assert not rs.is_entry(para("[see the appendix] for the derivation"))

    def test_a_long_bracketed_phrase_is_not_an_entry(self):
        # A label is short. A sentence that opens with a long bracketed clause
        # containing a year is prose, and treating it as an entry would move the
        # boundary into the body.
        assert not rs.is_entry(para(
            "[as the authors of the 2016 study on poking eventually conceded] "
            "the result did not replicate"))

    def test_a_list_of_them_is_a_reference_list(self):
        items = [para("[Agrawal et al. (2016)] P. Agrawal. A title."),
                 para("[Bakhtin et al. (2019)] A. Bakhtin. Another."),
                 para("[Watters et al. (2017)] N. Watters. A third.")]
        assert rs.looks_like_list(items)
