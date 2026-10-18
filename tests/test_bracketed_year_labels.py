"""A reference list labelled `Doe et al. [2021]` must be recognised as one.

natbib's author-year style puts the year in square brackets, so LaTeXML writes
the entry's tag as `Doe et al. [2021]`. Two halves disagreed about it:

  * the parser stripped EVERY leading and trailing bracket from the tag, the
    year's closing one included, so the list went out as `[Doe et al. [2021] …`;
  * the boundary refused any label holding a bracket at all.

Neither alone loses the list, but together they meant no entry was an entry. The
back-matter healer archived the list as a translated bibliography, the reference
healer found none and wrote it again, and that repeated on every cycle — each
edit keeping the page inside the window that brings the healers back.

The names below are invented.
"""
import time

import extract_paper_figures as ef
import link_references as lr
import reference_section as rs
import strip_backmatter as sb


def natbib_item(num, tag, authors, title):
    return (f'<li id="bib.bib{num}" class="ltx_bibitem">'
            f'<span class="ltx_tag ltx_role_refnum ltx_tag_bibitem">{tag}</span>\n'
            f'<span class="ltx_bibblock">\n{authors}\n</span>\n'
            f'<span class="ltx_bibblock">{title}\n</span></li>\n')


NATBIB = ('<ul class="ltx_biblist">\n'
          + natbib_item(1, "Doe et&nbsp;al. [2021]", "Jane Doe, Ann Two, and Bo Three.",
                        "A first invented title.")
          + natbib_item(2, "Roe and Poe [2019]", "Rick Roe and Pat Poe.",
                        "A second invented title.")
          + natbib_item(3, "Smith [2020a]", "Sam Smith.", "A third invented title.")
          + '</ul>\n')
LABELS = ["Doe et al. [2021]", "Roe and Poe [2019]", "Smith [2020a]"]


def rich(text):
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def block(bid, kind, text):
    return {"id": bid, "type": kind, kind: {"rich_text": rich(text)}}


def para(text):
    return block("p", "paragraph", text)


SENTENCE = "이 문장은 번역된 본문 한 문단을 흉내 낸 예시 문장이다. "


