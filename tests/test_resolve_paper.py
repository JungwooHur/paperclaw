"""Naming a paper by its acronym or model number.

A reader writes "XYZ논문에서 …" and means exactly one page. The backstop that
files a Q&A retroactively learned to read that; the resolver carried its own
tiering and never did, so the same message resolved in one path and asked
"which paper?" in the other. Both now ask one function.

The evidence is deliberately narrow: the name has to be the token the title
opens with, it has to stand next to the word 논문/paper, and it decides only
when a single page owns it. Everything below is that boundary.
"""
import auto_save_qa as aq
import resolve_paper


def paper(page_id, title):
    """A paper page as `load_paper_pages` hands it over."""
    return {"id": page_id, "title": title,
            "keywords": aq.extract_title_keywords(title)}


ACRONYM = paper("page-zqnk", "ZQNK: Quivering Widgets For Imaginary Robots")
MODEL_NUMBER = paper("page-qx", "qx2.5: An Invented Model Of Nothing")
UNRELATED = paper("page-other", "Blimp Wrangling With Very Fast Widgets")


def has_text(*page_ids):
    """A stand-in for the page-body check, with no page fetched."""
    return lambda page_id: page_id in page_ids


def every_page(_page_id):
    """Every page carries its translation."""
    return True


class TestNamingEvidence:
    def test_an_acronym_beside_a_korean_particle_names_its_paper(self):
        named = aq.named_paper("ZQNK논문에서 3.2절이 무슨 뜻이야?",
                               [UNRELATED, ACRONYM], every_page)
        assert named["id"] == ACRONYM["id"]

    def test_the_english_word_paper_names_it_too(self):
        named = aq.named_paper("what does the ZQNK paper mean by that?",
                               [UNRELATED, ACRONYM], every_page)
        assert named["id"] == ACRONYM["id"]

    def test_a_lowercase_spelling_of_an_acronym_is_not_the_name(self):
        assert aq.named_paper("zqnk 논문에서 뭐라고 했지?",
                              [UNRELATED, ACRONYM], every_page) is None

    def test_a_model_number_is_named_whatever_its_case(self):
        for text in ("qx2.5 논문 요약해줘", "QX2.5 논문 요약해줘"):
            named = aq.named_paper(text, [UNRELATED, MODEL_NUMBER], every_page)
            assert named["id"] == MODEL_NUMBER["id"]

    def test_a_title_that_merely_contains_the_word_is_not_named_by_it(self):
        assert aq.named_paper("Fast 논문에서 뭐라고 했지?",
                              [UNRELATED], every_page) is None

    def test_a_name_with_no_paper_word_beside_it_names_nothing(self):
        assert aq.named_paper("ZQNK를 어떻게 구현하면 돼?",
                              [UNRELATED, ACRONYM], every_page) is None

    def test_a_message_naming_no_paper_names_nothing(self):
        assert aq.named_paper("그럼 online이야?",
                              [UNRELATED, ACRONYM], every_page) is None


class TestSharedNames:
    def test_a_name_two_pages_with_content_share_decides_nothing(self):
        rival = paper("page-rival", "ZQNK: A Different Paper Entirely")
        assert aq.named_paper("ZQNK논문에서 3.2절이 무슨 뜻이야?",
                              [ACRONYM, rival], every_page) is None

    def test_the_owner_that_carries_the_paper_wins_over_a_stub(self):
        stub = paper("page-stub", "ZQNK: A Different Paper Entirely")
        named = aq.named_paper("ZQNK논문에서 3.2절이 무슨 뜻이야?",
                               [stub, ACRONYM], has_text(ACRONYM["id"]))
        assert named["id"] == ACRONYM["id"]

    def test_two_copies_of_one_page_are_not_an_ambiguity(self):
        copy = paper("page-copy", ACRONYM["title"])
        named = aq.named_paper("ZQNK논문에서 3.2절이 무슨 뜻이야?",
                               [ACRONYM, copy], has_text())
        assert named["id"] in (ACRONYM["id"], copy["id"])

    def test_two_names_of_equal_length_in_one_message_decide_nothing(self):
        other = paper("page-wplm", "WPLM: Yet Another Invented Widget")
        assert aq.named_paper("ZQNK논문이랑 WPLM논문 중에 뭐가 낫나?",
                              [ACRONYM, other], every_page) is None

    def test_the_longer_of_two_names_in_one_message_is_the_specific_one(self):
        named = aq.named_paper("ZQNK논문 말고 qx2.5 논문 얘기야",
                               [ACRONYM, MODEL_NUMBER], every_page)
        assert named["id"] == MODEL_NUMBER["id"]


class TestOneDefinition:
    def test_the_resolver_asks_the_backstop_rather_than_its_own_copy(self):
        assert resolve_paper.aq.named_paper is aq.named_paper
        assert not hasattr(resolve_paper, "named_paper")
        assert not hasattr(resolve_paper, "title_acronym")
