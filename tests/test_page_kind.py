"""Not every page in the research DB is a paper.

The owner keeps lecture pages in the same DB — a video at the top and the
narration script beneath it, filed under the venue `Learn`. The healer treated
one as a paper: the arXiv-link cleaner stripped the links from its reference
list, and the next steps in line would have promoted, deduplicated and even
resolved an arXiv id from its title and illustrated it with that paper's
figures. A page that is not a paper gets none of the paper repairs.
"""
import heal_paper_pages as hp
import page_kind


def page(venue=None):
    props = {"Paper Pages": {"type": "title", "title": [{"plain_text": "T"}]}}
    props["Journal, Conference"] = {
        "type": "select", "select": {"name": venue} if venue else None}
    return {"id": "p", "properties": props}


class TestTellingThemApart:

    def test_a_lecture_page_is_not_a_paper(self):
        assert not page_kind.is_paper(page("Learn"))

    def test_a_venue_is_a_paper(self):
        assert page_kind.is_paper(page("Nature"))

    def test_no_venue_is_still_a_paper(self):
        # Most pages are added before anyone fills the venue in.
        assert page_kind.is_paper(page())

    def test_a_page_without_the_property_is_a_paper(self):
        assert page_kind.is_paper({"id": "p", "properties": {}})


class TestTheHealerLeavesItAlone:

    def test_no_repair_runs_on_a_lecture_page(self, monkeypatch, capsys):
        ran = []
        monkeypatch.setattr(hp, "_page", lambda pid: page("Learn"))
        monkeypatch.setattr(hp, "_skipped_pages", lambda: set())
        for name in ("strip_furniture", "strip_backmatter", "clean_page",
                     "wrap_math_page", "heal_figures", "heal_verify"):
            monkeypatch.setattr(hp, name,
                                lambda *a, _n=name, **k: ran.append(_n) or {})
        hp.heal(["p"], apply=False)
        assert ran == []
        assert "not a paper" in capsys.readouterr().out

    def test_a_paper_is_still_healed(self, monkeypatch):
        ran = []
        monkeypatch.setattr(hp, "_page", lambda pid: page("Nature"))
        monkeypatch.setattr(hp, "_skipped_pages", lambda: set())
        monkeypatch.setattr(hp, "strip_furniture",
                            lambda *a, **k: ran.append("strip_furniture") or {})
        monkeypatch.setattr(hp, "strip_backmatter",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
        hp.heal(["p"], apply=False)
        assert ran == ["strip_furniture"]
