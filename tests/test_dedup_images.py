"""Repeated copies of one image are removed, and nothing else is.

An injector that could not recognise its own output placed the same tables again
on every healer cycle, and the copies outlived the fix: the runaway guard stopped
the growth, but nothing took the pile away, and the guard then refused the page
for good. One image per caption is the only correct state, so every later copy
goes and the first stays.

A forced table run is the other way copies pile up, so its replace step is pinned
here too: it archives what the new images stand in for, and nothing else.
"""
import dedup_images as di
import extract_paper_tables as ept


def image(bid, caption, name="table_0.png", kind="file"):
    url = f"https://files.example/abc/{bid}/{name}?X-Amz-Signature=sig-{bid}"
    return {"id": bid, "type": "image",
            "image": {"type": kind, kind: {"url": url},
                      "caption": [{"plain_text": caption}] if caption else []}}


def para(bid, text="본문"):
    return {"id": bid, "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]}}


def guard_counts(blocks):
    """What inject_tables' runaway guard counts: images per caption key."""
    hist = {}
    for b in blocks:
        if b["type"] == "image":
            key = ept._cap_key(ept._image_caption(b))
            if key:
                hist[key] = hist.get(key, 0) + 1
    return hist


class TestWhatCountsAsTheSameImage:

    def test_spacing_and_case_in_the_caption_do_not_matter(self):
        assert (di.image_key(image("a", "TABLE I:  Results\n"))
                == di.image_key(image("b", "table i: results")))

    def test_the_file_name_does_not_matter(self):
        # Tables are uploaded as table_<i> numbered per run: one table placed by
        # two runs that rendered different sets carries two names.
        assert (di.image_key(image("a", "Table A: Hyperparameters", "table_3.png"))
                == di.image_key(image("b", "Table A: Hyperparameters", "table_0.png")))

    def test_an_uncaptioned_image_has_no_identity(self):
        assert di.image_key(image("a", "")) is None

    def test_only_images_have_one(self):
        assert di.image_key(para("p")) is None


class TestWhichCopiesGo:

    def test_every_later_copy_goes_and_the_first_stays(self):
        blocks = [para("p1"), image("t0", "TABLE I: A"), para("p2"),
                  image("t1", "TABLE II: B", "table_1.png"),
                  image("t0b", "TABLE I: A"), image("t1b", "TABLE II: B", "table_1.png"),
                  image("t0c", "TABLE I: A")]
        assert di.duplicate_images(blocks) == ["t0b", "t1b", "t0c"]

    def test_a_page_that_ran_away_keeps_one_of_each(self):
        captions = [f"TABLE {n}: caption {n}" for n in ("I", "II", "III", "IV", "V")]
        blocks = [image(f"{i}-{k}", c, f"table_{k}.png")
                  for i in range(55) for k, c in enumerate(captions)]
        extra = set(di.duplicate_images(blocks))
        assert len(extra) == 54 * 5
        assert [b["id"] for b in blocks if b["id"] not in extra] == [
            f"0-{k}" for k in range(5)]

    def test_copies_under_different_file_names_still_collapse(self):
        blocks = [image("first", "Table A: Hyperparameters", "table_3.png"),
                  image("again", "Table A: Hyperparameters", "table_0.png"),
                  image("more", "Table A: Hyperparameters", "table_0.png")]
        assert di.duplicate_images(blocks) == ["again", "more"]

    def test_what_it_leaves_is_a_page_the_runaway_guard_accepts(self):
        blocks = [image(f"{i}-{k}", f"Table {k}: t{k}", f"table_{(k + i) % 3}.png")
                  for i in range(4) for k in range(3)]
        assert max(guard_counts(blocks).values()) == 4
        extra = set(di.duplicate_images(blocks))
        left = [b for b in blocks if b["id"] not in extra]
        assert max(guard_counts(left).values()) == 1

    def test_running_it_again_finds_nothing(self):
        blocks = [image("a", "TABLE I: A"), image("b", "TABLE I: A"),
                  image("c", "TABLE II: B", "table_1.png")]
        extra = set(di.duplicate_images(blocks))
        assert di.duplicate_images([b for b in blocks if b["id"] not in extra]) == []


class TestWhatIsNeverTouched:

    def test_uncaptioned_images_stay_even_when_they_share_a_file(self):
        blocks = [image("a", "", "photo.png"), image("b", "", "photo.png")]
        assert di.duplicate_images(blocks) == []

    def test_one_file_name_under_two_captions_is_two_tables(self):
        # Every run starts its counter at table_0, so distinct tables share names.
        blocks = [image("a", "TABLE I: A", "table_0.png"),
                  image("b", "TABLE III: C", "table_0.png")]
        assert di.duplicate_images(blocks) == []

    def test_different_floats_are_left_alone(self):
        blocks = [image("a", "TABLE I: A", "table_0.png"),
                  image("b", "TABLE II: B", "table_1.png"),
                  image("c", "Figure 1: C", "fig1.png")]
        assert di.duplicate_images(blocks) == []

    def test_an_already_archived_copy_is_neither_kept_nor_archived_again(self):
        gone = image("a", "TABLE I: A")
        gone["archived"] = True
        blocks = [gone, image("b", "TABLE I: A"), image("c", "TABLE I: A")]
        assert di.duplicate_images(blocks) == ["c"]


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
