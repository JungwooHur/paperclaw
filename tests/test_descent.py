"""The descent record: what is written to a page, and what reads back.

The record is the reader's own words plus the shape of how they got there. It
lives after the injected reference list, where the body boundary already keeps
every healer out. Structure is owned by a machine-readable state block; the
rendered sections are what a person reads.
"""
import descent


def passed_node(node_id, thesis, restatement, parent=None):
    return {"id": node_id, "parent": parent, "status": "passed",
            "thesis": thesis, "restatement": restatement,
            "mermaid": "graph LR\n  A --> B",
            "evidence": {"page": "https://www.notion.so/x#abc", "source": "3.2"}}


def a_state():
    return {
        "version": 1,
        "target": {"page_id": "page-id", "arxiv": "arxiv-id"},
        "analogy": "문을 여는 열쇠",
        "current": "n1",
        "nodes": [
            passed_node("n0", "이 층의 한 문장", "제 말로 다시 말한 문장입니다."),
            {"id": "n1", "parent": "n0", "status": "frontier",
             "axis": "mechanism", "label": "그게 실제로 어떻게 되나"},
        ],
        "findings": [],
    }


class TestRoundTrip:
    def test_a_rendered_record_reads_back_as_the_same_state(self):
        state = a_state()
        assert descent.parse_state(descent.render_blocks(state)) == state


def flatten(blocks):
    """Every block in a payload tree, parents before their children."""
    out = []
    for b in blocks:
        out.append(b)
        payload = b.get(b.get("type")) or {}
        out += flatten(payload.get("children", []))
    return out


def text_of(block):
    payload = block.get(block.get("type")) or {}
    if not isinstance(payload, dict):
        return ""
    return "".join(sp.get("text", {}).get("content", "")
                   for sp in payload.get("rich_text", []))


class TestRenderedRecord:
    def test_a_passed_layer_keeps_the_readers_restatement_verbatim(self):
        state = a_state()
        said = state["nodes"][0]["restatement"]
        assert said in [text_of(b) for b in flatten(descent.render_blocks(state))]

    def test_a_passed_layer_is_collapsible_and_labelled_by_its_thesis(self):
        blocks = flatten(descent.render_blocks(a_state()))
        toggles = [b for b in blocks if b["type"] == "toggle"]
        assert [text_of(t) for t in toggles] == ["이 층의 한 문장"]

    def test_the_diagram_is_a_mermaid_code_block(self):
        blocks = flatten(descent.render_blocks(a_state()))
        codes = [b for b in blocks if b["type"] == "code"]
        assert "mermaid" in [c["code"]["language"] for c in codes]

    def test_a_frontier_branch_is_listed_but_gets_no_section(self):
        blocks = flatten(descent.render_blocks(a_state()))
        listed = " ".join(text_of(b) for b in blocks)
        assert "그게 실제로 어떻게 되나" in listed
        toggles = [b for b in blocks if b["type"] == "toggle"]
        assert len(toggles) == 1

    def test_the_state_survives_a_page_that_also_carries_a_bibliography(self):
        state = a_state()
        page = [{"type": "heading_1",
                 "heading_1": {"rich_text": [{"plain_text": "References"}]}}]
        page += [{"type": "paragraph",
                  "paragraph": {"rich_text": [{"plain_text": f"[{n}] A. Author. T. 2024."}]}}
                 for n in (1, 2, 3)]
        assert descent.parse_state(page + descent.render_blocks(state)) == state


class TestRecordBoundary:
    """Where an existing record begins, so a rewrite replaces it.

    A writer that cannot find what it already wrote appends a second copy. That
    is not hypothetical here: a float injector that could not recognise its own
    output re-injected the same images every five minutes for days.
    """

    def test_finds_the_record_it_wrote(self):
        page = [{"type": "heading_1",
                 "heading_1": {"rich_text": [{"plain_text": "1 Introduction"}]}},
                {"type": "paragraph",
                 "paragraph": {"rich_text": [{"plain_text": "본문."}]}}]
        blocks = page + descent.render_blocks(a_state())
        assert descent.record_start(blocks) == len(page)

    def test_is_none_when_the_page_carries_no_record(self):
        page = [{"type": "paragraph",
                 "paragraph": {"rich_text": [{"plain_text": "본문."}]}}]
        assert descent.record_start(page) is None

    def test_is_not_fooled_by_an_unrelated_code_block(self):
        page = [{"type": "code",
                 "code": {"language": "python",
                          "rich_text": [{"plain_text": "print(1)"}]}},
                {"type": "code",
                 "code": {"language": "json",
                          "rich_text": [{"plain_text": '{"a": 1}'}]}}]
        assert descent.record_start(page) is None
