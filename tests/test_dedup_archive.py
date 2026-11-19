"""What the duplicate repair archives.

A `column_list` holds `column`s, and each column the blocks set in it. The audit
reads those blocks in place (`verify_sections.reading_order`); the repair measures
and compares copies the same way, and archives whole top-level blocks. A section
and its subsections are copies at two levels, and the text of every section keeps
a copy the repair leaves standing.
"""
import pytest

import heal_verify as hv
import verify_sections as vs

SENTENCES = ["제안하는 방법은 정책을 두 단계로 학습한다.",
             "먼저 시뮬레이션에서 보상 함수를 정의하고, 이어서 실제 로봇에서 미세 조정한다."] * 4
BODY = " ".join(SENTENCES)
TRUNCATED = "제안하는 방법은 정책을 두 단계로 학습한다. 먼저 시뮬레이션에서"
OTHER = ("평가는 세 가지 환경에서 진행했으며 각 환경마다 다섯 번 반복하여 평균 "
         "성공률과 표준편차를 보고한다. ") * 4
DETAIL = ("관절 토크 센서의 잡음을 줄이기 위해 칼만 필터를 적용하고 그 이득을 "
          "실험적으로 조정하였다. ") * 4
SETUP = ("학습률은 코사인 스케줄로 감소시키고 배치 크기는 고정하였으며 초기화는 "
         "동일한 시드를 사용했다. ") * 4
CAPTION = "그림 3: 제안 방법의 전체 구조를 보여주는 도식으로 각 모듈의 입력과 출력을 표시한다."
FIGURE = "그림 1: 전체 구조."


def rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def block(kind, text, bid):
    return {"type": kind, "id": bid, kind: {"rich_text": rich(text)}}


def equation(expression, bid):
    return {"type": "equation", "id": bid, "equation": {"expression": expression}}


def image(bid, caption="", url=None):
    """An image block, hosted at a url of its own unless `url` names one."""
    hosted = {"url": f"https://files.example/{bid}.png?X-Amz-Signature={bid}",
              "expiry_time": "2026-01-01T00:00:00.000Z"}
    return {"type": "image", "id": bid,
            "image": {"caption": rich(caption) if caption else [],
                      **({"type": "external", "external": {"url": url}} if url
                         else {"type": "file", "file": hosted})}}


def table(bid, rows):
    """A `table` and the children `fetch_blocks` returns for it."""
    return ({"type": "table", "id": bid, "has_children": True,
             "table": {"table_width": 2, "has_column_header": True, "has_row_header": False}},
            {bid: [{"type": "table_row", "id": f"{bid}-{n}",
                    "table_row": {"cells": [rich(c) for c in row]}}
                   for n, row in enumerate(rows)]})


def sentence(n):
    """A sentence whose three words no other `sentence` has."""
    return " ".join("".join(chr(0xAC00 + 9 * (3 * n + w) + s) for s in range(3))
                    for w in range(3)) + " 값은 늘 같다."


TEN = [sentence(n) for n in range(10)]


def words(ns):
    """Words of three letters, one per n, no two alike and none a `sentence`'s."""
    return ["".join(chr(0xC000 + 3 * n + s) for s in range(3)) for n in ns]


def linked(kind, bid, caption, url):
    """An image at `url`, or a bookmark or embed of it, under `caption`."""
    if kind == "image":
        return image(bid, caption, url)
    return {"type": kind, "id": bid, kind: {"caption": rich(caption) if caption else [],
                                            "url": url}}


def front():
    """Eight paragraphs ahead of the first heading."""
    return [block("paragraph", f"앞 문단 {n}입니다.", f"f{n}") for n in range(8)]


def columns(bid, left, right):
    """A `column_list` and the children `fetch_blocks` returns for it."""
    return ({"type": "column_list", "id": bid, "has_children": True},
            {bid: [{"type": "column", "id": bid + "-l", "has_children": True},
                   {"type": "column", "id": bid + "-r", "has_children": True}],
             bid + "-l": left, bid + "-r": right})


def record(monkeypatch, children):
    """The requests `dedupe_duplicates` sends, recorded instead of sent."""
    patched = []
    monkeypatch.setattr(vs, "fetch_blocks", children.__getitem__)
    monkeypatch.setattr(hv.time, "sleep", lambda s: None)
    monkeypatch.setattr(hv, "notion", lambda method, path, body=None:
                        patched.append((method, path, body)))
    return patched


