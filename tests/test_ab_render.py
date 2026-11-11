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
