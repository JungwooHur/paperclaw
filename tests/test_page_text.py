"""A lecture page as the text an answer is grounded in.

The page is the source: the narration script and storyboard the owner wrote,
under the video. Reading it whole on every question keeps answers to what the
lecture actually says and always current with the script — nothing to upload
and nothing to go stale. Earlier Q&A callouts are left out: they are answers,
not the lecture.
"""
import page_text as pt


def rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def block(kind, text="", bid=None, children=False, **extra):
    payload = {"rich_text": rich(text)} if text else {}
    payload.update(extra)
    return {"id": bid or kind + text[:8], "type": kind, "has_children": children,
            kind: payload}


def no_children(block_id):
    return []


class TestTheScript:

    def test_headings_keep_their_level(self):
        out = pt.page_text([block("heading_2", "Conventions")], no_children)
        assert "## Conventions" in out

    def test_lists_keep_their_markers(self):
        out = pt.page_text([block("bulleted_list_item", "a point"),
                            block("numbered_list_item", "a step")], no_children)
        assert "- a point" in out and "1. a step" in out

    def test_the_video_is_named(self):
        video = {"id": "v", "type": "video", "has_children": False,
                 "video": {"type": "external",
                           "external": {"url": "https://example.org/v"}}}
        assert "https://example.org/v" in pt.page_text([video], no_children)

    def test_a_display_equation_is_kept(self):
        eq = {"id": "e", "type": "equation", "has_children": False,
              "equation": {"expression": "a^2+b^2"}}
        assert "$$a^2+b^2$$" in pt.page_text([eq], no_children)

    def test_a_table_is_read_row_by_row(self):
        table = block("table", bid="t", children=True)
        rows = [{"id": "r1", "type": "table_row", "has_children": False,
                 "table_row": {"cells": [rich("stage"), rich("ms")]}},
                {"id": "r2", "type": "table_row", "has_children": False,
                 "table_row": {"cells": [rich("prefill"), rich("38")]}}]
        out = pt.page_text([table], lambda bid: rows if bid == "t" else [])
        assert "stage | ms" in out and "prefill | 38" in out

    def test_nested_items_are_included(self):
        parent = block("bulleted_list_item", "outer", bid="o", children=True)
        inner = block("bulleted_list_item", "inner")
        out = pt.page_text([parent], lambda bid: [inner] if bid == "o" else [])
        assert "- outer" in out and "  - inner" in out


class TestWhatIsNotTheLecture:

    def test_an_earlier_answer_is_left_out(self):
        qa = block("callout", bid="c", children=True)
        toggle = block("toggle", "Q: 왜 그런가요?", bid="q", children=True)
        answer = block("paragraph", "이전 답변입니다.")
        kids = {"c": [toggle], "q": [answer]}
        out = pt.page_text([qa, block("paragraph", "본문")],
                           lambda bid: kids.get(bid, []))
        assert "이전 답변" not in out and "본문" in out

    def test_an_ordinary_callout_is_kept(self):
        note = block("callout", "주의: 단위는 ms입니다", bid="n")
        assert "주의: 단위는 ms입니다" in pt.page_text([note], no_children)