def archived(monkeypatch, blocks, children):
    """Block ids `dedupe_duplicates` archives, applied against a recorder."""
    patched = record(monkeypatch, children)
    n = hv.dedupe_duplicates("page-1", blocks, apply=True)
    assert all(m == "PATCH" and b == {"archived": True} for m, _, b in patched)
    ids = [p.removeprefix("/blocks/") for _, p, _ in patched]
    assert n == len(ids)
    return ids


def refusal(monkeypatch, blocks, children):
    """The message of the refusal `dedupe_duplicates` raises, having sent nothing."""
    patched = record(monkeypatch, children)
    with pytest.raises(hv.DedupRefused) as raised:
        hv.dedupe_duplicates("page-1", blocks, apply=True)
    assert patched == []
    return str(raised.value)


class TestACopyWithItsTextInAColumnList:

    def test_it_is_kept_and_only_the_other_copy_s_top_level_blocks_go(self, monkeypatch):
        cols, children = columns("cols", [image("img")],
                                 [block("paragraph", BODY, "inner")])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", "아래 그림과 함께 설명한다.", "pB"),
            cols,
        ]
        assert archived(monkeypatch, blocks, children) == ["hA", "pA"]

    def test_a_different_body_in_columns_is_no_copy(self, monkeypatch):
        cols, children = columns("cols", [image("img")],
                                 [block("paragraph", OTHER, "inner")])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", "결과는 다음과 같다. " * 10, "p4"),
            block("heading_1", "3 Evaluation (평가)", "hB"),
            cols,
        ]
        assert archived(monkeypatch, blocks, children) == []


class TestAColumnListInsideTheArchivedCopy:

    def test_a_nested_one_goes_as_its_own_id_and_none_of_its_children(self, monkeypatch):
        inner, inner_children = columns("inner", [block("paragraph", BODY, "pIn")],
                                        [image("imgIn", FIGURE)])
        outer, children = columns("outer", [inner], [image("imgOut", CAPTION)])
        children.update(inner_children)
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            image("iA1", FIGURE),
            image("iA2", CAPTION),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            outer,
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
        ]
        assert archived(monkeypatch, blocks, children) == ["hB", "outer"]

    def test_an_empty_one_goes_with_the_copy(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
            {"type": "column_list", "id": "empty", "has_children": True},
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
        ]
        assert archived(monkeypatch, blocks, {"empty": []}) == ["hB", "pB", "empty"]


class TestACopyTheRepairCannotArchiveWhole:

    def test_its_heading_inside_a_column_raises_naming_page_and_heading(self, monkeypatch):
        cols, children = columns("cols", [block("heading_1", "3 Method (방법)", "hB"),
                                          block("paragraph", BODY, "pB")],
                                 [image("img")])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            cols,
        ]
        message = refusal(monkeypatch, blocks, children)
        assert "page-1" in message and "'3 Method" in message and "inside a column" in message

    def test_a_column_list_holding_the_next_heading_raises(self, monkeypatch):
        cols, children = columns("cols", [block("paragraph", "그림과 함께 이어서 설명한다.", "pC"),
                                          block("heading_1", "5 Discussion (논의)", "h5"),
                                          block("paragraph", OTHER, "p5")],
                                 [image("img")])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", "결과는 다음과 같다. " * 10, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
            cols,
        ]
        assert "inside column_list cols" in refusal(monkeypatch, blocks, children)


class TestASubsectionCopyInsideTheParentSArchivedCopy:
    """It goes with its parent's copy, whole, even where on its own it would have
    no top-level range."""

    def test_its_heading_inside_a_column(self, monkeypatch):
        cols, children = columns("cols", [block("heading_2", "3.1 Detail (세부)", "hB1"),
                                          block("paragraph", DETAIL, "pB1")],
                                 [image("img", FIGURE)])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            image("iA1", FIGURE),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
            cols,
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
        ]
        assert archived(monkeypatch, blocks, children) == ["hB", "pB", "cols"]

    def test_it_ending_inside_a_column_list_that_holds_the_next_subsection(self, monkeypatch):
        cols, children = columns("cols", [block("paragraph", "그림 설명 문장이다.", "pc"),
                                          block("heading_2", "3.2 Setup (설정)", "hB2"),
                                          block("paragraph", SETUP, "pB2")],
                                 [image("img", FIGURE)])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            block("paragraph", "그림 설명 문장이다.", "pA1c"),
            block("heading_2", "3.2 Setup (설정)", "hA2"),
            block("paragraph", SETUP, "pA2"),
            image("iA2", FIGURE),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", DETAIL, "pB1"),
            cols,
            block("heading_1", "5 Discussion (논의)", "h5"),
            block("paragraph", "논의는 다음과 같다. " * 10, "p5"),
        ]
        assert archived(monkeypatch, blocks, children) == ["hB", "pB", "hB1", "pB1", "cols"]

    def test_its_heading_inside_an_archived_column_is_no_standing_copy(self, monkeypatch):
        """Copy 2's 3.1, in a column, rolls up the unlabelled section after it and
        so measures richer than copy 1's, which holds the same own text."""
        cols, children = columns("cols", [block("heading_2", "3.1 Detail (세부)", "hB1"),
                                          block("paragraph", DETAIL, "pB1")],
                                 [block("heading_2", "Notes (메모)", "hBn"),
                                  block("paragraph", SETUP * 2, "pBn")])
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_2", "Notes (메모)", "hAn"),
            block("paragraph", SETUP * 2, "pAn"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
            cols,
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
        ]
        assert archived(monkeypatch, blocks, children) == ["hB", "pB", "cols"]


