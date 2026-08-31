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


class TestDepthSummary:
    """What a later question is told about a paper already being descended.

    It rides along with the resolver's answer, so it must be cheap, silent when
    there is nothing to say, and incapable of stopping the caller.
    """

    def test_says_nothing_when_the_page_has_no_record(self):
        assert descent.format_depth(None) == []

    def test_reports_what_is_passed_open_and_current(self):
        line = " ".join(descent.format_depth(a_state()))
        assert "passed=1" in line
        assert "open=1" in line
        assert "current=n1" in line

    def test_lists_each_passed_layer_by_its_thesis(self):
        lines = descent.format_depth(a_state())
        assert any("이 층의 한 문장" in ln for ln in lines)

    def test_a_closed_branch_is_not_counted_as_open(self):
        state = a_state()
        state["nodes"][1]["status"] = "closed"
        state["nodes"][1]["exit"] = "owned"
        line = " ".join(descent.format_depth(state))
        assert "open=0" in line

    def test_a_malformed_state_reports_rather_than_raises(self):
        assert descent.format_depth({"version": 1}) == []
        assert descent.format_depth("not a state") == []


class TestBranchLifecycle:
    """The shape of the descent, not just the layers that passed.

    A branch noticed once and never pulled is the part worth keeping, and a
    branch that ended should say WHY it ended — already known, nothing below it,
    or the subject changed. Those read differently to a reader coming back.
    """

    def test_offering_branches_puts_them_on_the_frontier(self):
        state = descent.offer(a_state(), "n0", [
            {"axis": "composition", "label": "그건 무엇으로 되어 있나"}])
        added = [n for n in state["nodes"] if n.get("label") == "그건 무엇으로 되어 있나"]
        assert added and added[0]["status"] == "frontier"
        assert added[0]["parent"] == "n0"

    def test_picking_a_branch_makes_it_current(self):
        assert descent.pick(a_state(), "n1")["current"] == "n1"

    def test_closing_a_branch_returns_current_to_its_parent(self):
        state = descent.close(a_state(), "n1", "owned")
        node = [n for n in state["nodes"] if n["id"] == "n1"][0]
        assert node["status"] == "closed"
        assert node["exit"] == "owned"
        assert state["current"] == "n0"

    def test_the_three_ways_a_branch_ends_render_distinguishably(self):
        marks = set()
        for kind in ("owned", "floor", "boundary"):
            state = descent.close(a_state(), "n1", kind)
            marks.add(" ".join(text_of(b)
                               for b in flatten(descent.render_blocks(state))))
        assert len(marks) == 3

    def test_ending_the_descent_clears_current_and_keeps_the_record(self):
        state = descent.end(a_state())
        assert state["current"] is None
        assert len(state["nodes"]) == 2

    def test_a_transition_does_not_mutate_the_state_it_was_given(self):
        original = a_state()
        descent.close(original, "n1", "floor")
        assert original["nodes"][1]["status"] == "frontier"
        assert original["current"] == "n1"


class TestAbsorbingPageEdits:
    """Text is owned by the page; structure is owned by the state.

    The record exists because the sentences are the reader's. A writer that
    overwrote them with what it stored last time would defeat the point — the
    same reason this project protects a hand-curated page from its own healers.
    """

    def test_absorbing_what_we_just_rendered_changes_nothing(self):
        state = a_state()
        absorbed, missing = descent.absorb(state, descent.render_blocks(state))
        assert absorbed == state
        assert missing == []

    def test_a_restatement_edited_on_the_page_wins(self):
        state = a_state()
        blocks = descent.render_blocks(state)
        toggle = [b for b in blocks if b["type"] == "toggle"][0]
        toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"][
            "content"] = "제가 고쳐 쓴 문장입니다."
        absorbed, _ = descent.absorb(state, blocks)
        assert absorbed["nodes"][0]["restatement"] == "제가 고쳐 쓴 문장입니다."

    def test_editing_a_restatement_leaves_the_tree_alone(self):
        state = a_state()
        blocks = descent.render_blocks(state)
        toggle = [b for b in blocks if b["type"] == "toggle"][0]
        toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"][
            "content"] = "다른 문장"
        absorbed, _ = descent.absorb(state, blocks)
        assert absorbed["current"] == state["current"]
        assert [n["id"] for n in absorbed["nodes"]] == [n["id"] for n in state["nodes"]]
        assert absorbed["nodes"][1]["status"] == "frontier"

    def test_a_section_deleted_by_the_reader_is_reported_not_recreated(self):
        state = a_state()
        blocks = [b for b in descent.render_blocks(state) if b["type"] != "toggle"]
        absorbed, missing = descent.absorb(state, blocks)
        assert missing == ["n0"]
        assert absorbed["nodes"][0]["restatement"] == state["nodes"][0]["restatement"]

    def test_an_empty_section_leaves_the_stored_sentence_alone(self):
        state = a_state()
        blocks = descent.render_blocks(state)
        toggle = [b for b in blocks if b["type"] == "toggle"][0]
        toggle["toggle"]["children"] = []
        absorbed, _ = descent.absorb(state, blocks)
        assert absorbed["nodes"][0]["restatement"] == state["nodes"][0]["restatement"]
