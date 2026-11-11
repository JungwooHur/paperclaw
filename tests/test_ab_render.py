"""How the PDF renderer's A/B harness sorts one PDF's floats."""
import importlib.util
import os

from conftest import SCRIPTS

_SPEC = importlib.util.spec_from_file_location(
    "ab_render", os.path.join(SCRIPTS, "tools", "ab_render.py"))
ab = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ab)


def item(page, box):
    return {"page": page, "box": box, "path": "x.png"}


def keys(report, category):
    return [row["key"] for row in report[category]]


class TestCompare:

    def test_each_float_lands_in_one_category(self):
        base = {"figure|1": item(1, [0, 0, 100, 100]),
                "figure|2": item(2, [0, 0, 100, 100]),
                "figure|3": item(3, [0, 0, 100, 100])}
        branch = {"figure|1": item(1, [0, 0, 100, 100.3]),
                  "figure|2": item(2, [0, 0, 100, 140]),
                  "table|1": item(4, [0, 0, 50, 50])}
        report = ab.compare(base, branch)
        assert keys(report, "same") == ["figure|1"]
        assert keys(report, "changed") == ["figure|2"]
        assert report["changed"][0]["delta"] == [0, 0, 0, 40]
        assert keys(report, "removed") == ["figure|3"]
        assert keys(report, "added") == ["table|1"]

    def test_a_float_on_another_page_has_changed(self):
        report = ab.compare({"figure|1": item(1, [0, 0, 10, 10])},
                            {"figure|1": item(2, [0, 0, 10, 10])})
        assert keys(report, "changed") == ["figure|1"]

    def test_one_float_under_a_new_label_is_relabeled(self):
        base = {"figure|3": item(5, [0, 0, 100, 100])}
        branch = {"figure|'3.1'": item(5, [0, 10, 100, 100]),
                  "figure|'3.2'": item(6, [0, 0, 100, 100])}
        report = ab.compare(base, branch)
        assert keys(report, "relabeled") == ["figure|3 -> figure|'3.1'"]
        assert keys(report, "added") == ["figure|'3.2'"]
        assert report["removed"] == []

    def test_a_float_elsewhere_on_the_page_is_not_a_relabel(self):
        base = {"figure|3": item(5, [0, 0, 100, 100])}
        branch = {"figure|'3.1'": item(5, [0, 300, 100, 400])}
        report = ab.compare(base, branch)
        assert keys(report, "removed") == ["figure|3"]
        assert keys(report, "added") == ["figure|'3.1'"]

    def test_a_table_is_never_a_relabeled_figure(self):
        report = ab.compare({"figure|3": item(5, [0, 0, 100, 100])},
                            {"table|3": item(5, [0, 0, 100, 100])})
        assert report["relabeled"] == []


def test_a_printed_label_is_told_from_a_number_in_the_key():
    assert ab.key_of("figure", 3) == "figure|3"
    assert ab.key_of("figure", "3") == "figure|'3'"


def test_a_page_of_any_shape_fits_its_thumbnail():
    for width, height in ((612, 792), (1644, 792), (612, 1584)):
        zoom = ab.thumb_zoom(width, height)
        assert width * zoom <= ab._THUMB_WIDTH + 1e-6
        assert height * zoom <= ab._ROW_HEIGHT - ab._LABEL_HEIGHT


def lost_caption_pdf(tmp_path):
    """One page: prose, a figure caption with no figure, prose."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    text = ("Body prose fills the column with justified lines of ordinary words "
            "so that every line runs the full measure. ") * 6
    page.insert_textbox(fitz.Rect(72, 72, 540, 300), text, fontsize=10,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    page.insert_text((72, 320), "Figure 7: A caption whose figure is elsewhere.",
                     fontsize=9)
    page.insert_textbox(fitz.Rect(72, 340, 540, 600), text, fontsize=10,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
    path = tmp_path / "lost.pdf"
    doc.save(path)
    return str(path)


class TestUnrendered:

    def test_the_branch_names_its_problem(self, tmp_path):
        result = ab.render_side(SCRIPTS, lost_caption_pdf(tmp_path),
                                str(tmp_path / "out"))
        assert result["media"] == {}
        assert list(result["unrendered"]) == ["figure|7"]
        assert result["unrendered"]["figure|7"]["page"] == 1
        assert result["unrendered"]["figure|7"]["problem"]

    def test_a_side_that_cannot_report_still_lists_its_captions(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "extract_pdf_media.py").write_text(
            "def _block_text(block):\n"
            "    return ' '.join(s['text'] for ln in block.get('lines', ())"
            " for s in ln['spans'])\n\n"
            "def caption_kind(text):\n"
            "    return ('figure', 7) if text.startswith('Figure 7') else None\n\n"
            "def render_media(pdf_path, out_dir, kinds=('figure', 'table'),"
            " only=None):\n"
            "    return {}\n")
        result = ab.render_side(str(scripts), lost_caption_pdf(tmp_path),
                                str(tmp_path / "out"))
        assert result["unrendered"] == {"figure|7": {"page": 1, "problem": None}}

    def test_the_summary_counts_them(self):
        entry = {c: [] for c in ab.CATEGORIES}
        entry["unrendered"] = {
            "base": {"figure|7": {"page": 1, "problem": None}},
            "branch": {"figure|7": {"page": 1, "problem": "nothing"},
                       "table|2": {"page": 3, "problem": None}}}
        entry["rendered"] = {"base": ["table|2"], "branch": []}
        lines = ab.summary_table({"paper": entry}).splitlines()
        assert lines[1].split()[-3:] == ["1", "2", "1"]
        assert "unrendered paper figure|7 p1: base None; branch nothing" in lines
        assert "unrendered paper table|2 p3: base rendered; branch None" in lines

    def test_a_caption_a_side_never_read_is_told_from_a_rendered_one(self):
        entry = {c: [] for c in ab.CATEGORIES}
        entry["unrendered"] = {"base": {},
                               "branch": {"figure|'S1'": {"page": 2, "problem": "x"}}}
        entry["rendered"] = {"base": [], "branch": []}
        assert ("unrendered paper figure|'S1' p2: base not read; branch x"
                in ab.summary_table({"paper": entry}).splitlines())
