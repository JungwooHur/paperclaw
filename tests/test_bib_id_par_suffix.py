r"""Bibliography ids that carry a literal `\par`, and a failure that said nothing.

Some arXiv-native renders write every entry id as `bib.bib7\par` and the body's
anchors to it as `#bib.bib7%5Cpar`, the same suffix URL-encoded. The ar5iv copy
of the same paper writes plain `bib.bib7`. Every pattern that knew only the
plain form read such a source as one with no bibliography, so the page got no
reference list and no linked citation.

Nobody heard about it either: the healer reads only `slots_linked` from
`heal_references`, and a page whose source could not be read reported zero,
exactly like a page with nothing to do.
"""
import datetime

import pytest

import clean_source_urls as csu
import collect_papers
import extract_paper_figures as ef
import link_references as lr
import verify_citations as vc


def bibitem(n, label, text, suffix="\\par", x=""):
    """One LaTeXML bibliography entry, its id written with `suffix`."""
    return (f'<li id="bib.bib{x}{n}{suffix}" class="ltx_bibitem">'
            f'<span class="ltx_tag ltx_role_refnum ltx_tag_bibitem">[{label}]</span>'
            f'<span class="ltx_bibblock">{text}</span></li>\n')


def cite(n, shown, suffix="%5Cpar"):
    """One in-body anchor to entry `n`, showing `shown`."""
    return f'<a href="#bib.bib{n}{suffix}" class="ltx_ref">{shown}</a>'


def source(items, body=""):
    return ('<article class="ltx_document">'
            '<section id="S1" class="ltx_section">'
            '<h2 class="ltx_title ltx_title_section">1 Introduction</h2>'
            f'<p class="ltx_p">{body}</p></section>'
            '<section id="bib" class="ltx_bibliography"><ul class="ltx_biblist">\n'
            + "".join(items) + '</ul></section></article>')


ENTRIES = [(1, "A. Author. A first title. In Venue, 2020."),
           (2, "B. Author. A second title. In Venue, 2021."),
           (3, "C. Author. A third title. In Venue, 2022.")]

# The shape the bug was found in: `\par` on every id, `%5Cpar` on every anchor.
PAR = source([bibitem(n, n, t) for n, t in ENTRIES],
             body=f"Prior work [{cite(2, 2)}, {cite(1, 1)}] and later [{cite(3, 3)}].")


class TestTheParSuffixIsRead:

    def test_every_entry_is_read(self):
        assert [e["text"] for e in lr.parse_bibliography(PAR)] == [t for _, t in ENTRIES]

    def test_entry_numbers_are_ints(self):
        entries = lr.parse_bibliography(PAR)
        assert [e["num"] for e in entries] == [1, 2, 3]
        assert [e["id_num"] for e in entries] == [1, 2, 3]
        assert all(type(e["num"]) is int and type(e["id_num"]) is int
                   for e in entries)

    def test_the_suffix_is_not_left_in_the_entry_or_its_label(self):
        for e in lr.parse_bibliography(PAR):
            assert "par" not in e["label"] and "\\" not in e["text"]

    def test_the_bodys_anchors_resolve_through_the_encoded_suffix(self):
        assert lr.source_citation_sequence(PAR) == {"1": [2, 1, 3]}

    def test_the_paragraph_reading_resolves_them_too(self):
        assert lr.source_citation_paragraphs(PAR) == {"1": [[2, 1, 3]]}

    def test_a_lowercase_escape_is_the_same_suffix(self):
        # Percent-escapes are case-insensitive; `%5c` is as valid as `%5C`.
        html = source([bibitem(n, n, t) for n, t in ENTRIES],
                      body=f"See [{cite(3, 3, '%5cpar')}].")
        assert lr.source_citation_sequence(html) == {"1": [3]}

    def test_an_anchor_maps_to_the_label_not_the_id(self):
        # The suffix must not break the id -> cited-number mapping: `bib.bib15`
        # can be `[1]`.
        html = source([bibitem(15, 1, ENTRIES[0][1]), bibitem(20, 2, ENTRIES[1][1]),
                       bibitem(3, 3, ENTRIES[2][1])],
                      body=f"[{cite(20, 2)}] [{cite(15, 1)}]")
        assert lr.source_citation_sequence(html) == {"1": [2, 1]}


