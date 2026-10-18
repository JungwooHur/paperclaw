"""An old DB row translated today is a new paper.

The healer places a reference list only on pages built in the last week, and it
dated a page by the page's own `created_time`. But a row in the research DB is
often a reading-list entry added months before the paper is sent, and the paper
is translated into that row. Such a page is a week-old translation on a
months-old page, so it was skipped; it had never been cleared (an empty page has
nothing to clear), so the clear-record exception did not cover it either.

The translation is dated by the headings and paragraphs this integration wrote
and no person has edited since. The owner's own blocks say nothing about when
the paper was translated, in either direction.
"""
import datetime

import collect_papers as cp
import link_references as lr

BOT = {"object": "user", "id": "bot-1"}
PERSON = {"object": "user", "id": "person-1"}
NOW = datetime.datetime.now(datetime.timezone.utc)


def stamp(days_ago):
    """A Notion timestamp `days_ago` days back."""
    when = NOW - datetime.timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def heading(bid, days_ago, who=BOT, edited_by=None, kind="heading_1"):
    text = f"{bid} Section (섹션)"
    return {"id": bid, "type": kind, "created_time": stamp(days_ago),
            kind: {"rich_text": [{"type": "text", "text": {"content": text},
                                  "plain_text": text}]},
            "created_by": who, "last_edited_by": edited_by or who}


def paragraph(bid, days_ago, who=BOT):
    return {"id": bid, "type": "paragraph", "created_time": stamp(days_ago),
            "paragraph": {"rich_text": []},
            "created_by": who, "last_edited_by": who}


def callout(bid, days_ago, who=BOT):
    """A saved Q&A, which this integration also writes."""
    return {"id": bid, "type": "callout", "created_time": stamp(days_ago),
            "callout": {"rich_text": []},
            "created_by": who, "last_edited_by": who}


OLD_ROW = stamp(229)


class Heal:
    """Run `heal_references` with Notion replaced by the given blocks."""

    def __init__(self, monkeypatch, tmp_path, blocks, cleared=False):
        self.fetches = 0
        self.linked = None
        monkeypatch.setattr(cp, "CLEARED_DIR", str(tmp_path))
        if cleared:
            (tmp_path / "abc123-1700000000.json").write_text("{}")
        monkeypatch.setattr(cp, "_me_id", lambda: BOT["id"])

        def fetch(_):
            self.fetches += 1
            return blocks
        monkeypatch.setattr(lr.vs, "fetch_blocks", fetch)

        def link(pid, apply=False, blocks=None):
            self.linked = {"blocks": blocks}
            return {"linked": pid}
        monkeypatch.setattr(lr, "link_page", link)

    def run(self, created_time):
        return lr.heal_references("abc-123", created_time=created_time)


