"""Where a figure goes when the body never mentions it.

Its anchor is the first mention of its number, and a figure the text never cites
— a teaser, or one whose citation the translation dropped — has none. Falling to
the end of the page puts it in the appendix while its neighbours sit chapters
earlier.
"""
import extract_paper_figures as ef


class TestFillingTheGaps:

    def test_an_uncited_figure_joins_the_one_before_it(self):
        resolved = ef.fill_anchor_gaps([1, 2, 3], {1: "a1", 2: None, 3: "a3"})
        assert resolved[2] == "a1"

    def test_it_prefers_the_nearest_one_before(self):
        resolved = ef.fill_anchor_gaps([1, 2, 3, 4],
                                       {1: "a1", 2: "a2", 3: None, 4: "a4"})
        assert resolved[3] == "a2"

    def test_the_first_figure_joins_the_one_after_it(self):
        # A teaser is cited by nothing and has no lower-numbered neighbour, so
        # looking only backwards left it at the page end — which is the appendix.
        resolved = ef.fill_anchor_gaps([1, 2, 3], {1: None, 2: "a2", 3: "a3"})
        assert resolved[1] == "a2"

    def test_it_prefers_the_nearest_one_after(self):
        resolved = ef.fill_anchor_gaps([1, 2, 3], {1: None, 2: None, 3: "a3"})
        assert resolved[1] == "a3" and resolved[2] == "a3"

    def test_a_placed_figure_is_left_where_it_is(self):
        resolved = ef.fill_anchor_gaps([1, 2], {1: "a1", 2: "a2"})
        assert resolved == {1: "a1", 2: "a2"}

    def test_with_nothing_placed_nothing_is_invented(self):
        # The page end remains the last resort; guessing a position for a figure
        # with no placed neighbour at all would be worse than admitting it.
        assert ef.fill_anchor_gaps([1, 2], {1: None, 2: None}) == {1: None, 2: None}

    def test_an_appendix_figure_does_not_borrow_a_body_figure(self):
        # `A.1` and `1` are different series; joining them would move an appendix
        # figure into the body.
        resolved = ef.fill_anchor_gaps(["1", "A.1"], {"1": "a1", "A.1": None})
        assert resolved["A.1"] is None


class TestJoiningFiguresAlreadyOnThePage:
    """When only the missing figures are being injected, the ones already placed
    are the only neighbours there are. Looking solely at the batch leaves a
    single uncited figure with nobody to sit next to, and it goes to the end."""

    # figure number -> index of its image block on the page
    PLACED = {2: 26, 4: 76, 5: 78}

    def test_it_follows_the_nearest_lower_figure(self):
        spot = ef.neighbour_spot(3, self.PLACED)
        assert spot == (26, "after")

    def test_the_first_figure_goes_before_the_nearest_higher_one(self):
        spot = ef.neighbour_spot(1, self.PLACED)
        assert spot == (26, "before")

    def test_a_figure_past_them_all_follows_the_last(self):
        assert ef.neighbour_spot(9, self.PLACED) == (78, "after")

    def test_with_no_figures_placed_there_is_no_spot(self):
        assert ef.neighbour_spot(3, {}) is None

    def test_a_number_already_placed_needs_no_spot(self):
        assert ef.neighbour_spot(4, self.PLACED) is None