class TestTheOtherShapesAreUnchanged:

    def test_plain_ids_and_anchors(self):
        html = source([bibitem(n, n, t, suffix="") for n, t in ENTRIES],
                      body=f"[{cite(2, 2, '')}, {cite(1, 1, '')}]")
        assert [e["num"] for e in lr.parse_bibliography(html)] == [1, 2, 3]
        assert lr.source_citation_sequence(html) == {"1": [2, 1]}

    def test_alphabetic_ids(self):
        html = source([bibitem(1, "ACDE12", ENTRIES[0][1], suffix="", x="x"),
                       bibitem(2, "VSP + 17", ENTRIES[1][1], suffix="", x="x")])
        entries = lr.parse_bibliography(html)
        assert [(e["num"], e["label"]) for e in entries] == [(1, "ACDE12"),
                                                            (2, "VSP + 17")]

    def test_alphabetic_ids_with_the_suffix(self):
        html = source([bibitem(7, "ACDE12", ENTRIES[0][1], x="x")],
                      body=f'[<a href="#bib.bibx7%5Cpar">ACDE12</a>]')
        assert [e["id_num"] for e in lr.parse_bibliography(html)] == [7]
        assert lr.source_citation_sequence(html) == {"1": [7]}

    def test_an_unknown_suffix_is_still_not_an_id(self):
        # Only `\par` is accepted; anything else after the number stays unread,
        # which is what `heal_references` then reports.
        html = source([bibitem(n, n, t, suffix="\\relax") for n, t in ENTRIES])
        assert lr.parse_bibliography(html) == []


class TestTheLeakedParIsDroppedFromTheEntry:
    """The render that suffixes its ids also leaves `\\par` in the entry text.

    It follows each accented letter of a name, `Exä\\parmplé\\par`, and went into
    the injected list as written. The ar5iv copy of the same paper has none.
    """

    def test_an_accented_name_is_read_whole(self):
        html = source([bibitem(1, 1, "Q. Exä\\parmplé\\par. A title. In Venue, 2020.")])
        assert lr.parse_bibliography(html)[0]["text"] == (
            "Q. Exämplé. A title. In Venue, 2020.")

    def test_and_so_is_an_author_year_label(self):
        html = source([bibitem(1, "Exä\\parmplé\\par et al. (2020)", ENTRIES[0][1])])
        assert lr.parse_bibliography(html)[0]["label"] == "Exämplé et al. (2020)"

    def test_a_formulas_own_commands_are_kept(self):
        # `\partial` and `\parallel` start with the same four letters.
        text = "On \\partial and \\parallel flows. 2020."
        assert lr.parse_bibliography(source([bibitem(1, 1, text)]))[0]["text"] == text

    def test_a_render_without_the_suffix_is_read_as_before(self):
        # Nothing says a `\par` there is not the paper's own TeX.
        text = "Set in a \\parbox. 2020."
        html = source([bibitem(1, 1, text, suffix="")])
        assert lr.parse_bibliography(html)[0]["text"] == text


FOUR = ENTRIES + [(4, "D. Author. A fourth title. In Venue, 2023.")]


def two_sections(second):
    """Section 1 with a plain id citing [1], [2]; section 2 with id `second`.

    Section 2 cites [3] itself and [4] from a subsection whose id carries the
    same suffix, as the render writes it.
    """
    sub = second.replace("S2", "S2.SS1", 1)
    return ('<article class="ltx_document">'
            '<section id="S1" class="ltx_section">'
            '<h2 class="ltx_title ltx_title_section">1 Introduction</h2>'
            f'<p class="ltx_p">[{cite(1, 1)}], [{cite(2, 2)}]</p></section>'
            f'<section id="{second}" class="ltx_section">'
            '<h2 class="ltx_title ltx_title_section">2 Method</h2>'
            f'<p class="ltx_p">[{cite(3, 3)}]</p>'
            f'<section id="{sub}" class="ltx_subsection">'
            '<h3 class="ltx_title ltx_title_subsection">2.1 Detail</h3>'
            f'<p class="ltx_p">[{cite(4, 4)}]</p></section></section>'
            '<section id="bib" class="ltx_bibliography"><ul class="ltx_biblist">\n'
            + "".join(bibitem(n, n, t) for n, t in FOUR)
            + '</ul></section></article>')