def two_copies(earlier, later):
    """Section 3 twice, copy 1's own body cut short, so the parent keeps copy 2 and
    archives copy 1 whole; `earlier` and `later` follow each copy's 3.1 heading."""
    return front() + [
        block("heading_1", "3 Method (방법)", "hA"),
        block("paragraph", TRUNCATED, "pA"),
        block("heading_2", "3.1 Detail (세부)", "hA1"),
        *earlier,
        block("heading_1", "4 Results (결과)", "h4"),
        block("paragraph", OTHER, "p4"),
        block("heading_1", "3 Method (방법)", "hB"),
        block("paragraph", BODY + " " + BODY, "pB"),
        block("heading_2", "3.1 Detail (세부)", "hB1"),
        *later,
    ]


class TestAParentKeepingItsRicherLaterCopy:
    """Copy 1's own body is cut short, so the parent keeps copy 2 and archives copy
    1 whole, subsections included."""

    @staticmethod
    def page(later_detail, earlier_sub="3.1 Detail (세부)"):
        return front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", earlier_sub, "hA1"),
            block("paragraph", DETAIL, "pA1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY + " " + BODY, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", later_detail, "pB1"),
        ]

    def test_the_subsection_keeps_the_copy_inside_the_kept_parent(self, monkeypatch):
        assert archived(monkeypatch, self.page(DETAIL), {}) == ["hA", "pA", "hA1", "pA1"]

    def test_a_subsection_text_only_the_archived_copy_holds_refuses_the_page(
            self, monkeypatch):
        message = refusal(monkeypatch, self.page("짧다."), {})
        assert "page-1" in message and "3.1 Detail" in message

    def test_a_subsection_only_the_archived_copy_has_refuses_the_page(self, monkeypatch):
        blocks = self.page(DETAIL, earlier_sub="3.2 Setup (설정)")
        assert "3.2 Setup" in refusal(monkeypatch, blocks, {})


