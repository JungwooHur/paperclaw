#!/usr/bin/env python3
"""A/B of `extract_pdf_media.render_media`: a base git ref against the work tree.

  tools/ab_render.py --out <dir> [--base origin/main] [--jobs N] <pdf>...

Each side renders every PDF in a subprocess with its own copy of the
research-papers scripts: the base copy is exported from `--base`, the branch copy
is the directory this file sits in. Floats are matched by (kind, label) and put
in one of four categories:

  added      rendered on the branch only
  removed    rendered on the base only
  relabeled  a removed float and an added one of the same kind on the same
             page whose boxes mostly overlap: one float read under a new label
  changed    rendered on both, on another page, with a box that moved or with
             other later pages (`render_media`'s "continued")
  same       rendered on both with the same box and later pages

A caption a side reads (its own `caption_kind`) and renders nothing for is
that side's `unrendered`, with the problem it reported (None when it reported
none, or cannot report). A removed float the branch reports no problem for,
whether or not it reads the caption, is `lost`.

Written under `--out`:

  report.json             {pdf: {category: [{key, page, base, branch, delta}],
                                 "unrendered": {side: {key: {page, problem}}}}}
  sheets/<category>-NN.png  one row per float: its page with the base box in red
                          and the branch box in green, then the base crop and
                          the branch crop; then one row per later page of it

The base side is cached by commit under `<out>/base-<sha>/`, so rerunning after
a change to the work tree renders only the branch side again.
"""
import argparse
import concurrent.futures
import io
import json
import os
import subprocess
import sys
import tarfile

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_IN_REPO = os.path.join("groups", "main", "research-papers")
CATEGORIES = ("added", "removed", "relabeled", "changed", "same")
SHEET_CATEGORIES = ("added", "removed", "relabeled", "changed")
BOX_TOL = 0.5
RELABEL_OVERLAP = 0.5

_ROW_HEIGHT = 560
_THUMB_WIDTH = 428
_CROP_WIDTH = 620
_LABEL_HEIGHT = 18
_RED = (0.85, 0.05, 0.05)
_GREEN = (0.0, 0.6, 0.1)


def key_of(kind: str, label) -> str:
    """The report key of a float: `figure|3` for an int label, `figure|'S2'` for a
    printed one."""
    return f"{kind}|{label}" if isinstance(label, int) else f"{kind}|{label!r}"


def delta(base_box, branch_box) -> list:
    """Branch box minus base box, edge by edge, in points."""
    return [round(b - a, 1) for a, b in zip(base_box, branch_box)]


