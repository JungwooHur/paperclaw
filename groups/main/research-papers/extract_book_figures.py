#!/usr/bin/env python3
"""Extract figures from a book's source chapter PDFs (inside the uploaded zip)
and map each to its "Figure N-M" label, so the book translator can inject the
images next to the paragraphs that reference them.

Books arrive as a zip of one PDF per chapter; figures are embedded raster images
with a "Figure N-M" caption directly below. For each embedded image we find the
nearest caption below it (the mapping is unambiguous — verified dy≈7pt) and save
the original image bytes locally. The caller uploads them into Notion privately
(notion_upload.upload_image) at assembly time — these are copyrighted book
figures, so they must NOT go to a public host.

  from extract_book_figures import extract_figures
  figmap = extract_figures("/path/book.zip", workdir="/tmp/fig_x")  # {"1-5": "/tmp/.../fig_1-5.png"}

CLI:  extract_book_figures.py <book.zip> [--workdir DIR] [--json]
"""
import json
import os
import re
import sys
import tempfile
import zipfile

_CAP = re.compile(r"^\s*Figure\s*([0-9]+[-.][0-9]+)", re.I)


def extract_figures(zip_path, workdir=None):
    """Return {figure_label: local_image_path} for all figures in the book zip.

    Local paths are durable (unlike Notion file_upload ids, which expire until
    attached) — upload them to Notion at assembly time."""
    import fitz  # PyMuPDF
    work = workdir or tempfile.mkdtemp(prefix="bookfig_")
    os.makedirs(work, exist_ok=True)
    cache = os.path.join(work, "figmap.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            cached = json.load(f)
        # Stale-cache guard: the cache holds absolute paths to extracted PNGs. If
        # those files were cleaned up (e.g. an old short-prefix /tmp workdir whose
        # figmap.json was carried over), every upload fails and figure injection
        # silently yields 0 — re-extract instead of trusting dead paths.
        if cached and all(os.path.exists(p) for p in cached.values()):
            return cached

    exdir = os.path.abspath(os.path.join(work, "pdfs"))
    with zipfile.ZipFile(zip_path) as z:
        # Zip Slip guard: these zips come from user uploads — reject any member
        # that would write outside exdir (../ traversal, absolute paths).
        for m in z.infolist():
            dest = os.path.abspath(os.path.join(exdir, m.filename))
            if dest != exdir and not dest.startswith(exdir + os.sep):
                raise ValueError(f"unsafe zip member: {m.filename}")
        z.extractall(exdir)
    pdfs = sorted(p for p in _walk(exdir) if p.lower().endswith(".pdf"))

    figmap = {}
    for pdf in pdfs:
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        for pn in range(doc.page_count):
            pg = doc[pn]
            imgs = pg.get_images(full=True)
            if not imgs:
                continue
            blocks = [b for b in pg.get_text("dict")["blocks"] if b["type"] == 0]
            for img in imgs:
                xref = img[0]
                rects = pg.get_image_rects(xref)
                if not rects:
                    continue
                ir = rects[0]
                if ir.width < 60 or ir.height < 60:    # skip icons/rules
                    continue
                label, best = None, 1e9
                for b in blocks:
                    t = " ".join(s["text"] for l in b["lines"] for s in l["spans"])
                    m = _CAP.match(t.replace("\xa0", " "))
                    if not m:
                        continue
                    dy = b["bbox"][1] - ir.y1            # caption top - image bottom
                    if -25 < dy < best:
                        label, best = m.group(1).replace(".", "-"), dy
                if not label or label in figmap:
                    continue
                ext = doc.extract_image(xref)
                out = os.path.join(work, f"fig_{label}.{ext['ext']}")
                with open(out, "wb") as f:
                    f.write(ext["image"])
                figmap[label] = out
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(figmap, f, ensure_ascii=False)
    return figmap


def _walk(root):
    for d, _, fs in os.walk(root):
        for f in fs:
            yield os.path.join(d, f)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    m = extract_figures(a.zip, a.workdir)
    if a.json:
        print(json.dumps(m, indent=2))
    else:
        def _key(s):
            return [int(x) for x in re.split(r"[-.]", s) if x.isdigit()]
        for k in sorted(m, key=_key):
            print(f"Figure {k}: {m[k]}")
        print(f"\n{len(m)} figures", file=sys.stderr)


if __name__ == "__main__":
    main()
