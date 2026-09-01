"""Splitting a heading that swallowed its own paragraph.

The assembler sometimes emits a run-in subsection title and its whole body as
ONE heading block. Two things go wrong at once: the page renders a wall of large
bold text, and — because the body is inside the heading — the heading text no
longer matches the same section's other copy, so the duplicate healer cannot see
that the section was uploaded twice.
"""
import heading_bloat as hb


def heading(text, level=3):
    kind = "heading_%d" % level
    return {"id": "b1", "type": kind,
            kind: {"rich_text": [{"type": "text",
                                  "text": {"content": text},
                                  "plain_text": text}]}}


def spans(*items):
    """Rich text from (content, annotations) pairs, or ('=', expression)."""
    out = []
    for kind, value in items:
        if kind == "=":
            out.append({"type": "equation", "equation": {"expression": value},
                        "plain_text": value})
        else:
            out.append({"type": "text", "text": {"content": value},
                        "plain_text": value,
                        "annotations": {"bold": kind == "b"}})
    return out


LEAD = "Smooth power laws:"
BODY = ("Performance는 다른 두 요인에 의해 병목이 발생하지 않을 때 세 가지 scale "
        "factors N, D, C 각각에 대해 power-law 관계를 가지며, 이는 6자리 이상의 "
        "크기에 걸쳐 뚜렷한 곡률 없이 이어집니다. 우리는 이 경향이 계속될지에 대한 "
        "징후를 아직 관측하지 못했습니다.")


class TestWhatCountsAsBloat:

    def test_a_heading_carrying_its_body_is_split(self):
        assert hb.split_block(heading(LEAD + " " + BODY)) is not None

    def test_an_ordinary_heading_is_left_alone(self):
        assert hb.split_block(heading(LEAD)) is None

    def test_a_long_heading_with_no_run_in_colon_is_left_alone(self):
        # Refusing is the safe answer: a wrong cut mangles the reader's text,
        # and the audit still reports the block.
        assert hb.split_block(heading("아주 긴 제목이지만 콜론이 없습니다 " * 8)) is None

    def test_a_colon_far_into_the_text_is_not_a_run_in_title(self):
        text = "본문이 한참 이어지다가 " * 12 + "여기서: 콜론이 나옵니다"
        assert hb.split_block(heading(text)) is None

    def test_a_paragraph_is_never_touched(self):
        block = heading(LEAD + " " + BODY)
        block["type"] = "paragraph"
        block["paragraph"] = block.pop("heading_3")
        assert hb.split_block(block) is None


class TestWhatTheSplitProduces:

    def test_the_title_keeps_its_colon_and_becomes_the_heading(self):
        head, body = hb.split_block(heading(LEAD + " " + BODY))
        assert head["type"] == "heading_3"
        assert hb.text_of(head) == LEAD

    def test_the_body_becomes_a_paragraph(self):
        head, body = hb.split_block(heading(LEAD + " " + BODY))
        assert body["type"] == "paragraph"
        assert hb.text_of(body) == BODY

    def test_the_heading_keeps_its_level(self):
        head, _ = hb.split_block(heading(LEAD + " " + BODY, level=2))
        assert head["type"] == "heading_2"

    def test_nothing_of_the_text_is_lost(self):
        block = heading(LEAD + " " + BODY)
        head, body = hb.split_block(block)
        assert hb.text_of(head) + " " + hb.text_of(body) == hb.text_of(block)


class TestFormattingSurvives:

    def test_an_equation_in_the_body_stays_an_equation(self):
        block = heading(LEAD)
        block["heading_3"]["rich_text"] = spans(
            ("t", LEAD + " 손실은 "), ("=", "L(N)"), ("t", " 로 " + BODY))
        head, body = hb.split_block(block)
        kinds = [s["type"] for s in body["paragraph"]["rich_text"]]
        assert "equation" in kinds

    def test_bold_in_the_body_stays_bold(self):
        block = heading(LEAD)
        block["heading_3"]["rich_text"] = spans(
            ("t", LEAD + " "), ("b", "중요한 부분"), ("t", " " + BODY))
        _, body = hb.split_block(block)
        assert any(s.get("annotations", {}).get("bold")
                   for s in body["paragraph"]["rich_text"])

    def test_a_cut_landing_inside_an_equation_is_refused(self):
        # An equation span has no character offsets to cut at, so splitting one
        # would have to invent LaTeX. Leave the block whole instead.
        block = heading(LEAD)
        block["heading_3"]["rich_text"] = spans(
            ("t", "Smooth power laws"), ("=", ": L(N) = a" + "x" * 200))
        assert hb.split_block(block) is None


class TestOneDefinitionOfBloat:
    """The audit reports what `is_bloated` says and the repair acts on the same
    call, so the two cannot drift into flagging one set and fixing another."""

    def test_a_heading_over_the_limit_is_bloated(self):
        assert hb.is_bloated(heading("가" * (hb.MAX_HEADING_CHARS + 1)))

    def test_a_heading_at_the_limit_is_not(self):
        assert not hb.is_bloated(heading("가" * hb.MAX_HEADING_CHARS))

    def test_a_long_paragraph_is_not_a_bloated_heading(self):
        block = heading("가" * 500)
        block["type"] = "paragraph"
        block["paragraph"] = block.pop("heading_3")
        assert not hb.is_bloated(block)

    def test_nothing_the_audit_ignores_is_ever_split(self):
        for text in (LEAD, "가" * hb.MAX_HEADING_CHARS, LEAD + " " + BODY):
            block = heading(text)
            if not hb.is_bloated(block):
                assert hb.split_block(block) is None

    def test_the_audit_flags_a_block_through_this_predicate(self):
        import verify_sections
        assert verify_sections.heading_bloat.is_bloated is hb.is_bloated