class TestTheCopyHoldingAnArchivedSectionSText:
    """A copy left standing holds each archived section's own text, its
    subsections left out; a subsection is held in its own group."""

    def test_a_parent_body_only_the_archived_copy_has_refuses_the_page(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", TRUNCATED, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", DETAIL * 2, "pB1"),
        ]
        message = refusal(monkeypatch, blocks, {})
        assert "page-1" in message and "'3 Method" in message

    def test_a_subsection_body_only_the_archived_copy_has_refuses_the_page(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "h3"),
            block("paragraph", BODY, "p3"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL[:40], "pA1"),
            block("heading_3", "3.1.1 Sensor (센서)", "hA11"),
            block("paragraph", SETUP * 2, "pA11"),
            block("heading_2", "3.2 Results (결과)", "h32"),
            block("paragraph", OTHER, "p32"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", DETAIL, "pB1"),
            block("heading_3", "3.1.1 Sensor (센서)", "hB11"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
        ]
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_an_empty_section_needs_none(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", "3.9 Extra (추가)", "hA9"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY, "pB"),
        ]
        assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA9"]

    def test_an_archived_copy_holds_none(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", TRUNCATED, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", DETAIL, "pB1"),
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
            block("heading_1", "3 Method (방법)", "hC"),
            block("paragraph", BODY + " " + BODY, "pC"),
            block("heading_2", "3.1 Detail (세부)", "hC1"),
            block("paragraph", "짧다.", "pC1"),
        ]
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_standing_copy_with_another_body_holds_none(self, monkeypatch):
        blocks = TestAParentKeepingItsRicherLaterCopy.page(SETUP)
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_short_body_is_held_only_by_a_copy_with_its_words(self, monkeypatch):
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hK"),
            block("paragraph", BODY, "pK"),
            block("heading_2", "3.1 Detail (세부)", "hK1"),
            block("paragraph", "토크 센서는 매 실험 전에 영점을 다시 맞추었다.", "pK1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hU"),
            block("paragraph", SETUP, "pU"),
            block("heading_2", "3.1 Detail (세부)", "hU1"),
            block("paragraph", OTHER * 2, "pU1"),
        ]
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @staticmethod
    def with_equation(later):
        """Copy 1's subsection is one equation; copy 2's is `later`."""
        return front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            equation("x_t = f(x_{t-1})", "eA1"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY + " " + BODY, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            later,
        ]

    def test_a_body_with_no_word_is_held_by_a_copy_with_it_verbatim(self, monkeypatch):
        blocks = self.with_equation(equation("x_t = f(x_{t-1})", "eB1"))
        assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "eA1"]

    def test_a_body_with_no_word_is_held_by_no_other_copy(self, monkeypatch):
        blocks = self.with_equation(block("paragraph", DETAIL, "pB1"))
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_short_distinct_body_sharing_most_words_with_the_kept_copy_refuses(
            self, monkeypatch):
        """Four of its six words are in the kept copy; the sentence is not."""
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hK"),
            block("paragraph", BODY, "pK"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hS"),
            block("paragraph", "제안하는 방법은 실제 로봇에서 정책을 학습하지 않는다.", "pS"),
        ]
        assert "'3 Method" in refusal(monkeypatch, blocks, {})

    @staticmethod
    def twice(kept, later):
        """Section 3 twice, copy 1 kept; `kept` and `later` follow each heading."""
        return front() + [block("heading_1", "3 Method (방법)", "hK"), *kept,
                          block("heading_1", "4 Results (결과)", "h4"),
                          block("paragraph", OTHER, "p4"),
                          block("heading_1", "3 Method (방법)", "hS"), *later]

    @staticmethod
    def past_the_cap(tag, tail):
        """Paragraphs of `TEN` over and over, past the 4000 characters of a section's
        `text`, then a paragraph of `tail`."""
        return [block("paragraph", " ".join(TEN * 7), f"{tag}{n}") for n in range(3)] + [
            block("paragraph", tail, f"{tag}t")]

    def test_a_body_past_the_text_cap_is_held_only_by_its_words_after_it(self, monkeypatch):
        """Copy 2's body, as long as copy 1's, has twenty sentences after the cap
        that copy 1's has not."""
        blocks = self.twice(
            self.past_the_cap("pK", " ".join(sentence(n) for n in range(10, 30))),
            self.past_the_cap("pS", " ".join(sentence(n) for n in range(40, 60))))
        assert "'3 Method" in refusal(monkeypatch, blocks, {})

    def test_captions_past_the_text_cap_are_held_only_by_a_copy_with_their_words(
            self, monkeypatch):
        """Copy 2's body is copy 1's sentences, past the cap, then two captions of
        fifteen words each that copy 1, the longer, has not."""
        captions = [f"그림 {n}: " + " ".join(words(range(15 * n, 15 * n + 15))) + "."
                    for n in (1, 2)]
        blocks = self.twice(self.past_the_cap("pK", " ".join(TEN * 7)),
                            self.past_the_cap("pS", captions[0])
                            + [block("paragraph", captions[1], "cS")])
        assert "'3 Method" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("earlier", [sentence(99),
                                         " ".join(sentence(n) for n in range(10, 20))])
    def test_a_copy_holds_what_its_body_has_past_the_text_cap(self, monkeypatch, earlier):
        """Copy 1's 3.1 is one sentence, or ten, that copy 2's has after the cap."""
        blocks = two_copies([block("paragraph", earlier, "pA1")],
                            self.past_the_cap("qB", earlier))
        assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1"]

    @pytest.mark.parametrize("over, held", [(-1, False), (0, True)])
    def test_a_body_under_the_empty_twin_bar_is_held_only_verbatim(self, monkeypatch,
                                                                   over, held):
        """Copy 1's 3.1 is `hv.MIN_WORDS` words, then digits up to `over` characters
        past the bar; copy 2's has three fifths of the words and others."""
        n = hv.MIN_WORDS
        shared = -(-3 * n // 5)
        digits = " " + "0" * vs.EMPTY_TWIN_CHARS
        earlier = (" ".join(words(range(n))) + digits)[:vs.EMPTY_TWIN_CHARS + over]
        later = " ".join(words(range(shared)) + words(range(100, 100 + n - shared))) + digits
        blocks = two_copies([block("paragraph", earlier, "pA1")],
                            [block("paragraph", later, "pB1")])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("n, held", [(19, False), (20, True)])
    def test_a_body_is_held_by_shared_words_only_with_twenty_of_them(self, monkeypatch, n,
                                                                     held):
        """Copy 1's 3.1 is `n` words, each with a number; copy 2's, as long, has three
        fifths of them and others."""
        shared = -(-3 * n // 5)

        def text(ws):
            return " ".join(f"{w} 0.5" for w in ws)

        blocks = two_copies(
            [block("paragraph", text(words(range(n))), "pA1")],
            [block("paragraph", text(words(range(shared)) + words(range(100, 100 + n - shared))),
                   "pB1")])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("expression", [
        r"\mathcal{L}(\theta) = \sum_{i=1}^{N} \left( \frac{1}{2} \lVert \mathbf{x}_i - "
        r"\mathbf{f}_\theta(\mathbf{z}_i) \rVert^2 + \lambda \lVert \theta \rVert_1 \right)",
        r"x_1 + 2 y_2 = 3 z_3 - 4 w_4 + 5 v_5 \cdot 6 u_6 / 7 t_7 + 8 s_8 - 9 r_9 + "
        r"10 q_10 \cdot 11 p_11 + 12 o_12 - 13 n_13 + 14 m_14 = 0"])
    @pytest.mark.parametrize("same", [True, False])
    def test_a_long_equation_is_held_only_by_a_copy_with_it_verbatim(self, monkeypatch,
                                                                    expression, same):
        """Copy 1's 3.1 is one equation of few words; copy 2's is it, or the same
        macros with other numbers."""
        later = expression if same else expression.translate(str.maketrans("12", "21"))
        blocks = two_copies([equation(expression, "eA1")], [equation(later, "eB1")])
        if same:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "eA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("later, held", [
        (TEN[:6] + [sentence(n) for n in range(10, 14)], True),
        (TEN[:5] * 2, False)])
    def test_a_body_is_held_by_a_copy_with_shared_words_of_its_words(self, monkeypatch,
                                                                    later, held):
        """Copy 1's 3.1 has ten sentences; copy 2's, as long, has six of them and
        four others, or five of them twice."""
        blocks = two_copies([block("paragraph", " ".join(TEN), "pA1")],
                            [block("paragraph", " ".join(later), "pB1")])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("over, twin", [(-1, True), (0, False)])
    def test_a_copy_under_the_bar_is_an_empty_twin_of_any_same_titled_section(self, over,
                                                                              twin):
        sections = vs.group_sections([
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", (OTHER * 2)[:vs.EMPTY_TWIN_CHARS + over], "pB")])
        assert vs.dup_confirmed(sections[0], sections[2]) is twin

    @pytest.mark.parametrize("kept, held", [
        ([" ".join(TEN[:7])], False),
        ([" ".join(TEN)[:-1]], False),
        ([" ".join(TEN[:7]), "3.1.1 Sensor (센서)", SETUP], False),
        ([" ".join(TEN)], True)])
    def test_a_body_is_held_only_by_a_copy_with_as_many_characters(self, monkeypatch, kept,
                                                                   held):
        """Copy 1's 3.1 has ten sentences; copy 2's is a paragraph of `kept`, and a
        3.1.1 under it."""
        later = [block("paragraph", kept[0], "pB1")] + [
            block(kind, text, f"xB{n}") for n, (kind, text)
            in enumerate(zip(("heading_3", "paragraph"), kept[1:]))]
        blocks = two_copies([block("paragraph", " ".join(TEN), "pA1")], later)
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_caption_only_the_archived_copy_has_refuses_the_page(self, monkeypatch):
        blocks = two_copies([block("paragraph", CAPTION, "cA1")], [])
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_caption_only_the_archived_copy_of_a_long_section_has_refuses_the_page(
            self, monkeypatch):
        """Copy 2's 3.1 has all of copy 1's words but the caption's few."""
        blocks = two_copies([block("paragraph", " ".join(TEN), "pA1"),
                             block("paragraph", CAPTION, "cA1")],
                            [block("paragraph", " ".join(TEN), "pB1")])
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("kept, held", [(CAPTION, True),
                                            (CAPTION.replace("입력과", "구성과"), False)])
    def test_a_caption_is_held_only_by_a_copy_with_it(self, monkeypatch, kept, held):
        blocks = two_copies([block("paragraph", CAPTION, "cA1")],
                            [block("paragraph", kept, "cB1")])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "cA1"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_a_short_body_is_held_by_a_copy_with_it_set_otherwise(self, monkeypatch):
        """Copy 1's 3.1 is two paragraphs; copy 2's is one, a line break between."""
        blocks = two_copies([block("paragraph", TEN[0], "pA1"), block("paragraph", TEN[1], "pA1b")],
                            [block("paragraph", f"{TEN[0]}\n{TEN[1]}", "pB1")])
        assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1", "pA1b"]

    def test_a_heading_with_no_text_of_its_own_goes_though_a_section_folds_into_it(
            self, monkeypatch):
        """`Notes`, unlabelled at 3.9's depth, rolls up into 3.9 and is held in its
        own group."""
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", "3.9 Extra (추가)", "hA9"),
            block("heading_2", "Notes (메모)", "hAn"),
            block("paragraph", CAPTION, "cAn"),
            block("paragraph", DETAIL, "pAn"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", BODY + " " + BODY, "pB"),
            block("heading_2", "Notes (메모)", "hBn"),
            block("paragraph", CAPTION, "cBn"),
            block("paragraph", DETAIL, "pBn"),
        ]
        assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA9", "hAn", "cAn", "pAn"]


def nested(bid, kids):
    """A bulleted item with children, and the children `fetch_blocks` returns."""
    item = block("bulleted_list_item", "센서 구성", bid)
    item["has_children"] = True
    return item, {bid: [block("bulleted_list_item", k, f"{bid}-{n}")
                        for n, k in enumerate(kids)]}


class TestABlockTheTextCheckDoesNotRead:
    """An image, a table or a block with children in an archived copy's own span
    needs an equal block in one standing copy of the same section."""

    def test_an_image_only_the_archived_copy_has_refuses_the_page(self, monkeypatch):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), image("iA", FIGURE)],
                            [block("paragraph", DETAIL, "pB1")])
        message = refusal(monkeypatch, blocks, {})
        assert "'3.1 Detail" in message and "image" in message

    def test_a_section_holding_only_images_refuses_the_page(self, monkeypatch):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"),
                             block("heading_2", "3.9 Figures (그림)", "hA9"),
                             image("iA9", FIGURE), image("iA9b")],
                            [block("paragraph", DETAIL, "pB1")])
        assert "'3.9 Figures" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("caption, held", [(FIGURE, True),
                                               (FIGURE.replace(" ", "  "), True),
                                               (CAPTION, False)])
    def test_an_image_is_held_by_one_under_its_caption(self, monkeypatch, caption, held):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), image("iA", FIGURE)],
                            [block("paragraph", DETAIL, "pB1"), image("iB", caption)])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1", "iA"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    def test_panels_under_one_caption_need_as_many_in_the_kept_copy(self, monkeypatch):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"),
                             image("iA", FIGURE), image("iAb", FIGURE)],
                            [block("paragraph", DETAIL, "pB1"), image("iB", FIGURE)])
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("url, kept_url, held", [
        ("https://example.org/x1.png", "https://example.org/x1.png", True),
        ("https://example.org/x1.png", None, False),
        (None, None, False)])
    def test_an_uncaptioned_image_is_held_by_one_at_its_url(self, monkeypatch, url, kept_url,
                                                            held):
        """Without `url` an image is hosted, at a url signed for it alone."""
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), image("iA", url=url)],
                            [block("paragraph", DETAIL, "pB1"), image("iB", url=kept_url)])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1", "iA"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("kind", ["image", "bookmark", "embed"])
    @pytest.mark.parametrize("url, held", [("https://example.org/a", True),
                                           ("https://example.org/b", False)])
    def test_a_captioned_block_at_a_url_is_held_by_one_at_its_url(self, monkeypatch, kind,
                                                                  url, held):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"),
                             linked(kind, "xA", FIGURE, "https://example.org/a")],
                            [block("paragraph", DETAIL, "pB1"), linked(kind, "xB", FIGURE, url)])
        if held:
            assert archived(monkeypatch, blocks, {}) == ["hA", "pA", "hA1", "pA1", "xA"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("only", [{"type": "divider", "id": "xA", "divider": {}},
                                      linked("bookmark", "xA", "", "https://example.org/a")])
    def test_a_divider_or_a_bookmark_only_the_archived_copy_has_refuses_the_page(
            self, monkeypatch, only):
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), only],
                            [block("paragraph", DETAIL, "pB1")])
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, {})

    @pytest.mark.parametrize("hid, title, own", [("hA", "'3 Method", DETAIL),
                                                 ("hA1", "'3.1 Detail", DETAIL),
                                                 ("hA1", "'3.1 Detail", "")])
    def test_a_heading_with_children_in_the_archived_copy_refuses_the_page(self, monkeypatch,
                                                                          hid, title, own):
        """A toggleable heading holds blocks no section's span has; copy 1's 3.1 is a
        paragraph of `own`, or nothing but the heading's children when `own` is empty."""
        blocks = two_copies([block("paragraph", own, "pA1")] if own else [],
                            [block("paragraph", DETAIL, "pB1")])
        heading = next(b for b in blocks if b["id"] == hid)
        heading["has_children"] = True
        heading[heading["type"]]["is_toggleable"] = True
        children = {hid: [block("paragraph", SETUP, f"{hid}-k")]}
        assert title in refusal(monkeypatch, blocks, children)

    @pytest.mark.parametrize("rows, held", [([("a", "1"), ("b", "2")], True),
                                            ([("a", "1")], False)])
    def test_a_table_is_held_by_one_with_its_rows(self, monkeypatch, rows, held):
        tA, children = table("tA", [("a", "1"), ("b", "2")])
        tB, kept_rows = table("tB", rows)
        children.update(kept_rows)
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), tA],
                            [block("paragraph", DETAIL, "pB1"), tB])
        if held:
            assert archived(monkeypatch, blocks, children) == ["hA", "pA", "hA1", "pA1", "tA"]
        else:
            assert "'3.1 Detail" in refusal(monkeypatch, blocks, children)

    def test_an_image_the_kept_copy_holds_in_another_section_refuses(self, monkeypatch):
        blocks = two_copies([block("paragraph", DETAIL, "pA1")],
                            [block("paragraph", DETAIL, "pB1"), image("iB", FIGURE)])
        blocks.insert(blocks.index(next(b for b in blocks if b["id"] == "hA1")),
                      image("iA", FIGURE))
        assert "'3 Method" in refusal(monkeypatch, blocks, {})

    def test_standing_copies_of_the_section_hold_its_blocks_between_them(self, monkeypatch):
        """Copy 2's 3 has another body, so it stands beside copy 1, the one kept;
        copy 3 is copy 1's twin, and its 3.1 has the images copies 1 and 2 have."""
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            image("iA", FIGURE),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", SETUP, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", OTHER, "pB1"),
            image("iB", CAPTION),
            block("heading_1", "5 Data (데이터)", "h5"),
            block("paragraph", "데이터는 다음과 같다. " * 10, "p5"),
            block("heading_1", "3 Method (방법)", "hC"),
            block("paragraph", BODY, "pC"),
            block("heading_2", "3.1 Detail (세부)", "hC1"),
            block("paragraph", DETAIL, "pC1"),
            image("iC", FIGURE),
            image("iC2", CAPTION),
        ]
        assert archived(monkeypatch, blocks, {}) == ["hC", "pC", "hC1", "pC1", "iC", "iC2"]

    def test_an_archived_copy_holds_none(self, monkeypatch):
        """Copies 1 and 2 are cut short and archived; their 3.1s both have the image."""
        blocks = front() + [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", TRUNCATED, "pA"),
            block("heading_2", "3.1 Detail (세부)", "hA1"),
            block("paragraph", DETAIL, "pA1"),
            image("iA", FIGURE),
            block("heading_1", "4 Results (결과)", "h4"),
            block("paragraph", OTHER, "p4"),
            block("heading_1", "3 Method (방법)", "hB"),
            block("paragraph", TRUNCATED, "pB"),
            block("heading_2", "3.1 Detail (세부)", "hB1"),
            block("paragraph", DETAIL, "pB1"),
            image("iB", FIGURE),
            block("heading_1", "5 Setup (설정)", "h5"),
            block("paragraph", SETUP, "p5"),
            block("heading_1", "3 Method (방법)", "hC"),
            block("paragraph", BODY + " " + BODY, "pC"),
            block("heading_2", "3.1 Detail (세부)", "hC1"),
            block("paragraph", DETAIL, "pC1"),
        ]
        assert "image" in refusal(monkeypatch, blocks, {})

    def test_children_only_the_archived_copy_has_refuse_the_page(self, monkeypatch):
        item, children = nested("lA", ["토크 센서", "관성 센서"])
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), item],
                            [block("paragraph", DETAIL, "pB1"),
                             block("bulleted_list_item", "센서 구성", "lB")])
        assert "'3.1 Detail" in refusal(monkeypatch, blocks, children)

    def test_a_block_with_the_same_children_in_the_kept_copy_goes(self, monkeypatch):
        item, children = nested("lA", ["토크 센서", "관성 센서"])
        kept, kept_children = nested("lB", ["토크 센서", "관성 센서"])
        children.update(kept_children)
        blocks = two_copies([block("paragraph", DETAIL, "pA1"), item],
                            [block("paragraph", DETAIL, "pB1"), kept])
        assert archived(monkeypatch, blocks, children) == ["hA", "pA", "hA1", "pA1", "lA"]