class TestSuffixedSectionIds:
    """The render that suffixes the bibliography's ids suffixes every other id too.

    A section whose id the reader missed did not end the section before it, so
    every citation inside it was credited to the last section that was read.
    """

    @pytest.mark.parametrize("second", ["S2", "S2\\par"])
    def test_each_section_keeps_its_own_citations(self, second):
        assert lr.source_citation_sequence(two_sections(second)) == {
            "1": [1, 2], "2": [3, 4]}

    @pytest.mark.parametrize("second", ["S2", "S2\\par"])
    def test_and_its_own_paragraphs(self, second):
        assert lr.source_citation_paragraphs(two_sections(second)) == {
            "1": [[1, 2]], "2": [[3], [4]]}

    @pytest.mark.parametrize("second", ["S2", "S2\\par"])
    def test_the_page_links_both_sections(self, offline, monkeypatch, second):
        offline["html"] = two_sections(second)
        page = [blk("h1", "heading_1", "1 서론 (Introduction)"),
                blk("p1", "paragraph", "선행 연구 [1], [2]."),
                blk("h2", "heading_1", "2 방법 (Method)"),
                blk("p2", "paragraph", "이 방법 [3]."),
                blk("h3", "heading_2", "2.1 세부 (Detail)"),
                blk("p3", "paragraph", "세부 사항 [4].")]
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: page)
        rep = lr.link_page("p")
        assert rep["sections_linked"] == 2
        assert rep["would_link"] == {"1": 2, "2": 2}


class TestTheStyleClassifierReadsTheSuffix:
    """verify_citations decides numeric vs author-year from the body's anchors.

    A natbib list labels its entries `Doe et al. [2021]` while the body cites by
    number, so only the anchors say this paper is numeric. Unread anchors made it
    author-year, and `--apply` on that verdict strips every numeric marker.
    """

    NATBIB = source(
        [bibitem(n, f"Doe{n} et al. [202{n}]", f"Doe{n}. A title {n}.")
         for n in range(1, 7)],
        body=" ".join(f"[{cite(n, n)}]" for n in range(1, 7)))

    def test_a_numerically_cited_paper_is_numeric(self):
        assert vc.classify_bibliography(self.NATBIB)[0] == "numeric"

    def test_the_entry_count_comes_from_the_suffixed_ids(self):
        assert vc.classify_bibliography(self.NATBIB) == ("numeric", 6)

    def test_an_author_year_body_is_still_author_year(self):
        html = source([bibitem(n, f"Doe{n} et al. [202{n}]", f"Doe{n}. A title {n}.")
                       for n in range(1, 7)],
                      body=" ".join(cite(n, f"Doe{n} et al.") for n in range(1, 7)))
        assert vc.classify_bibliography(html) == ("author-year", None)

    # The same render ends every anchor's visible text in `\par\par`. That made
    # a name too long to be read as one, and a number not a number, which turns
    # a numeric paper author-year: the verdict that strips its markers.
    LEAKED = "\\par\\par"

    def test_a_number_that_ends_in_the_leaked_par_is_still_a_number(self):
        html = source([bibitem(n, f"Doe{n} et al. [202{n}]", f"Doe{n}. A title {n}.")
                       for n in range(1, 7)],
                      body=" ".join(f"[{cite(n, str(n) + self.LEAKED)}]"
                                    for n in range(1, 7)))
        assert vc.classify_bibliography(html) == ("numeric", 6)

    def test_a_name_that_ends_in_it_is_still_a_name(self):
        html = source([bibitem(n, f"Doe{n} et al. [202{n}]", f"Doe{n}. A title {n}.")
                       for n in range(1, 7)],
                      body=" ".join(cite(n, f"Doe{n} et al. (202{n})" + self.LEAKED)
                                    for n in range(1, 7)))
        assert vc.classify_bibliography(html) == ("author-year", None)


class TestLeakedCitationUrlsWithTheSuffix:
    """A flattened anchor leaks into the body as `N <url>#bib.bibN`."""

    @staticmethod
    def group(suffix):
        return (f"이전 연구 [ 1 https://arxiv.org/html/xxxx#bib.bib1{suffix} , "
                f"2 https://arxiv.org/html/xxxx#bib.bib2{suffix} ]는 그렇다.")

    def test_the_plain_form_is_removed_whole(self):
        out = csu.strip_citation_urls(self.group(""))
        assert out.startswith("이전 연구") and out.endswith("는 그렇다.")
        assert "http" not in out and "[" not in out and "]" not in out

    def test_a_suffixed_group_is_removed_exactly_like_a_plain_one(self):
        assert (csu.strip_citation_urls(self.group("%5Cpar"))
                == csu.strip_citation_urls(self.group("")))


