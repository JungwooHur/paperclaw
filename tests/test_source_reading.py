"""How the audit reads a `--source`, and where it finds a page's headings in it."""
import email.message
import io
import urllib.error

import fitz
import pytest

import verify_sections as vs

SENTENCE = ("The method is evaluated on a range of tasks and compared with the "
            "baselines described above. ")


def prose(chars):
    """Lines of about 75 characters, about `chars` in all."""
    text = (SENTENCE * (chars // len(SENTENCE) + 1))[:chars]
    return "\n".join(text[i:i + 75] for i in range(0, len(text), 75))


def pdf_bytes(text):
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(60, 60, 540, 780), text, fontsize=10)
    return doc.tobytes()


class Response(io.BytesIO):
    """What `urllib.request.urlopen` returns: a body and its headers."""

    def __init__(self, body, content_type):
        super().__init__(body)
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type


def serve(monkeypatch, body=None, content_type=None, error=None):
    def urlopen(req, timeout=None):
        if error:
            raise error
        return Response(body, content_type)
    monkeypatch.setattr(vs.urllib.request, "urlopen", urlopen)


URL = "https://example.org/pdf/paper"
PDF_TEXT = "1 Introduction\nA sentence that the PDF prints in its body."


class TestASourceIsReadByWhatItIs:

    def test_a_pdf_served_without_an_extension_is_read_as_a_pdf(self, monkeypatch):
        serve(monkeypatch, pdf_bytes(PDF_TEXT), "application/pdf")
        text = " ".join(vs.load_source_text(URL, None).split())
        assert "A sentence that the PDF prints in its body." in text
        assert "%PDF" not in text

    def test_a_pdf_is_known_by_its_bytes_whatever_its_content_type(self, monkeypatch):
        serve(monkeypatch, pdf_bytes(PDF_TEXT), "application/octet-stream")
        assert "prints in its body" in vs.load_source_text(URL, None)

    @pytest.mark.parametrize("content_type", ["text/html; charset=utf-8",
                                              "application/xhtml+xml"])
    def test_an_html_page_is_read_as_html(self, monkeypatch, content_type):
        serve(monkeypatch, b"<html><body><p>Body <b>text</b> here.</p></body></html>",
              content_type)
        text = " ".join(vs.load_source_text("https://example.org/paper", None).split())
        assert "Body text here." in text
        assert "<p>" not in text

    def test_a_body_neither_pdf_nor_html_raises(self, monkeypatch):
        serve(monkeypatch, b"%!PS-Adobe-3.0\n", "application/postscript")
        with pytest.raises(ValueError, match="example.org/pdf/paper.*postscript"):
            vs.load_source_text(URL, None)

    def test_a_body_served_as_a_pdf_that_is_not_one_raises(self, monkeypatch):
        serve(monkeypatch, b"<html><body>Not Found</body></html>", "application/pdf")
        with pytest.raises(ValueError, match="example.org/pdf/paper.*application/pdf"):
            vs.load_source_text(URL, None)

    def test_a_failed_fetch_raises_naming_the_url(self, monkeypatch):
        serve(monkeypatch, error=urllib.error.HTTPError(URL, 404, "Not Found", None, None))
        with pytest.raises(OSError, match="example.org/pdf/paper.*404"):
            vs.load_source_text(URL, None)

    def test_a_local_pdf_is_read_as_a_pdf(self, tmp_path):
        path = tmp_path / "paper"
        path.write_bytes(pdf_bytes(PDF_TEXT))
        assert "prints in its body" in vs.load_source_text(str(path), None)

    def test_a_local_path_that_does_not_exist_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="missing.pdf"):
            vs.load_source_text(str(tmp_path / "missing.pdf"), None)

    def test_the_audit_does_not_run_on_a_source_it_could_not_read(self, monkeypatch):
        serve(monkeypatch, error=urllib.error.URLError("timed out"))
        blocks = [{"id": "h", "type": "heading_2",
                   "heading_2": {"rich_text": [{"plain_text": "1 Introduction (서론)"}]}}]
        monkeypatch.setattr(vs, "fetch_blocks", lambda page: blocks)
        monkeypatch.setattr(vs, "page_title", lambda page: "a paper")
        monkeypatch.setattr("sys.argv", ["verify_sections.py", "--page", "p",
                                         "--source", URL, "--json"])
        with pytest.raises(OSError, match="timed out"):
            vs.main()


def spans(headings, source):
    ordered = [(vs.section_key(h), vs.english_title(h)) for h in headings]
    return vs.source_section_chars(source, ordered)