def test_a_refused_page_s_journal_shows_the_refusal_and_its_audit_flags(monkeypatch, capsys):
    import extract_paper_figures
    import heal_paper_pages as hp
    patched = record(monkeypatch, {"pg": TestAParentKeepingItsRicherLaterCopy.page(SETUP)})
    monkeypatch.setattr(hp, "_page", lambda pid: {"id": pid, "properties": {}})
    monkeypatch.setattr(hp, "_skipped_pages", lambda: set())
    for name in ("strip_furniture", "strip_backmatter", "clean_page", "wrap_math_page",
                 "heal_equations", "heal_mangled", "heal_fences", "heal_figures",
                 "heal_tables", "heal_pdf_media", "heal_references"):
        monkeypatch.setattr(hp, name, lambda *a, **k: {})
    monkeypatch.setattr(extract_paper_figures, "arxiv_id_from_page", lambda pid: None)
    monkeypatch.setattr(hv, "audit", lambda pid, arxiv=None: {"findings": [
        {"type": "DUPLICATE", "section": "3.1", "detail": "repeated"},
        {"type": "MISSING", "section": "5", "detail": "absent"}]})
    hp.heal(["pg"], apply=True)
    journal = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("  pg: ")]
    assert patched == []
    assert [ln for ln in journal if "AUDIT" in ln] == ["  pg: AUDIT DUPLICATE(3.1): repeated",
                                                       "  pg: AUDIT MISSING(5): absent"]
    refused = [ln for ln in journal if "AUDIT" not in ln]
    assert len(refused) == 1 and "page pg" in refused[0] and "'3.1 Detail" in refused[0]


