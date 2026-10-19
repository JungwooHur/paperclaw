r"""A repair whose output it would flag again never stops running.

heal_mangled_math rewrote the same paragraphs on four pages on every healer
cycle for days. Each pass archived the blocks and inserted a rebuilt copy, so
the ids changed while the content did not, and every write bumped the page's
last-edited time, which kept it inside the healer's window. Two causes:

  * A false positive. `is_mangled` flagged any Korean inside an equation, but a
    unit label written in text mode, `2\text{초}`, is legitimate LaTeX. The
    rebuild wrapped it straight back into the same equation, and the next cycle
    fired again.
  * No convergence check. A run whose rebuild came back still mangled was
    written anyway. Rewriting it changed nothing except the block ids.
"""
import copy
import itertools

import pytest

import heal_mangled_math as hm
import heal_paper_pages as hp


def text(s):
    return {"type": "text", "text": {"content": s}, "plain_text": s}


def eq(expr):
    return {"type": "equation", "equation": {"expression": expr},
            "plain_text": expr}


def para(*spans):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": list(spans)}}


def with_unit(expr):
    """A paragraph whose only equation is EXPR, set in ordinary prose."""
    return para(text("측정값은 "), eq(expr), text(" 로 기록된다."))


# The legitimate shapes that looped: unit labels and short tags in text mode.
# Generic stand-ins of the same shapes (a bare unit, a label with a trailing
# space, a multi-word label, a slash), not text from any real page.
UNIT_LABELS = [
    r"2\text{초}",
    r"0.5\text{초}",
    r"\text{단위당 } 3",
    r"30\text{개의 항목}",
    r"\text{태그/라벨}",
]


class TestTextModeKoreanIsNotProse:

    @pytest.mark.parametrize("expr", UNIT_LABELS)
    def test_a_unit_label_is_not_mangled(self, expr):
        assert not hm.is_mangled(with_unit(expr))

    @pytest.mark.parametrize("cmd", [
        "text", "textrm", "textbf", "textit", "texttt", "textsf",
        "textnormal", "mathrm", "mbox", "operatorname"])
    def test_every_text_mode_command_counts(self, cmd):
        assert not hm.is_mangled(with_unit(f"5\\{cmd}{{초}}"))

    def test_nested_braces_stay_inside_the_group(self):
        # The group ends at its OWN closing brace, not the first one.
        assert not hm.is_mangled(with_unit(r"\text{초 {단위} 초} + 1"))


class TestRealManglingIsStillCaught:
    """The two fingerprints from the module docstring must still fire."""

    def test_a_korean_word_as_an_equation(self):
        assert hm.is_mangled(para(text("위치 "), eq("및 "), text("x")))

    def test_a_korean_sentence_as_an_equation(self):
        assert hm.is_mangled(para(eq("가 위치 정보와 결합된다")))

    def test_delimiter_debris_in_an_expression(self):
        assert hm.is_mangled(para(eq(r"\Theta_q(\mathbf{x}_q\)")))
        assert hm.is_mangled(para(eq(r"x + \\( y")))

    def test_debris_counts_even_inside_a_text_group(self):
        # The debris test reads the whole expression, unchanged.
        assert hm.is_mangled(para(eq(r"\text{\(}")))

    def test_korean_next_to_a_text_group(self):
        assert hm.is_mangled(with_unit(r"2\text{초} 동안"))
        assert hm.is_mangled(with_unit(r"동안 \text{초}"))

    def test_korean_between_two_text_groups(self):
        assert hm.is_mangled(with_unit(r"\text{초} 그리고 \mathrm{분}"))

    def test_an_unclosed_group_hides_nothing(self):
        # An unclosed `\text{` is a broken parse, not a text-mode argument.
        assert hm.is_mangled(with_unit(r"\text{초 동안 실행"))


class TestMalformedGroupsDoNotCrash:

    @pytest.mark.parametrize("expr", [
        r"\text{", r"\text{초", r"\text{{{초", r"\text{초}}}", r"}\text{",
        r"\text", r"x\text", r"\text{초 {가}", r"\text{a\}초", r"\text{\\"])
    def test_it_returns_a_verdict(self, expr):
        assert hm.is_mangled(with_unit(expr)) in (True, False)


# --- heal_page against a fake Notion -----------------------------------------