# --- the failure is heard -----------------------------------------------------

def rich(text):
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def blk(bid, kind, text):
    return {"id": bid, "type": kind, kind: {"rich_text": rich(text)}}


REFERENCE_LIST = [blk("h", "heading_1", "References")] + [
    blk(f"r{n}", "paragraph", f"[{n}] {t}") for n, t in ENTRIES]


@pytest.fixture
def offline(monkeypatch):
    """A translated page whose source is whatever the test sets."""
    def no_writes(*_a, **_k):
        raise AssertionError("wrote to Notion")
    monkeypatch.setattr(lr, "notion", no_writes)
    monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: [])
    monkeypatch.setattr(ef, "_has_body_to_anchor", lambda _: True)
    monkeypatch.setattr(collect_papers, "classify_translation", lambda _: "TRANSLATED")
    monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: "xxxx")
    state = {"html": PAR}
    monkeypatch.setattr(ef, "html_renders", lambda _: iter([(state["html"], "src")]))
    return state


def heal(page="p"):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return lr.heal_references(page, created_time=now)


class TestAnUnreadableBibliographyIsReported:

    def test_entries_the_parser_cannot_read_are_not_called_no_bibliography(
            self, offline):
        # "no bibliography in source" reads as a paper without references; this
        # is a parser that could not see them.
        offline["html"] = source([bibitem(n, n, t, suffix="\\relax") for n, t in ENTRIES])
        rep = lr.link_page("p")
        assert rep["entries"] == 0 and rep["bibitems"] == 3
        assert rep["error"] != "no bibliography in source"

    def test_the_healer_prints_it(self, offline, capsys):
        offline["html"] = source([bibitem(n, n, t, suffix="\\relax") for n, t in ENTRIES])
        rep = heal("page-1")
        err = capsys.readouterr().err
        assert f"page-1: REFS error {rep['error']}" in err
        assert len(err.strip().splitlines()) == 1

    def test_a_source_with_no_entries_at_all_is_printed_too(self, offline, capsys):
        # An HTML render with figures and no bibliography: native-latest has been
        # seen to drop the list that v1 carries.
        offline["html"] = figure(source([]))
        rep = heal()
        assert rep["error"] == "no bibliography in source"
        assert "p: REFS error" in capsys.readouterr().err

    def test_any_other_error_is_printed_and_the_report_returned(
            self, monkeypatch, capsys):
        rep = {"page": "p", "entries": 0, "error": "something new went wrong"}
        monkeypatch.setattr(lr, "link_page", lambda *_a, **_k: rep)
        assert heal() is rep
        assert "p: REFS error something new went wrong" in capsys.readouterr().err

    def test_the_par_source_now_parses_and_says_nothing(self, offline, capsys):
        rep = heal()
        assert rep["entries"] == 3 and "error" not in rep
        assert capsys.readouterr().err == ""


class TestStatesThatAreNotFaultsStayQuiet:
    """Each is true of a healthy page and stays true every cycle."""

    def test_a_page_whose_translation_has_not_arrived(
            self, offline, monkeypatch, capsys):
        monkeypatch.setattr(collect_papers, "classify_translation", lambda _: "PARTIAL")
        assert heal()["deferred"]
        assert capsys.readouterr().err == ""

    def test_a_page_that_already_has_its_list(self, offline, monkeypatch, capsys):
        monkeypatch.setattr(lr.vs, "fetch_blocks", lambda _: REFERENCE_LIST)
        assert heal()["refs"] == "already present"
        assert capsys.readouterr().err == ""

    def test_a_pdf_only_paper(self, offline, monkeypatch, capsys):
        monkeypatch.setattr(ef, "html_renders", lambda _: iter([]))
        assert heal()["error"] == "no HTML source"
        assert capsys.readouterr().err == ""

    def test_a_page_that_answers_with_neither_figure_nor_list(
            self, offline, capsys):
        # What ar5iv serves, with a 200, for a paper it could not convert.
        offline["html"] = '<html><body><p>Conversion failed.</p></body></html>'
        assert heal()["error"] == "no HTML source"
        assert capsys.readouterr().err == ""

    def test_a_paper_with_no_arxiv_source(self, offline, monkeypatch, capsys):
        monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _: None)
        assert heal()["error"] == "no arxiv id"
        assert capsys.readouterr().err == ""

    def test_a_page_too_old_for_the_healer(self, monkeypatch, capsys):
        # The age gate is `_heal_references`' own decision, and how it dates a
        # page is not this test's business: only that a skip says nothing.
        rep = {"page": "p", "skipped": "page is 40d old"}
        monkeypatch.setattr(lr, "_heal_references", lambda *_a, **_k: rep)
        assert heal() is rep
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("rep", [
        {"page": "p", "deferred": "the translation is not on the page yet"},
        {"page": "p", "refs": "already present"},
        {"page": "p", "error": "no HTML source"},
        {"page": "p", "error": "no arxiv id"},
    ])
    def test_the_same_reports_handed_back_directly(self, monkeypatch, capsys, rep):
        monkeypatch.setattr(lr, "link_page", lambda *_a, **_k: rep)
        assert heal() is rep
        assert capsys.readouterr().err == ""


