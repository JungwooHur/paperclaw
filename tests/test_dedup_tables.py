"""Repeated copies of one table image are removed, and nothing else is.

The table injector, unable to recognise its own output, placed the same tables
again on every healer cycle, and the copies outlived the fix: the runaway guard
stopped the growth, but nothing took the pile away, and the guard then refused the
page for good. One image per table caption is the only correct state, so every
later copy goes and the first stays.

Figures are left alone. A composite figure's panels each carry the figure's
caption, so a repeated figure caption is normal — and the guard used to count
those too, refusing such a page so that its tables were never placed.

A forced table run is the other way copies pile up, so its replace step is pinned
here too: it archives what the new images stand in for, and nothing else.
"""
import dedup_tables as dt
import extract_paper_tables as ept


def image(bid, caption, name="table_0.png", kind="file"):
    url = f"https://files.example/abc/{bid}/{name}?X-Amz-Signature=sig-{bid}"
    return {"id": bid, "type": "image",
            "image": {"type": kind, kind: {"url": url},
                      "caption": [{"plain_text": caption}] if caption else []}}


def para(bid, text="본문"):
    return {"id": bid, "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def without(blocks, ids):
    ids = set(ids)
    return [b for b in blocks if b["id"] not in ids]


class TestWhatCountsAsTheSameTable:

    def test_spacing_and_case_in_the_caption_do_not_matter(self):
        assert (dt.table_key(image("a", "TABLE I:  Results\n"))
                == dt.table_key(image("b", "table i: results")))

    def test_the_file_name_does_not_matter(self):
        # Tables are uploaded as table_<i> numbered per run: one table placed by
        # two runs that rendered different sets carries two names.
        assert (dt.table_key(image("a", "Table A: Hyperparameters", "table_3.png"))
                == dt.table_key(image("b", "Table A: Hyperparameters", "table_0.png")))

    def test_figures_and_uncaptioned_images_have_no_key(self):
        assert dt.table_key(image("a", "Figure 2: Samples", "tmpab.png")) is None
        assert dt.table_key(image("b", "(a)", "tmpcd.png")) is None
        assert dt.table_key(image("c", "")) is None
        assert dt.table_key(para("p")) is None


class TestWhichCopiesGo:

    def test_every_later_copy_goes_and_the_first_stays(self):
        blocks = [para("p1"), image("t0", "TABLE I: A"), para("p2"),
                  image("t1", "TABLE II: B", "table_1.png"),
                  image("t0b", "TABLE I: A"), image("t1b", "TABLE II: B", "table_1.png"),
                  image("t0c", "TABLE I: A")]
        assert dt.duplicate_tables(blocks) == ["t0b", "t1b", "t0c"]

    def test_a_page_that_ran_away_keeps_one_of_each(self):
        captions = [f"TABLE {n}: caption {n}" for n in ("I", "II", "III", "IV", "V")]
        blocks = [image(f"{i}-{k}", c, f"table_{k}.png")
                  for i in range(55) for k, c in enumerate(captions)]
        extra = dt.duplicate_tables(blocks)
        assert len(extra) == 54 * 5
        assert [b["id"] for b in without(blocks, extra)] == [f"0-{k}" for k in range(5)]

    def test_copies_under_different_file_names_still_collapse(self):
        blocks = [image("first", "Table A: Hyperparameters", "table_3.png"),
                  image("again", "Table A: Hyperparameters", "table_0.png"),
                  image("more", "Table A: Hyperparameters", "table_0.png")]
        assert dt.duplicate_tables(blocks) == ["again", "more"]

    def test_what_it_leaves_is_a_page_the_runaway_guard_accepts(self):
        blocks = [image(f"{i}-{k}", f"Table {k}: t{k}", f"table_{(k + i) % 3}.png")
                  for i in range(4) for k in range(3)]
        assert ept.table_repeats(blocks) == 4
        assert ept.table_repeats(without(blocks, dt.duplicate_tables(blocks))) == 1

    def test_running_it_again_finds_nothing(self):
        blocks = [image("a", "TABLE I: A"), image("b", "TABLE I: A"),
                  image("c", "TABLE II: B", "table_1.png")]
        assert dt.duplicate_tables(without(blocks, dt.duplicate_tables(blocks))) == []


class TestWhatIsNeverTouched:

    def test_a_composite_figures_panels_share_its_caption_and_all_stay(self):
        blocks = [image("p1", "Figure 4: Zero-shot sampling", "tmp1.png"),
                  image("p2", "Figure 4: Zero-shot sampling", "tmp2.png"),
                  image("s1", "(a)", "tmp3.png"), image("s2", "(a)", "tmp4.png")]
        assert dt.duplicate_tables(blocks) == []

    def test_uncaptioned_images_stay_even_when_they_share_a_file(self):
        blocks = [image("a", "", "image.png"), image("b", "", "image.png")]
        assert dt.duplicate_tables(blocks) == []

    def test_one_file_name_under_two_captions_is_two_tables(self):
        # Every run starts its counter at table_0, so distinct tables share names.
        blocks = [image("a", "TABLE I: A", "table_0.png"),
                  image("b", "TABLE III: C", "table_0.png")]
        assert dt.duplicate_tables(blocks) == []

    def test_an_already_archived_copy_is_neither_kept_nor_archived_again(self):
        gone = image("a", "TABLE I: A")
        gone["archived"] = True
        blocks = [gone, image("b", "TABLE I: A"), image("c", "TABLE I: A")]
        assert dt.duplicate_tables(blocks) == ["c"]


class TestTheRunawayGuardCountsTablesOnly:

    def test_repeated_figure_captions_do_not_trip_it(self):
        blocks = [image("p1", "Figure 4: Panels", "tmp1.png"),
                  image("p2", "Figure 4: Panels", "tmp2.png"),
                  image("t", "Table 1: Results")]
        assert ept.table_repeats(blocks) == 1

    def test_a_repeated_table_caption_does(self):
        blocks = [image("a", "TABLE I: Results"), image("b", "TABLE I: Results")]
        assert ept.table_repeats(blocks) == 2


class TestAForcedTableRunReplaces:

    def test_the_caption_written_is_always_labelled(self):
        assert ept.placed_caption(3, "Results.") == "Table 3: Results."
        assert ept.placed_caption(3, "") == "Table 3"
        assert ept.placed_caption(1, "TABLE I: Results") == "TABLE I: Results"

    def test_the_old_copy_of_a_re_placed_table_goes(self):
        blocks = [para("p"), image("old", "Table 1: Main results"),
                  image("other", "Table 2: Ablation", "table_1.png")]
        keys = {ept._cap_key(ept.placed_caption(1, "Table 1: Main results"))}
        assert ept.replaced_by(blocks, keys) == ["old"]

    def test_a_table_sharing_only_its_number_stays(self):
        # The id counter restarts in an appendix: S2.T1 and A1.T1 are both 1.
        blocks = [image("body", "Table 1: Main results"),
                  image("appendix", "Table A.1: Hyperparameters", "table_1.png")]
        keys = {ept._cap_key("Table A.1: Hyperparameters")}
        assert ept.replaced_by(blocks, keys) == ["appendix"]

    def test_uncaptioned_images_and_figures_stay(self):
        blocks = [image("mine", "", "image.png"),
                  image("fig", "Figure 1: Overview", "fig1.png"),
                  image("old", "Table 1: Main results")]
        keys = {ept._cap_key("Table 1: Main results")}
        assert ept.replaced_by(blocks, keys) == ["old"]