def body(sections=6, chars=1500):
    """A translated page: headings, each with a Korean paragraph of `chars`."""
    text = (SENTENCE * (chars // len(SENTENCE) + 1))[:chars]
    out = []
    for n in range(1, sections + 1):
        out += [block(f"h{n}", "heading_1", f"{n} Section {n} (섹션 {n})"),
                block(f"b{n}", "paragraph", text)]
    return out


class FakeNotion:
    """A page's top-level blocks, answering the calls the two healers make."""

    def __init__(self, blocks):
        self.blocks = [dict(b) for b in blocks]
        self.appends = 0
        self._made = 0

    def live(self, _page_id=None):
        return [b for b in self.blocks if not b.get("archived")]

    def api(self, method, path, body=None, **_):
        if method == "PATCH" and path.endswith("/children"):
            made = []
            for child in body["children"]:
                kind = child["type"]
                text = "".join(s["text"]["content"] for s in child[kind]["rich_text"])
                self._made += 1
                made.append(block(f"new{self._made}", kind, text))
            self.blocks += made
            self.appends += 1
            return {"results": made}
        target = next(b for b in self.blocks if b["id"] == path.rsplit("/", 1)[1])
        if body.get("archived"):
            target["archived"] = True
        else:
            target.update(body)
        return target


def written_entries(monkeypatch, entries):
    """The paragraph text `inject_references` writes for each entry."""
    fake = FakeNotion([])
    monkeypatch.setattr(lr, "notion", fake.api)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    lr.inject_references("page", entries, apply=True)
    return [rs.block_text(b) for b in fake.blocks[1:]]


class TestTheLabelKeepsItsYear:

    def test_the_year_keeps_its_closing_bracket(self):
        assert [e["label"] for e in lr.parse_bibliography(NATBIB)] == LABELS

    def test_only_one_enclosing_pair_is_removed(self):
        wrapped = natbib_item(1, "[Doe et al. [2021]]", "Jane Doe.", "A title.")
        assert lr.parse_bibliography(wrapped)[0]["label"] == "Doe et al. [2021]"

    def test_the_written_entry_opens_with_the_whole_label(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(NATBIB))
        assert texts[0].startswith("[Doe et al. [2021]] Jane Doe")


class TestTheBoundarySeesTheList:

    def test_every_written_entry_is_an_entry(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(NATBIB))
        assert all(rs.is_entry(para(t)) for t in texts)

    def test_the_list_has_a_span(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(NATBIB))
        page = body(2) + [block("ref", "heading_1", "References")]
        page += [block(f"e{i}", "paragraph", t) for i, t in enumerate(texts)]
        assert rs.list_spans(page) == [(4, 8)]

    def test_back_matter_stripping_leaves_it_alone(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(NATBIB))
        fake = FakeNotion(body() + [block("ref", "heading_1", "References")]
                          + [block(f"e{i}", "paragraph", t) for i, t in enumerate(texts)])
        monkeypatch.setattr(sb, "fetch_blocks", fake.live)
        monkeypatch.setattr(sb, "_api", fake.api)
        report = sb.strip_backmatter("page", apply=True)
        assert (report["backmatter_from"], report["archived"]) == (None, 0)


class TestTheGuardsStillHold:
    """Allowing an inner bracket must not let prose in. These pass on the old,
    stricter pattern too; they pin what the loosened one may not accept."""

    def test_an_aside_citing_something_mid_bracket_is_prose(self):
        assert not rs.is_entry(para("[see [12] below] for the derivation"))

    def test_an_inner_group_alone_is_not_a_label(self):
        assert not rs.is_entry(para("[[2021]] for the derivation"))

    def test_an_inner_group_with_no_digit_is_not_a_label(self):
        assert not rs.is_entry(para("[see [below]] for the derivation"))

    def test_a_long_label_is_refused_even_with_an_inner_year(self):
        assert not rs.is_entry(para(
            "[as the authors of that study conceded [2021]] it did not hold"))

    def test_a_translated_entry_is_still_refused(self):
        assert not rs.is_entry(para("[Doe et al. [2021]] 저자. 어떤 제목."))

    def test_the_mangled_form_is_not_an_entry(self):
        # A list already written as `[Doe et al. [2021] …` is NOT protected:
        # back-matter stripping removes it once and the injector writes it anew.
        assert not rs.is_entry(para("[Doe et al. [2021] Jane Doe. A title."))


class TestReadingTheListBack:
    """`ref_ids_from_texts` maps blocks already on the page to their entries by
    the label each opens with. It has to read the label exactly as written."""

    def test_every_written_entry_maps_to_its_block(self, monkeypatch):
        entries = [{"num": n, "label": label, "text": f"Author {n}. A title."}
                   for n, label in enumerate(LABELS, 1)]
        texts = written_entries(monkeypatch, entries)
        found = lr.ref_ids_from_texts({f"b{i}": t for i, t in enumerate(texts)}, entries)
        assert found == {1: "b0", 2: "b1", 3: "b2"}


NOW = "2099-01-01T00:00:00.000Z"    # never older than the healer's age gate


def wire(monkeypatch, fake, html):
    """Point both healers at `fake`, and the source fetch at `html`."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(sb, "fetch_blocks", fake.live)
    monkeypatch.setattr(sb, "_api", fake.api)
    monkeypatch.setattr(lr.vs, "fetch_blocks", fake.live)
    monkeypatch.setattr(lr, "notion", fake.api)
    monkeypatch.setattr(ef, "arxiv_id_from_page", lambda _p: "2401.12345")
    monkeypatch.setattr(ef, "fetch_html", lambda _a: (html, "https://example.org/"))


class TestTheHealerCycleSettles:
    """Back-matter stripping, then the reference healer, as one healer cycle runs
    them — against a page held in memory instead of Notion."""

    def cycle(self):
        return (sb.strip_backmatter("page", apply=True),
                lr.heal_references("page", apply=True, created_time=NOW))

    def test_a_list_written_once_stays(self, monkeypatch):
        fake = FakeNotion(body())
        wire(monkeypatch, fake, NATBIB)
        _, first = self.cycle()
        assert first["refs"] == "injected 3"
        for _ in range(3):
            stripped, healed = self.cycle()
            assert stripped["archived"] == 0
            assert healed["refs"] == "already present"
        assert fake.appends == 1

    def test_a_list_already_written_mangled_is_replaced_once(self, monkeypatch):
        # What the old parser wrote: the label lost its year's closing bracket.
        mangled = [block("old-ref", "heading_1", "References")] + [
            block(f"old{i}", "paragraph", f"[{label[:-1]}] Author {i}. A title.")
            for i, label in enumerate(LABELS)]
        fake = FakeNotion(body() + mangled)
        wire(monkeypatch, fake, NATBIB)
        stripped, healed = self.cycle()
        assert stripped["archived"] == len(mangled)
        assert healed["refs"] == "injected 3"
        stripped, healed = self.cycle()
        assert (stripped["archived"], healed["refs"]) == (0, "already present")
        live = [rs.block_text(b) for b in fake.live()]
        assert live[-3].startswith("[Doe et al. [2021]] ")


class TestAuthorYearCitationsAreLinked:
    """The body of a natbib paper cites `(Doe et al., 2021)`, and the index that
    links it is keyed by the name and year in each entry's label. The parser
    takes the label out of the entry's text, so an index read from the text was
    empty and not one such citation was ever linked."""

    def test_every_entry_is_indexed_by_its_label(self):
        index = lr.author_year_index(lr.parse_bibliography(NATBIB))
        assert index == {("doe et al", "2021"): 1, ("roe and poe", "2019"): 2,
                         ("smith", "2020a"): 3}

    def citations_linked(self, sentence):
        entries = lr.parse_bibliography(NATBIB)
        page = body(1) + [block("c", "paragraph", sentence)]
        return lr.link_author_year(
            "page", page, lr.author_year_index(entries),
            {e["num"]: f"ref{e['num']}" for e in entries},
            {e["num"]: e["text"] for e in entries}, apply=False)

    def test_a_citation_in_the_body_is_linked(self):
        assert self.citations_linked("앞선 연구 (Doe et al., 2021)에서 보였다.") == 1

    def test_a_non_breaking_space_in_the_body_still_matches(self):
        assert self.citations_linked("앞선 연구 (Doe et\xa0al., 2021)에서 보였다.") == 1

    def test_the_healer_links_them_when_it_writes_the_list(self, monkeypatch):
        cited = block("c", "paragraph", "앞선 연구 (Roe and Poe, 2019)를 따른다.")
        fake = FakeNotion(body() + [cited])
        wire(monkeypatch, fake, NATBIB)
        report = lr.heal_references("page", apply=True, created_time=NOW)
        assert (report["refs"], report["author_year_linked"]) == ("injected 3", 1)


def natbib_list(labels, author="Jane Doe{n}."):
    return ('<ul class="ltx_biblist">\n'
            + "".join(natbib_item(n, label, author.format(n=n), f"Invented title {n}.")
                      for n, label in enumerate(labels, 1))
            + '</ul>\n')


# Three labels the boundary refuses: 47 characters with the year, where a label
# may hold 40. Invented names; a consortium-style author list reaches that.
LONG = [f"Invented Consortium Number {n} Team et&nbsp;al. [20{10 + n}]" for n in (1, 2, 3)]
SOME_LONG = natbib_list(LONG + [f"Doe{n} et&nbsp;al. [20{10 + n}]" for n in range(4, 11)])


class TestTheWriterWritesOnlyWhatTheBoundaryReads:
    """`strip_backmatter` decides whether a list is ours by reading its labels;
    the injector's own guard reads only the heading. So any list with more than
    a fifth of its labels outside the boundary's grammar was archived and written
    again on every cycle. The writer now asks the boundary before it writes."""

    def test_the_label_check_agrees_with_the_boundary(self):
        for label in ["7", "ACDE12", "VSP + 17", "Doe et al. [2021]",
                      "Doe et al. (2021)", "Fou"]:
            assert rs.is_label(label), label
        for label in ["Invented Consortium Number 1 Team et al. [2011]",
                      "Doe [n.d.]", "Invented", "see below", "홍길동 [2020]"]:
            assert not rs.is_label(label), label

    def test_a_label_it_would_refuse_is_written_as_the_entry_number(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(SOME_LONG))
        assert texts[0].startswith("[1] Jane Doe1.")
        assert texts[3].startswith("[Doe4 et al. [2014]] Jane Doe4.")

    def test_every_written_entry_is_read_back(self, monkeypatch):
        entries = lr.parse_bibliography(SOME_LONG)
        texts = written_entries(monkeypatch, entries)
        found = lr.ref_ids_from_texts({f"b{i}": t for i, t in enumerate(texts)}, entries)
        assert len(found) == len(entries)

    def test_a_list_with_some_long_labels_settles(self, monkeypatch):
        fake = FakeNotion(body(30))
        wire(monkeypatch, fake, SOME_LONG)
        for _ in range(4):
            sb.strip_backmatter("page", apply=True)
            lr.heal_references("page", apply=True, created_time=NOW)
        assert fake.appends == 1
        assert rs.list_spans(fake.live()) == [(60, 71)]

    # An English bibliography that gives three authors' names in Hangul: no label
    # can make those entries readable, so the list cannot be written at all.
    HANGUL = natbib_list([f"Doe{n} et&nbsp;al. [20{10 + n}]" for n in range(1, 11)],
                         author="Jane Doe{n} and 홍길동.")

    def test_a_list_the_boundary_cannot_read_is_never_written(self, monkeypatch):
        fake = FakeNotion(body(30))
        wire(monkeypatch, fake, self.HANGUL)
        for _ in range(4):
            sb.strip_backmatter("page", apply=True)
            report = lr.heal_references("page", apply=True, created_time=NOW)
        assert (fake.appends, report["refs"]) == (0, "not written")

    def test_force_does_not_trade_a_list_for_nothing(self, monkeypatch):
        texts = written_entries(monkeypatch, lr.parse_bibliography(NATBIB))
        fake = FakeNotion(body() + [block("ref", "heading_1", "References")]
                          + [block(f"e{i}", "paragraph", t) for i, t in enumerate(texts)])
        wire(monkeypatch, fake, self.HANGUL)
        assert lr.link_page("page", apply=True, force=True)["refs"] == "not written"
        assert len(rs.list_spans(fake.live())) == 1

    def test_the_injector_refuses_it_whoever_calls_it(self, monkeypatch):
        import pytest
        for html in (self.HANGUL, natbib_list(["Doe [2021]", "Roe [2022]"])):
            with pytest.raises(lr.UnreadableList):
                written_entries(monkeypatch, lr.parse_bibliography(html))