class FakePage:
    """The page's top-level blocks, kept the way Notion would after each write."""

    def __init__(self, blocks):
        self._ids = (f"b{i}" for i in itertools.count())
        self.blocks = [dict(copy.deepcopy(b), id=next(self._ids)) for b in blocks]
        self.calls = []

    def fetch(self, page_id):
        return [b for b in self.blocks if not b.get("archived")]

    def stored(self, child):
        """A written child as a later read returns it: an id, plain_text."""
        b = dict(copy.deepcopy(child), id=next(self._ids))
        for s in (b.get(b["type"]) or {}).get("rich_text", []):
            s["plain_text"] = (s["equation"]["expression"]
                               if s["type"] == "equation" else s["text"]["content"])
        return b

    def notion(self, method, path, body=None, **_):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path.endswith("/children"):
            at = next(i for i, b in enumerate(self.blocks)
                      if b["id"] == body["after"])
            self.blocks[at + 1:at + 1] = [self.stored(c) for c in body["children"]]
        elif body.get("archived"):
            bid = path.rsplit("/", 1)[1]
            next(b for b in self.blocks if b["id"] == bid)["archived"] = True
        return {}

    def written(self):
        return [self.stored(c) for _, path, body in self.calls
                if path.endswith("/children") for c in body["children"]]


@pytest.fixture
def notion_page(monkeypatch):
    """Install a FakePage holding BLOCKS as the page heal_page reads and writes."""
    import translate_fulltext
    import verify_sections

    monkeypatch.setattr("time.sleep", lambda s: None)

    def install(blocks):
        page = FakePage(blocks)
        monkeypatch.setattr(verify_sections, "fetch_blocks", page.fetch)
        monkeypatch.setattr(translate_fulltext, "notion", page.notion)
        return page
    return install


# A swapped parse spread over two blocks Notion split, with a unit label inside
# it: rebuilding it must also leave the label alone on the next pass.
GENUINE = [
    para(text("서론 문단입니다.")),
    para(text("위치 정보는 "), eq("및 "), text(r"\mathbf{x}_q"),
         eq("가 위치 정보와 결합되고 각 단계는 "), eq(r"2\text{초}"),
         text(" 동안 실행된다. "), eq(r"\Theta_q(\mathbf{x}_q"),
         text(r", m) - \(")),
    para(text(r"\begin{aligned} a &= b \\ c &= d \end{aligned} 그리고 "),
         eq("이것이 결과이다.")),
    para(text("결론 문단입니다.")),
]

# A prompt template typeset as one equation, Korean placeholder outside any
# text group. Whatever it really is, the rebuild hands back the same equation.
TEMPLATE = para(text("모델은 다음 형식으로 답한다: "),
                eq(r"{ ''task'': ''<한 문장의 작업 설명>'', ''max_steps'': 10 }"),
                text(" 끝."))

MIXED = GENUINE[:3] + [para(text("중간 문단입니다.")), TEMPLATE, GENUINE[3]]


class TestTheRepairConverges:

    def test_the_genuine_run_is_repaired(self, notion_page):
        page = notion_page(GENUINE)
        rep = hm.heal_page("p", apply=True)
        assert rep["mangled_blocks"] == 2 and rep["new_blocks"] > 0
        assert not any(hm.is_mangled(b) for b in page.fetch("p"))

    def test_a_second_pass_writes_nothing(self, notion_page):
        page = notion_page(GENUINE)
        hm.heal_page("p", apply=True)
        assert page.calls, "the first pass must have repaired something"
        page.calls.clear()
        rep = hm.heal_page("p", apply=True)
        assert page.calls == []
        assert rep["mangled_blocks"] == 0 and rep["new_blocks"] == 0

    def test_insert_comes_before_archive(self, notion_page):
        page = notion_page(GENUINE)
        hm.heal_page("p", apply=True)
        kinds = ["insert" if p.endswith("/children") else "archive"
                 for _, p, _ in page.calls]
        assert kinds == ["insert", "archive", "archive"]

    def test_a_page_of_unit_labels_is_left_alone(self, notion_page):
        page = notion_page([with_unit(e) for e in UNIT_LABELS])
        rep = hm.heal_page("p", apply=True)
        assert page.calls == [] and rep["mangled_blocks"] == 0