class TestTheSafetyCap:
    """A plan archiving more than half the page's top-level blocks is refused."""

    @staticmethod
    def page():
        """Seven top-level blocks; the plan archives copy 2's four."""
        return [
            block("heading_1", "3 Method (방법)", "hA"),
            block("paragraph", BODY, "pA"),
            block("heading_1", "4 Results (결과)", "h4"),
            block("heading_1", "3 Method (방법)", "hB"),
        ] + [block("paragraph", " ".join(SENTENCES[n:n + 3]), f"pB{n}") for n in (0, 3, 6)]

    def test_it_refuses_most_of_the_page(self, monkeypatch):
        message = refusal(monkeypatch, self.page(), {})
        assert "page-1" in message and "4 of 7" in message

    def test_it_allows_half_the_page(self, monkeypatch):
        blocks = [block("paragraph", "앞 문단입니다.", "f0")] + self.page()
        assert archived(monkeypatch, blocks, {}) == ["hB", "pB0", "pB3", "pB6"]

    def test_it_counts_top_level_blocks_not_the_blocks_in_columns(self, monkeypatch):
        cols, children = columns("cols", [block("paragraph", s, f"q{n}")
                                          for n, s in enumerate(SENTENCES)],
                                 [image("img")])
        blocks = [
            block("heading_1", "3 Method (방법)", "hA"),
            cols,
            block("heading_1", "4 Results (결과)", "h4"),
            block("heading_1", "3 Method (방법)", "hB"),
        ] + [block("paragraph", " ".join(SENTENCES[n:n + 3]), f"pB{n}") for n in (0, 3, 6)]
        assert "4 of 7" in refusal(monkeypatch, blocks, children)


def test_a_page_without_columns_plans_its_top_level_ranges_and_fetches_nothing(monkeypatch):
    blocks = front() + [
        block("heading_1", "3 Method (방법)", "h3"),
        block("paragraph", BODY, "p3"),
        block("heading_2", "3.1 Detail (세부)", "h31"),
        block("paragraph", OTHER, "p31"),
        block("heading_1", "4 Results (결과)", "h4"),
        block("paragraph", "결과는 다음과 같다. " * 10, "p4"),
        block("heading_1", "3 Method (방법)", "c3"),
        block("paragraph", BODY, "q3"),
        block("heading_2", "3.1 Detail (세부)", "c31"),
        block("paragraph", OTHER, "q31"),
    ]
    assert archived(monkeypatch, blocks, {}) == ["c3", "q3", "c31", "q31"]