def overlap(a, b) -> float:
    """Intersection over union of two boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return w * h / (area(a) + area(b) - w * h)


def _kind(key: str) -> str:
    return key.split("|", 1)[0]


def _moved(a: dict, b: dict, tol: float) -> bool:
    """Are the images `a` and `b` on other pages or boxes more than `tol` apart?"""
    return a["page"] != b["page"] or any(abs(d) > tol for d in delta(a["box"], b["box"]))


def compare(base: dict, branch: dict, tol: float = BOX_TOL) -> dict:
    """{category: [row]} for two `render_one` results of one PDF.

    A row is {key, page, base, branch, delta}; `base` and `branch` are the side's
    item or None, and `page` is the branch's page when it has one. A relabeled
    row's key is "<base key> -> <branch key>".
    """
    out = {c: [] for c in CATEGORIES}
    for key in sorted(set(base) | set(branch)):
        a, b = base.get(key), branch.get(key)
        row = {"key": key, "page": (b or a)["page"], "base": a, "branch": b,
               "delta": None}
        if a is None:
            out["added"].append(row)
            continue
        if b is None:
            out["removed"].append(row)
            continue
        row["delta"] = delta(a["box"], b["box"])
        moved = (_moved(a, b, tol) or len(a["continued"]) != len(b["continued"])
                 or any(_moved(x, y, tol) for x, y in zip(a["continued"], b["continued"])))
        out["changed" if moved else "same"].append(row)
    for gone in list(out["removed"]):
        twins = [new for new in out["added"]
                 if _kind(new["key"]) == _kind(gone["key"])
                 and new["branch"]["page"] == gone["base"]["page"]
                 and overlap(new["branch"]["box"], gone["base"]["box"])
                 >= RELABEL_OVERLAP]
        if not twins:
            continue
        new = max(twins, key=lambda r: overlap(r["branch"]["box"],
                                               gone["base"]["box"]))
        out["removed"].remove(gone)
        out["added"].remove(new)
        out["relabeled"].append({
            "key": f"{gone['key']} -> {new['key']}", "page": new["page"],
            "base": gone["base"], "branch": new["branch"],
            "delta": delta(gone["base"]["box"], new["branch"]["box"])})
    return out


def render_one(scripts_dir: str, pdf: str, out_dir: str) -> dict:
    """Runs `render_media` from `scripts_dir` on `pdf` in this process.

    Returns {"media": {key: {page, box, path, continued}}, "unrendered":
    {key: {page, problem}}}; "continued" is [{page, box, path}] of each later
    page of the float, none from a side whose `render_media` crops no later
    page. A later page reported with no crop is unrendered under
    `<key> part <n>`.
    """
    import inspect

    import fitz

    sys.path.insert(0, scripts_dir)
    import extract_pdf_media as pm

    here = os.path.dirname(os.path.abspath(pm.__file__))
    if here != os.path.abspath(scripts_dir):
        raise RuntimeError(f"imported {pm.__file__}, not the copy in {scripts_dir}")
    reported = []
    if "unrendered" in inspect.signature(pm.render_media).parameters:
        media = pm.render_media(pdf, out_dir, unrendered=reported)
    else:
        media = pm.render_media(pdf, out_dir)
    media = {key_of(kind, label): {"page": item["page"], "box": item["box"],
                                   "path": item["path"],
                                   "continued": item.get("continued", [])}
             for (kind, label), item in media.items()}
    problems = {key_of(u["kind"], u["label"]): u["problem"] for u in reported
                if "part" not in u}
    unrendered = {f"{key_of(u['kind'], u['label'])} part {u['part']}":
                  {"page": u["page"], "problem": u["problem"]}
                  for u in reported if "part" in u}
    with fitz.open(pdf) as doc:
        for pno in range(doc.page_count):
            for b in doc[pno].get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                seen = pm.caption_kind(pm._block_text(b))
                key = None if seen is None else key_of(*seen)
                if key is None or key in media or key in unrendered:
                    continue
                unrendered[key] = {"page": pno + 1, "problem": problems.get(key)}
    return {"media": media, "unrendered": unrendered}


def render_side(scripts_dir: str, pdf: str, out_dir: str) -> dict:
    """`render_one` in a subprocess, so each side imports its own scripts."""
    os.makedirs(out_dir, exist_ok=True)
    result = os.path.join(out_dir, "render.json")
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--render-one", scripts_dir,
         pdf, out_dir], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"render failed for {pdf} with {scripts_dir}:\n"
                           f"{proc.stderr[-4000:]}")
    with open(result, encoding="utf-8") as fh:
        return json.load(fh)


def export_base(ref: str, out_root: str) -> tuple:
    """(sha, scripts dir) of `ref`'s research-papers scripts, exported once."""
    top = subprocess.run(["git", "-C", SCRIPTS, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True).stdout.strip()
    sha = subprocess.run(["git", "-C", top, "rev-parse", "--verify", ref + "^{commit}"],
                         capture_output=True, text=True, check=True).stdout.strip()
    root = os.path.join(out_root, f"base-{sha[:12]}")
    scripts = os.path.join(root, "src", SCRIPTS_IN_REPO)
    if not os.path.isdir(scripts):
        tar = subprocess.run(["git", "-C", top, "archive", "--format=tar", sha,
                              SCRIPTS_IN_REPO], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
            archive.extractall(os.path.join(root, "src"), filter="data")
    return sha, root


def ab_one(pdf: str, base_root: str, branch_root: str) -> dict:
    """`compare` of one PDF's two sides, with each side's unrendered captions."""
    stem = os.path.splitext(os.path.basename(pdf))[0]
    base_out = os.path.join(base_root, "media", stem)
    cached = os.path.join(base_out, "render.json")
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as fh:
            base = json.load(fh)
    else:
        base = render_side(os.path.join(base_root, "src", SCRIPTS_IN_REPO), pdf,
                           base_out)
    branch = render_side(SCRIPTS, pdf, os.path.join(branch_root, stem))
    out = compare(base["media"], branch["media"])
    out["unrendered"] = {"base": base["unrendered"], "branch": branch["unrendered"]}
    out["rendered"] = {"base": sorted(base["media"]), "branch": sorted(branch["media"])}
    return out


def _fit(rect_w: float, rect_h: float, w: float, h: float) -> tuple:
    scale = min(rect_w / w, rect_h / h, 1.0)
    return w * scale, h * scale


def thumb_zoom(width: float, height: float) -> float:
    """Zoom of a page thumbnail: `_THUMB_WIDTH` wide, or less to fit the row."""
    return min(_THUMB_WIDTH / width, (_ROW_HEIGHT - _LABEL_HEIGHT - 8) / height)


def _row(sheet, y: float, pdf: str, row: dict, doc_cache: dict) -> None:
    import fitz

    if pdf not in doc_cache:
        doc_cache[pdf] = fitz.open(pdf)
    doc = doc_cache[pdf]
    page = doc[row["page"] - 1]
    title = (f"{os.path.basename(pdf)}  {row['key']}  p{row['page']}"
             + (f"  delta {row['delta']}" if row["delta"] else ""))
    sheet.insert_text((8, y + 13), title, fontsize=11)
    top = y + _LABEL_HEIGHT
    zoom = thumb_zoom(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    sheet.insert_image(fitz.Rect(8, top, 8 + pix.width, top + pix.height), pixmap=pix)
    for item, color in ((row["base"], _RED), (row["branch"], _GREEN)):
        if item is None or item["page"] != row["page"]:
            continue
        box = [v * zoom for v in item["box"]]
        sheet.draw_rect(fitz.Rect(8 + box[0], top + box[1], 8 + box[2], top + box[3]),
                        color=color, width=2)
    x = 16 + _THUMB_WIDTH
    for item, color, name in ((row["base"], _RED, "base"),
                              (row["branch"], _GREEN, "branch")):
        sheet.insert_text((x, top + 12), name if item else f"{name}: none",
                          fontsize=10, color=color)
        if item is not None and os.path.exists(item["path"]):
            crop = fitz.Pixmap(item["path"])
            w, h = _fit(_CROP_WIDTH, _ROW_HEIGHT - _LABEL_HEIGHT - 20, crop.width,
                        crop.height)
            sheet.insert_image(fitz.Rect(x, top + 18, x + w, top + 18 + h),
                               pixmap=crop)
            sheet.draw_rect(fitz.Rect(x, top + 18, x + w, top + 18 + h), color=color,
                            width=1)
        x += _CROP_WIDTH + 12


def part_rows(row: dict) -> list:
    """A sheet row (`compare`'s) for each later page of `row`'s float, the
    sides' later pages matched in order; key `<key> part <n>`."""
    sides = [row[s]["continued"] if row[s] else [] for s in ("base", "branch")]
    out = []
    for n in range(max(map(len, sides))):
        a, b = (side[n] if n < len(side) else None for side in sides)
        out.append({"key": f"{row['key']} part {n + 1}", "page": (b or a)["page"],
                    "base": a, "branch": b,
                    "delta": delta(a["box"], b["box"]) if a and b else None})
    return out


def contact_sheets(report: dict, pdfs: dict, out_dir: str, per_sheet: int) -> list:
    """Writes `<out_dir>/<category>-NN.png` for every added/removed/changed row."""
    import fitz

    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.endswith(".png"):
            os.remove(os.path.join(out_dir, name))
    written, doc_cache = [], {}
    page_w = 8 + _THUMB_WIDTH + 2 * (_CROP_WIDTH + 12) + 16
    for category in SHEET_CATEGORIES:
        rows = [(stem, r) for stem in sorted(report)
                for row in report[stem][category] for r in [row] + part_rows(row)]
        for start in range(0, len(rows), per_sheet):
            chunk = rows[start:start + per_sheet]
            sheet_doc = fitz.open()
            sheet = sheet_doc.new_page(width=page_w,
                                       height=_ROW_HEIGHT * len(chunk) + 8)
            sheet.draw_rect(sheet.rect, color=None, fill=(1, 1, 1))
            for i, (stem, row) in enumerate(chunk):
                _row(sheet, 4 + i * _ROW_HEIGHT, pdfs[stem], row, doc_cache)
            path = os.path.join(out_dir, f"{category}-{start // per_sheet:02d}.png")
            sheet.get_pixmap(alpha=False).save(path)
            sheet_doc.close()
            written.append(path)
    for doc in doc_cache.values():
        doc.close()
    return written


UNRENDERED_COLUMNS = ("unr base", "unr branch", "silent", "lost")


def lost(entry: dict) -> list:
    """The removed rows of `entry` whose float the branch reports no problem
    for: rendered on the base, and on the branch neither rendered, under its
    label or another (`relabeled`), nor reported unrendered."""
    branch = entry["unrendered"]["branch"]
    return [row for row in entry["removed"]
            if (branch.get(row["key"]) or {}).get("problem") is None]


def _unrendered_counts(entry: dict) -> dict:
    """{column: count}: each side's unrendered captions, the branch's that
    carry no problem, and the floats it lost (`lost`)."""
    base, branch = entry["unrendered"]["base"], entry["unrendered"]["branch"]
    return {"unr base": len(base), "unr branch": len(branch),
            "silent": sum(1 for u in branch.values() if u["problem"] is None),
            "lost": len(lost(entry))}


def summary_table(report: dict) -> str:
    """One line per PDF and a total: the count in each category, of
    unrendered captions and of lost floats, then one line per caption either
    side leaves unrendered and one per lost float."""
    columns = CATEGORIES + UNRENDERED_COLUMNS
    lines = [f"{'pdf':<28}" + "".join(f"{c:>11}" for c in columns)]
    totals = dict.fromkeys(columns, 0)
    for stem in sorted(report):
        counts = {c: len(report[stem][c]) for c in CATEGORIES}
        counts.update(_unrendered_counts(report[stem]))
        for c in columns:
            totals[c] += counts[c]
        lines.append(f"{stem:<28}" + "".join(f"{counts[c]:>11}" for c in columns))
    lines.append(f"{'TOTAL':<28}" + "".join(f"{totals[c]:>11}" for c in columns))
    for stem in sorted(report):
        sides, rendered = report[stem]["unrendered"], report[stem]["rendered"]

        def _status(side, key):
            if key in sides[side]:
                return sides[side][key]["problem"]
            return "rendered" if key in rendered[side] else "not read"

        for key in sorted(set(sides["base"]) | set(sides["branch"])):
            page = (sides["branch"].get(key) or sides["base"].get(key))["page"]
            lines.append(f"unrendered {stem} {key} p{page}: "
                         f"base {_status('base', key)}; "
                         f"branch {_status('branch', key)}")
        for row in lost(report[stem]):
            lines.append(f"lost {stem} {row['key']} p{row['page']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--render-one"]:
        scripts_dir, pdf, out_dir = argv[1:4]
        result = render_one(scripts_dir, pdf, out_dir)
        with open(os.path.join(out_dir, "render.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdfs", nargs="+", help="local PDF files")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--per-sheet", type=int, default=3)
    a = ap.parse_args(argv)

    pdfs = {}
    for pdf in a.pdfs:
        stem = os.path.splitext(os.path.basename(pdf))[0]
        if stem in pdfs:
            raise SystemExit(f"two PDFs named {stem}")
        pdfs[stem] = os.path.abspath(pdf)
    os.makedirs(a.out, exist_ok=True)
    sha, base_root = export_base(a.base, os.path.abspath(a.out))
    branch_root = os.path.join(os.path.abspath(a.out), "branch")
    report = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures = {pool.submit(ab_one, path, base_root, branch_root): stem
                   for stem, path in pdfs.items()}
        for future in concurrent.futures.as_completed(futures):
            report[futures[future]] = future.result()
            print(f"done {futures[future]}", file=sys.stderr, flush=True)
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump({"base": sha, "pdfs": report}, fh, indent=1)
    sheets = contact_sheets(report, pdfs, os.path.join(a.out, "sheets"), a.per_sheet)
    print(f"base {sha[:12]} vs work tree")
    print(summary_table(report))
    print(f"{len(sheets)} sheets in {os.path.join(a.out, 'sheets')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