# --- which render is read -----------------------------------------------------

def figure(html):
    return html.replace('<section id="bib"',
                        '<figure id="S1.F1" class="ltx_figure"></figure><section id="bib"')


class TestARenderWithNoFigureStillHasItsBibliography:
    """`ef.fetch_html` takes a render only if it has a `<figure`.

    That is the figure injector's need, and the reference linker borrowed it. A
    paper with no figure therefore read as "no HTML source", the state kept quiet
    as a PDF-only paper, and never got its list.
    """

    @staticmethod
    def renders(monkeypatch, *pages):
        pulled = []

        def gen(_):
            for i, page in enumerate(pages):
                pulled.append(i)
                yield page, f"render-{i}"
        monkeypatch.setattr(ef, "html_renders", gen)
        return pulled

    def test_its_bibliography_is_read(self, offline, monkeypatch, capsys):
        self.renders(monkeypatch, PAR)
        rep = heal()
        assert rep["entries"] == 3 and "error" not in rep
        assert capsys.readouterr().err == ""

    def test_a_render_with_a_figure_is_still_the_one_read(self, monkeypatch):
        # As before: the render the figures come from, when there is one.
        with_figure = figure(source([bibitem(1, 1, ENTRIES[0][1])]))
        self.renders(monkeypatch, PAR, with_figure)
        assert lr.fetch_source("xxxx") == (with_figure, "render-1")

    def test_the_first_render_with_a_figure_ends_the_fetch(self, monkeypatch):
        # One request for a paper whose native render is whole, every cycle.
        pulled = self.renders(monkeypatch, figure(PAR), PAR)
        assert lr.fetch_source("xxxx")[1] == "render-0"
        assert pulled == [0]

    def test_the_figure_injector_still_needs_a_figure(self, monkeypatch):
        self.renders(monkeypatch, PAR)
        assert ef.fetch_html("xxxx") == (None, None)
        self.renders(monkeypatch, PAR, figure(PAR))
        assert ef.fetch_html("xxxx") == (figure(PAR), "render-1")

    def test_the_figure_render_is_whatever_fetch_html_returns(self, monkeypatch):
        # The list and the figures share a source because both ask `ef.fetch_html`,
        # and a test that stubs only that keeps the linker off the network.
        monkeypatch.setattr(ef, "fetch_html", lambda _a: (PAR, "stub"))

        def walk_again(_a):
            raise AssertionError("fetched the renders a second time")
        monkeypatch.setattr(ef, "html_renders", walk_again)
        assert lr.fetch_source("xxxx") == (PAR, "stub")

    def test_with_no_figure_anywhere_the_latest_list_is_read(self, monkeypatch):
        # The figures then come from the PDF, the latest version. The list is the
        # native render's, also the latest, and ar5iv's (often v1) only when the
        # native render has none.
        ar5iv = source([bibitem(1, 1, ENTRIES[0][1])])
        self.renders(monkeypatch, PAR, ar5iv)
        assert lr.fetch_source("xxxx") == (PAR, "render-0")
        self.renders(monkeypatch, "<html><body></body></html>", ar5iv)
        assert lr.fetch_source("xxxx") == (ar5iv, "render-1")