class TestARunThatCannotConvergeIsNotWritten:

    @pytest.mark.parametrize("apply", [False, True])
    def test_the_template_is_reported_and_untouched(self, notion_page, apply):
        page = notion_page([para(text("앞 문단.")), TEMPLATE])
        rep = hm.heal_page("p", apply=apply)
        assert rep["unconverged"] == 1
        assert page.calls == []
        assert rep["mangled_blocks"] == 0 and rep["new_blocks"] == 0
        assert [r["blocks"] for r in rep["runs"] if r["unconverged"]] == [[1]]

    def test_the_other_runs_are_still_repaired(self, notion_page):
        page = notion_page(MIXED)
        rep = hm.heal_page("p", apply=True)
        assert rep["unconverged"] == 1 and rep["mangled_blocks"] == 2
        archived = {p.rsplit("/", 1)[1] for _, p, b in page.calls
                    if b.get("archived")}
        assert archived == {"b1", "b2"}           # never the template, b4

    def test_mangled_counts_only_what_was_written(self, notion_page):
        page = notion_page(MIXED)
        rep = hm.heal_page("p", apply=True)
        assert rep["new_blocks"] == len(page.written())

    def test_dry_run_reports_what_apply_does(self, notion_page):
        notion_page(MIXED)
        dry = hm.heal_page("p", apply=False)
        notion_page(MIXED)
        assert hm.heal_page("p", apply=True) == dry


class TestNothingWrittenWouldBeFlaggedAgain:

    @pytest.mark.parametrize("blocks", [GENUINE, MIXED, [TEMPLATE] + GENUINE],
                             ids=["genuine", "mixed", "template-first"])
    def test_every_written_block_is_clean(self, notion_page, blocks):
        page = notion_page(blocks)
        hm.heal_page("p", apply=True)
        assert page.written(), "the fixture must exercise a write"
        assert not [b for b in page.written() if hm.is_mangled(b)]


class TestAStuckRunIsVisibleInTheJournal:

    def test_the_healer_names_the_stuck_blocks(self, monkeypatch, capsys):
        monkeypatch.setattr(hp, "_page", lambda pid: {"id": pid, "properties": {}})
        monkeypatch.setattr(hp, "_skipped_pages", lambda: set())
        for name in ("strip_furniture", "strip_backmatter", "clean_page",
                     "wrap_math_page", "heal_equations", "heal_fences",
                     "heal_figures", "heal_tables", "heal_pdf_media",
                     "heal_references", "heal_verify"):
            monkeypatch.setattr(hp, name, lambda *a, **k: {})
        monkeypatch.setattr(hp, "heal_mangled", lambda *a, **k: {
            "new_blocks": 0, "unconverged": 1,
            "runs": [{"blocks": [7, 8], "unconverged": True}]})
        hp.heal(["pg"], apply=True)
        out = capsys.readouterr()
        stuck = [ln for ln in out.err.splitlines()
                 if "pg:" in ln and "MANGLED-MATH" in ln]
        assert len(stuck) == 1 and "7" in stuck[0] and "8" in stuck[0]
        # Nothing was written, so the page is not counted as healed.
        assert "healed 0/1" in out.out


# --- One leftover Korean span must not cost the whole run ---------------------

# Swapped parses of the most common gloss shape, a formula followed by its Korean
# name with no space: `s_t(상태)`. wrap_math leaves the `(` inside the run, so the
# rebuild gets `$s_t($상태)`, and the paren balancer used to carry the run on to
# the `)` across the Korean, giving the equation `s_t(상태)`.
GLOSSED = [
    para(text("에이전트는 "), eq("관측을 받아 "), text("s_t(상태)"),
         eq("를 갱신한다 ")),
    para(text(r"\pi_\theta"), eq("는 정책이고 "), text(r"\gamma"),
         eq(r"는 할인율이다 \)")),
]


