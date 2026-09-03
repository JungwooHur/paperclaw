"""A paragraph too dense with citations to link every one.

Notion allows a hundred rich_text pieces per block. A related-work paragraph can
carry fifty citations, and linking each needs more pieces than that. Raising on
it aborted the whole page — every other paragraph lost its links too, over one
paragraph nobody could have linked anyway.
"""
import link_references as lr


def para(text):
    return {"id": "b1", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text",
                                         "text": {"content": text},
                                         "plain_text": text}]}}


def dense(n):
    """A paragraph citing 1..n, the shape a related-work section produces."""
    return para(" ".join(f"연구 [{i}]" for i in range(1, n + 1)))


def text_of(spans):
    return "".join(s["text"]["content"] for s in spans)


class TestStayingWithinTheLimit:

    def setup_method(self):
        self.ids = {i: f"blk{i}" for i in range(1, 61)}

    def rewrite(self, n):
        return lr._rewrite_block(dense(n), list(range(1, n + 1)),
                                 "page-id", self.ids, {})

    def test_a_paragraph_that_fits_is_fully_linked(self):
        out = self.rewrite(5)
        assert sum(1 for s in out if (s.get("text") or {}).get("link")) == 5

    def test_a_paragraph_too_dense_is_still_rewritten(self):
        assert self.rewrite(50) is not None

    def test_it_never_exceeds_the_limit(self):
        assert len(self.rewrite(50)) <= lr.MAX_SPANS

    def test_it_links_as_many_as_it_can(self):
        linked = sum(1 for s in self.rewrite(50)
                     if (s.get("text") or {}).get("link"))
        assert linked > 20

    def test_the_text_is_never_changed(self):
        # The one thing that must hold whatever else happens: this pass links,
        # it does not rewrite what the paper says.
        original = "".join(s["text"]["content"]
                           for s in dense(50)["paragraph"]["rich_text"])
        assert text_of(self.rewrite(50)) == original

    def test_the_citations_left_unlinked_are_the_later_ones(self):
        out = self.rewrite(50)
        linked = [s["text"]["content"] for s in out
                  if (s.get("text") or {}).get("link")]
        assert linked[0] == "1"