class TestAnOldRowTranslatedNow:

    def test_it_gets_its_list(self, monkeypatch, tmp_path):
        blocks = [heading("h1", 0), paragraph("p1", 0), heading("h2", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert heal.run(OLD_ROW) == {"linked": "abc-123"}

    def test_the_page_is_read_once(self, monkeypatch, tmp_path):
        # The healer visits every recently edited page every cycle, so dating the
        # translation must not cost a second read of the page.
        blocks = [heading("h1", 1)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        heal.run(OLD_ROW)
        assert heal.fetches == 1
        assert heal.linked["blocks"] is blocks

    def test_an_old_heading_of_the_owners_does_not_make_it_look_old(self, monkeypatch, tmp_path):
        # The row the owner annotated months ago, then the paper translated below.
        blocks = [heading("note", 229, who=PERSON), heading("h1", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert heal.run(OLD_ROW) == {"linked": "abc-123"}

    def test_an_older_saved_answer_does_not_date_it(self, monkeypatch, tmp_path):
        # A question was answered on the row weeks before the paper was sent.
        blocks = [callout("qa", 19), heading("h1", 0), paragraph("p1", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert heal.run(OLD_ROW) == {"linked": "abc-123"}

    def test_the_window_still_applies_to_the_translation(self, monkeypatch, tmp_path):
        blocks = [heading("h1", lr.HEAL_MAX_AGE_DAYS + 2)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert "skipped" in heal.run(OLD_ROW)


class TestAnOldTranslationStaysSkipped:

    def test_a_fresh_heading_of_the_owners_does_not_make_it_look_new(self, monkeypatch, tmp_path):
        blocks = [heading("h1", 200), paragraph("p1", 200), heading("h2", 200),
                  heading("mine", 0, who=PERSON)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        rep = heal.run(OLD_ROW)
        assert "skipped" in rep and heal.linked is None

    def test_a_heading_a_person_edited_is_theirs(self, monkeypatch, tmp_path):
        # Written by the integration, then retitled by the owner today: its
        # created_time is the integration's, but the block now reads as theirs.
        blocks = [heading("h1", 200), heading("h2", 0, edited_by=PERSON)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert "skipped" in heal.run(OLD_ROW)

    def test_the_earliest_heading_dates_it(self, monkeypatch, tmp_path):
        # The healers add headings of their own to an old page (a split heading,
        # a promoted title); the newest one is not when the paper arrived.
        blocks = [heading("h1", 200), heading("split", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert "skipped" in heal.run(OLD_ROW)

    def test_promoted_titles_do_not_make_it_look_new(self, monkeypatch, tmp_path):
        # Every title was a paragraph and the healer promoted them all today: each
        # heading is a new block, and only the paragraphs keep the old date.
        blocks = [heading("t1", 0), paragraph("p1", 200), heading("t2", 0),
                  paragraph("p2", 200)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        rep = heal.run(OLD_ROW)
        assert "skipped" in rep and heal.linked is None

    def test_a_paragraph_a_person_edited_does_not_date_it(self, monkeypatch, tmp_path):
        # The owner rewrote an old paragraph of ours; it no longer reads as ours,
        # so the fresh translation around it is not made to look old.
        edited = paragraph("p0", 200)
        edited["last_edited_by"] = PERSON
        blocks = [edited, heading("h1", 0), paragraph("p1", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert heal.run(OLD_ROW) == {"linked": "abc-123"}

    def test_no_heading_of_ours_means_skip(self, monkeypatch, tmp_path):
        blocks = [heading("mine", 0, who=PERSON), paragraph("p1", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        assert "skipped" in heal.run(OLD_ROW)

    def test_an_unknown_integration_id_means_skip(self, monkeypatch, tmp_path):
        blocks = [heading("h1", 0)]
        heal = Heal(monkeypatch, tmp_path, blocks)
        monkeypatch.setattr(cp, "_me_id", lambda: None)
        assert "skipped" in heal.run(OLD_ROW)


class TestWhatDidNotChange:

    def test_a_new_page_is_healed_without_reading_its_blocks(self, monkeypatch, tmp_path):
        heal = Heal(monkeypatch, tmp_path, [])
        assert heal.run(stamp(1)) == {"linked": "abc-123"}
        assert heal.fetches == 0 and heal.linked["blocks"] is None

    def test_a_cleared_page_is_healed_without_reading_its_blocks(self, monkeypatch, tmp_path):
        heal = Heal(monkeypatch, tmp_path, [], cleared=True)
        assert heal.run(OLD_ROW) == {"linked": "abc-123"}
        assert heal.fetches == 0


class TestLinkPageTakesTheBlocks:

    def test_given_blocks_are_not_fetched_again(self, monkeypatch):
        def no_fetch(_):
            raise AssertionError("fetched blocks it was handed")
        monkeypatch.setattr(lr.vs, "fetch_blocks", no_fetch)
        # No body yet, so it defers before any source is read.
        assert lr.link_page("p", apply=False, blocks=[heading("h1", 0)])["deferred"]