class TestAKoreanGlossStaysProse:

    def test_the_balancer_does_not_cross_korean(self):
        assert hm._balance_dollar_runs("$s_t($상태)를") == "$s_t($상태)를"

    def test_it_still_closes_a_formula_cut_at_the_comma(self):
        assert (hm._balance_dollar_runs(r"$\Theta_q(\mathbf{x}_q$, m) - ")
                == r"$\Theta_q(\mathbf{x}_q, m)$ - ")

    def test_a_run_with_a_gloss_is_repaired_and_stays_repaired(self, notion_page):
        page = notion_page([para(text("서론 문단입니다.")), GENUINE[1]] + GLOSSED
                           + [para(text("결론 문단입니다."))])
        rep = hm.heal_page("p", apply=True)
        assert rep["unconverged"] == 0 and rep["mangled_blocks"] == 3
        assert not any(hm.is_mangled(b) for b in page.fetch("p"))
        page.calls.clear()
        hm.heal_page("p", apply=True)
        assert page.calls == []


# A swapped block whose rebuild cannot converge: a Korean subscript outside text
# mode comes back as the equation `\mathcal{L}_{총}`, which is still flagged.
SUBSCRIPT = para(eq("손실 함수 "), text(r"\mathcal{L}_{총}"), eq("는 다음과 같다 "))


def archived_ids(page):
    return {p.rsplit("/", 1)[1] for _, p, b in page.calls if b.get("archived")}


class TestOneStuckBlockDoesNotHoldBackItsRun:
    """Consecutive mangled blocks are joined into one run, so a single block that
    cannot converge used to leave every block beside it unrepaired."""

    def test_soup_beside_the_template_is_still_repaired(self, notion_page):
        page = notion_page([para(text("앞 문단.")), GENUINE[1], GENUINE[2],
                            TEMPLATE, para(text("끝 문단."))])
        rep = hm.heal_page("p", apply=True)
        assert rep["mangled_blocks"] == 2 and rep["unconverged"] == 1
        assert archived_ids(page) == {"b1", "b2"}       # never the template, b3
        assert [r["blocks"] for r in rep["runs"] if r["unconverged"]] == [[3]]

    def test_a_korean_subscript_block_leaves_only_itself(self, notion_page):
        page = notion_page([GLOSSED[0], SUBSCRIPT, GLOSSED[1]])
        rep = hm.heal_page("p", apply=True)
        assert rep["mangled_blocks"] == 2 and rep["unconverged"] == 1
        assert archived_ids(page) == {"b0", "b2"}
        assert [r["blocks"] for r in rep["runs"] if r["unconverged"]] == [[1]]

    def test_the_split_run_converges(self, notion_page):
        page = notion_page([GENUINE[1], GENUINE[2], TEMPLATE] + GLOSSED)
        hm.heal_page("p", apply=True)
        assert page.written(), "the blocks beside the template must be written"
        assert not [b for b in page.written() if hm.is_mangled(b)]
        page.calls.clear()
        rep = hm.heal_page("p", apply=True)
        assert page.calls == [] and rep["unconverged"] == 1

    @pytest.mark.parametrize("apply", [False, True])
    def test_dry_run_reports_the_same_split(self, notion_page, apply):
        notion_page([GLOSSED[0], SUBSCRIPT, GLOSSED[1]])
        rep = hm.heal_page("p", apply=apply)
        assert [(r["blocks"], r["unconverged"]) for r in rep["runs"]] == [
            ([0], False), ([1], True), ([2], False)]


class TestHowARunIsSplit:
    """The split, against a stand-in for `the rebuild of these blocks converges`."""

    @staticmethod
    def split(run, good):
        return hm._converging_pieces(run, lambda r: tuple(r) in good)

    def test_a_converging_run_stays_whole(self):
        assert self.split([1, 2, 3], {(1, 2, 3)}) == [([1, 2, 3], True)]

    def test_the_stuck_block_is_cut_out(self):
        good = {(1,), (3,), (4,), (3, 4)}
        assert self.split([1, 2, 3, 4], good) == [
            ([1], True), ([2], False), ([3, 4], True)]

    def test_blocks_stuck_alone_are_tried_together(self):
        # A formula straddling a Notion split parses only when both halves join.
        assert self.split([1, 2, 3], {(1, 2), (3,)}) == [([1, 2], True), ([3], True)]

    def test_blocks_clean_alone_but_not_joined_go_one_by_one(self):
        assert self.split([1, 2], {(1,), (2,)}) == [([1], True), ([2], True)]

    def test_a_run_stuck_every_way_is_left_whole(self):
        assert self.split([1, 2], set()) == [([1, 2], False)]