class TestALabelIsNotATitleWord:

    APPENDIX = ["D.1 Prompt Construction (프롬프트 구성)",
                "Appendix E. Few-shot Exemplars (부록 E. 퓨샷 예시)"]
    NUMBERED = ["1) Intuition (직관)", "2) Formulation (정식화)"]

    def test_the_title_drops_the_label(self):
        assert vs.english_title(self.APPENDIX[1]) == "Few-shot Exemplars"
        assert vs.english_title(self.NUMBERED[0]) == "Intuition"
        assert vs.english_title("Appendix B Pre-training Data") == "Pre-training Data"

    def test_the_key_is_unchanged(self):
        assert [vs.section_key(h) for h in self.APPENDIX + self.NUMBERED] == [
            "D.1", "E", "1", "2"]

    def test_a_capital_word_opening_an_unlabelled_heading_is_no_label(self):
        assert vs.section_key("A New Approach (새 접근)") is None
        assert vs.english_title("A New Approach (새 접근)") == "A New Approach"

    @pytest.mark.parametrize("printed", ["E Few-shot Exemplars",
                                         "Appendix E Few-shot Exemplars",
                                         "E. Few-shot Exemplars"])
    def test_an_appendix_heading_is_located(self, printed):
        first, second = prose(600), prose(900)
        got = spans(self.APPENDIX, "D.1 Prompt Construction\n" + first
                    + "\n" + printed + "\n" + second + "\n")
        assert got["D.1"] is not None and abs(got["D.1"] - len(first)) <= 40, got
        assert got["E"] is not None and abs(got["E"] - len(second)) <= 40, got

    def test_a_parenthesised_number_is_located(self):
        first, second = prose(700), prose(500)
        got = spans(self.NUMBERED, "1) Intuition: " + first
                    + "\n2) Formulation: " + second + "\n")
        assert got["1"] is not None and abs(got["1"] - len(first)) <= 40, got
        assert got["2"] is not None and abs(got["2"] - len(second)) <= 40, got


class TestALabelIsFoundOnlyAsALabel:

    HEADINGS = ["Appendix D. Setup (설정)", "Appendix E. Detailed Results (상세 결과)",
                "Appendix A. Network Architecture (네트워크 구조)", "Appendix F. Compute (계산)"]

    def test_neither_a_word_ending_in_the_letter_nor_an_article_is_a_label(self):
        d, e, a = prose(600), prose(700), prose(800)
        f = ("This part describes the detailed results of a run in which we used\n"
             "a network architecture of the same size. " + prose(500))
        got = spans(self.HEADINGS, "D Setup\n" + d + "\nE Detailed Results\n" + e
                    + "\nA Network Architecture\n" + a + "\nF Compute\n" + f + "\n")
        for key, text in (("D", d), ("E", e), ("A", a)):
            assert got[key] is not None and abs(got[key] - len(text)) <= 40, (key, got)


class TestAHeadingOpensItsLine:

    def test_a_later_heading_citing_a_section_does_not_move_it(self):
        headings = ["1 Introduction (서론)", "2 Widget Drift Model (위젯 드리프트 모델)",
                    "Appendix A. Proofs of the Bounds (부록 A. 경계의 증명)",
                    "Appendix B. Results of Section 2 (부록 B. 2절의 결과)"]
        data, proofs, later = prose(600), prose(700), prose(800)
        got = spans(headings, "1 Introduction\n" + prose(500) + "\n2 Widget Drift Model\n"
                    + data + "\nA Proofs of the Bounds\n" + proofs
                    + "\nB Results of Section 2 Widget Drift Model\n" + later + "\n")
        for key, text in (("2", data), ("A", proofs), ("B", later)):
            assert got[key] is not None and abs(got[key] - len(text)) <= 60, (key, got)

    def test_a_label_inside_a_number_or_a_word_is_no_heading(self):
        headings = ["1 Introduction (서론)", "2 Method Overview (방법 개요)",
                    "Appendix E. Detailed Results (부록 E. 상세 결과)",
                    "Appendix F. Compute (부록 F. 계산)"]
        method, e = prose(600), prose(700)
        f = prose(500) + "\n12 Method Overview\nBASELINE Detailed Results\n" + prose(200)
        got = spans(headings, "1 Introduction\n" + prose(500) + "\n2 Method Overview\n"
                    + method + "\nE Detailed Results\n" + e + "\nF Compute\n" + f + "\n")
        for key, text in (("2", method), ("E", e)):
            assert got[key] is not None and abs(got[key] - len(text)) <= 40, (key, got)

    def test_a_dash_before_an_appendix_letter_is_part_of_its_label(self):
        a, b = prose(600), prose(700)
        got = spans(["A. Training Details (학습 세부사항)", "B. Extra Results (추가 결과)"],
                    "-A Training Details\n" + a + "\n-B Extra Results\n" + b + "\n")
        for key, text in (("A", a), ("B", b)):
            assert got[key] is not None and abs(got[key] - len(text)) <= 40, (key, got)

    def test_a_title_formula_the_source_spells_in_tex_is_skipped(self):
        first, second = prose(600), prose(700)
        got = spans(["1 Sampling 𝐱ₜ₋₁⁽ⁱ⁾ Exactly (정확한 샘플링)", "2 Results (결과)"],
                    "1 Sampling \\mathbf{x}_{t-1}^{(i)} Exactly\n" + first
                    + "\n2 Results\n" + second + "\n")
        assert got["1"] is not None and abs(got["1"] - len(first)) <= 60, got
