"""The URL cleaner leaves the injected reference list alone.

`clean_source_urls` removes arXiv links that leaked into translated BODY text.
It scanned the whole page, so it also reached the English reference list the
healer injects at the end — whose entries carry their own arXiv and DOI links
on purpose — and stripped them: "Preprint at https://… (2024)" became
"Preprint at (2024)". Every other healer already stops at that list through
`reference_section`; this one did not.
"""
import clean_source_urls as cu


def block(kind, text, bid):
    return {"id": bid, "type": kind,
            kind: {"rich_text": [{"type": "text", "plain_text": text,
                                  "text": {"content": text}}]}}


BODY_LEAK = "결과는 Table 2 https://arxiv.org/html/xxxx#S5.T2 에 있습니다."
ENTRY = "[8] Author, A. et al. A system card. Preprint at https://arxiv.org/abs/xxxx (2024)."


def page():
    return [block("paragraph", BODY_LEAK, "body"),
            block("heading_1", "References", "refs"),
            block("paragraph", ENTRY, "entry8"),
            block("paragraph", "[9] Author, B. A book (Publisher, 2020).", "entry9"),
            block("paragraph", "[10] Author, C. A paper. J. Venue 1, 2 (2021).", "entry10")]


class TestTheReferenceListIsLeftAlone:

    def test_an_entry_keeps_its_link(self):
        edited = {b["id"] for b, _ in cu.plan_edits(page())}
        assert "entry8" not in edited

    def test_the_body_is_still_cleaned(self):
        edited = dict((b["id"], new) for b, new in cu.plan_edits(page()))
        assert "body" in edited
        text = "".join(r["text"]["content"] for r in edited["body"])
        assert "arxiv.org" not in text and "Table 2" in text

    def test_a_page_without_a_reference_list_is_cleaned_throughout(self):
        blocks = [block("paragraph", BODY_LEAK, "body")]
        assert [b["id"] for b, _ in cu.plan_edits(blocks)] == ["body"]
