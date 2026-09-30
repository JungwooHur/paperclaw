"""Which PDF a paper outside arXiv is rendered from.

A journal paper was sent WITH its PDF attached, and still ended up with no
figures: every figure path was keyed on an arXiv id, the attachment is saved
under a message id that nothing ties to the page, and so the healer had no
source. The reader's own PDF is the best source there is — it is the version
they are reading — so it comes first, found by the title on its first page.
The publisher's PDF is the fallback.

A second trap sat beside it: the arXiv id was "resolved from the title" even
when the page already carried a publisher URL, so a journal paper with a
preprint would have had that URL overwritten and been illustrated from a
different version of itself.
"""
import fitz

import extract_paper_figures as ef
import extract_pdf_media as pm

TITLE = "A Study of Structured Reasoning under Tree Search"


def attachment(directory, name, first_page_text):
    doc = fitz.open()
    doc.new_page().insert_text((40, 60), first_page_text, fontsize=9)
    doc.new_page().insert_text((40, 60), "Body text.", fontsize=9)
    path = directory / name
    doc.save(path)
    return str(path)


class TestTheReadersOwnPdf:

    def test_it_is_found_by_its_title(self, tmp_path):
        attachment(tmp_path, "a.pdf", "Some other paper entirely")
        mine = attachment(tmp_path, "b.pdf", "Article\nA Study of Structured Reasoning\nunder Tree Search")
        assert pm.attachment_for(TITLE, str(tmp_path)) == mine

    def test_case_and_punctuation_do_not_matter(self, tmp_path):
        mine = attachment(tmp_path, "b.pdf", "A STUDY OF STRUCTURED-REASONING, UNDER TREE SEARCH")
        assert pm.attachment_for(TITLE, str(tmp_path)) == mine

    def test_nothing_is_found_when_no_pdf_carries_the_title(self, tmp_path):
        attachment(tmp_path, "a.pdf", "Some other paper entirely")
        assert pm.attachment_for(TITLE, str(tmp_path)) is None

    def test_a_short_title_is_not_trusted(self, tmp_path):
        attachment(tmp_path, "a.pdf", "Search")
        assert pm.attachment_for("Search", str(tmp_path)) is None

    def test_a_file_that_is_not_a_pdf_is_skipped(self, tmp_path):
        (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
        mine = attachment(tmp_path, "b.pdf", TITLE)
        assert pm.attachment_for(TITLE, str(tmp_path)) == mine

    def test_a_missing_directory_finds_nothing(self, tmp_path):
        assert pm.attachment_for(TITLE, str(tmp_path / "absent")) is None


class TestThePublishersPdf:

    def test_a_nature_article_has_one(self):
        assert (pm.journal_pdf_url("https://www.nature.com/articles/s00000-000-00000-x")
                == "https://www.nature.com/articles/s00000-000-00000-x.pdf")

    def test_so_does_its_doi(self):
        assert (pm.journal_pdf_url("https://doi.org/10.1038/s00000-000-00000-x")
                == "https://www.nature.com/articles/s00000-000-00000-x.pdf")

    def test_another_publisher_has_none_we_know(self):
        assert pm.journal_pdf_url("https://example.org/paper/1") is None


class TestWhichComesFirst:

    def test_the_readers_pdf_beats_the_publishers(self, tmp_path):
        mine = attachment(tmp_path, "b.pdf", TITLE)
        assert pm.pdf_source_for(
            TITLE, "https://www.nature.com/articles/s00000-000-00000-x",
            str(tmp_path)) == mine

    def test_the_publishers_pdf_when_there_is_no_attachment(self, tmp_path):
        assert pm.pdf_source_for(
            TITLE, "https://www.nature.com/articles/s00000-000-00000-x",
            str(tmp_path)).endswith(".pdf")

    def test_nothing_when_neither_exists(self, tmp_path):
        assert pm.pdf_source_for(TITLE, "https://example.org/p", str(tmp_path)) is None


class TestAPublisherUrlIsNotReplaced:

    def test_the_title_is_not_resolved_when_the_page_has_a_url(self, monkeypatch):
        page = {"properties": {
            "Paper Pages": {"type": "title", "title": [{"plain_text": TITLE}]},
            "Paper URL": {"type": "url",
                          "url": "https://www.nature.com/articles/s00000-000-00000-x"}}}
        calls = []
        monkeypatch.setattr("translate_fulltext.notion",
                            lambda method, path, body=None, **kw: calls.append(method) or page)
        import resolve_arxiv
        monkeypatch.setattr(resolve_arxiv, "resolve",
                            lambda title: {"arxiv_id": "preprint-id",
                                           "title": TITLE, "url": "https://example.org/preprint"})
        assert ef.ensure_arxiv_id("page", apply=True) is None
        assert "PATCH" not in calls
