"""The guard that makes a second reference list impossible.

The injector refused to append only when it RECOGNISED the list it had written
— heading text plus a body that looks like entries. Recognition is the fragile
half: one unfamiliar label style and the tool cannot see its own work, so it
appends another copy, every five minutes, forever. One page reached thirty-seven
copies of its own bibliography.

The heading is not fragile. This tool writes it, its text is fixed, and nothing
else on a paper page produces one. So the decision "may I append a reference
list?" is made on the heading alone, and stays correct however the entries are
labelled.
"""
import link_references as lr


def heading(text, level=1):
    kind = "heading_%d" % level
    return {"id": "h-" + text[:6], "type": kind,
            kind: {"rich_text": [{"plain_text": text}]}}


def para(text):
    return {"id": "p-" + text[:6], "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


class TestCountingWhatIsAlreadyThere:

    def test_a_page_with_no_list_has_none(self):
        assert lr.reference_headings([para("본문"), heading("1 Introduction")]) == []

    def test_our_heading_is_found_whatever_the_entries_look_like(self):
        blocks = [heading("References"), para("[완전히 낯선 라벨] 무엇이든")]
        assert len(lr.reference_headings(blocks)) == 1

    def test_every_copy_is_counted(self):
        blocks = [heading("References"), para("a"),
                  heading("References"), para("b"),
                  heading("References"), para("c")]
        assert len(lr.reference_headings(blocks)) == 3

    def test_the_match_ignores_case_and_spacing(self):
        assert len(lr.reference_headings([heading("  references  ")])) == 1

    def test_a_section_merely_mentioning_references_is_not_one(self):
        assert lr.reference_headings([heading("7 References and Notes")]) == []


class TestRefusingToAppendASecond:

    def test_a_page_with_none_may_be_given_one(self):
        assert lr.may_inject_references([para("본문")]) is True

    def test_a_page_that_already_has_one_may_not(self):
        blocks = [heading("References"), para("[unrecognisable] entry")]
        assert lr.may_inject_references(blocks) is False

    def test_recognition_is_not_consulted(self):
        # The whole point: the body may be in any style, or unparseable, and the
        # answer must still be no.
        blocks = [heading("References"), para("완전히 다른 무엇")]
        assert lr.may_inject_references(blocks) is False
