"""Cleaning a NotebookLM answer before it is turned into blocks.

The cleaning step used to be a snippet pasted into the workflow document, and it
deleted every `$` and every `**` along with the CLI's status lines. `$` is how
the converter finds maths and `**` is how NotebookLM marks a subsection title,
so a page assembled through it came out with its formulas as bare LaTeX and a
whole Methods section's titles flattened into ordinary paragraphs. Only the
CLI's own furniture is noise; everything else is content.
"""
import save_qa_callout as sq

ANSWER = ("Continuing conversation <id>...\n"
          "Answer: **Related work (관련 연구)**\n\n"
          "우리는 $s_t$에서 시작합니다.\n")


class TestOnlyTheFurnitureGoes:

    def test_the_status_line_is_removed(self):
        assert "Continuing conversation" not in sq.strip_cli_furniture(ANSWER)

    def test_the_answer_label_is_removed(self):
        assert not sq.strip_cli_furniture(ANSWER).lstrip().startswith("Answer:")

    def test_maths_delimiters_are_kept(self):
        assert "$s_t$" in sq.strip_cli_furniture(ANSWER)

    def test_a_bold_title_is_kept(self):
        assert "**Related work (관련 연구)**" in sq.strip_cli_furniture(ANSWER)

    def test_a_clean_answer_is_unchanged(self):
        clean = "우리는 $s_t$에서 시작합니다.\n\n**Training (학습)**"
        assert sq.strip_cli_furniture(clean) == clean


class TestTheTitleThenBecomesAHeading:

    def test_a_bold_title_line_is_promotable(self):
        import paragraph_headings as ph
        blocks = sq.build_answer_blocks(sq.strip_cli_furniture(ANSWER))
        assert ph.heading_level(blocks[0]) == 2
