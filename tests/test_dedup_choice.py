"""Which copy of a duplicated section survives.

Keeping the richest copy exists to protect against a truncated one. But two
copies of the same re-uploaded section are about equally rich, so richness alone
decides by a hair — and it happily keeps the copy sitting in the wrong part of
the document while archiving the one in its proper place. Position is what
distinguishes them; richness only overrules it when the difference is real.
"""
import heal_verify as hv


def occurrence(chars, heading="Smooth power laws:"):
    return {"heading": heading, "heading_id": "h%d" % chars, "chars": chars,
            "key": None, "level": 3}


class TestChoosingWhichCopyToKeep:

    def test_the_one_in_its_proper_place_wins_a_close_call(self):
        first, later = occurrence(300), occurrence(310)
        assert hv.choose_kept([first, later]) is first

    def test_a_substantially_richer_copy_still_wins(self):
        # The reason keep-richest existed: a first copy cut short by a failed
        # upload must not be preferred just for being first.
        first, later = occurrence(40), occurrence(600)
        assert hv.choose_kept([first, later]) is later

    def test_an_empty_first_copy_never_wins(self):
        first, later = occurrence(0), occurrence(120)
        assert hv.choose_kept([first, later]) is later

    def test_the_earliest_wins_when_all_are_equal(self):
        first, second, third = occurrence(200), occurrence(200), occurrence(200)
        assert hv.choose_kept([first, second, third]) is first

    def test_one_copy_is_its_own_choice(self):
        only = occurrence(200)
        assert hv.choose_kept([only]) is only
