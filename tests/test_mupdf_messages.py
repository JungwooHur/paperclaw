"""MuPDF's own messages reach stderr; a script's stdout carries only its output.

Each case runs a script that opens a PDF, in a fresh interpreter, on a PDF whose
Screen annotation makes MuPDF print `MuPDF error: ...`.
"""
import json
import os
import re
import subprocess
import sys
import zipfile

import fitz
import pytest

from conftest import SCRIPTS


def noisy_pdf(path):
    """A one-page PDF with text and an annotation MuPDF cannot draw."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(60, 60, 540, 780),
                        "1 Introduction\n" + "A sentence of the body. " * 40)
    annot = doc.get_new_xref()
    doc.update_object(annot, "<< /Type /Annot /Subtype /Screen /Rect [72 600 200 700] >>")
    doc.xref_set_key(page.xref, "Annots", f"[{annot} 0 R]")
    doc.save(path)
    return str(path)


AUDIT = """
import sys
import verify_sections as vs
heading = {"id": "h", "type": "heading_2",
           "heading_2": {"rich_text": [{"plain_text": "1 Introduction (서론)"}]}}
vs.fetch_blocks = lambda page: [heading]
vs.page_title = lambda page: "a paper"
sys.argv = ["verify_sections.py", "--page", "p", "--source", sys.argv[1], "--json"]
sys.exit(vs.main())
"""
REMAP = """
import json, sys
import remap_citations
print(json.dumps(remap_citations.source_sections(sys.argv[1])))
"""
MEDIA_TEXT = """
import json, sys
import extract_pdf_media
print(json.dumps(extract_pdf_media._media_text(
    sys.argv[1], {"Figure 1": {"page": 1, "box": [0, 0, 612, 792]}})))
"""
ATTACHMENT = """
import json, os, sys
import extract_pdf_media
path = extract_pdf_media.attachment_for("A sentence of the body. " * 2,
                                        os.path.dirname(sys.argv[1]))
assert path == sys.argv[1], path
print(json.dumps(path))
"""


def book_zip(tmp_path):
    path = tmp_path / "book.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.write(noisy_pdf(tmp_path / "chapter.pdf"), "chapter.pdf")
    return str(path)


def script(name):
    return os.path.join(SCRIPTS, name)


CASES = {
    "verify_sections --json": lambda t: [
        "-c", AUDIT, noisy_pdf(t / "paper.pdf")],
    "extract_pdf_media --out": lambda t: [
        script("extract_pdf_media.py"), "--pdf", noisy_pdf(t / "paper.pdf"),
        "--out", str(t / "media")],
    "extract_pdf_media _media_text": lambda t: [
        "-c", MEDIA_TEXT, noisy_pdf(t / "paper.pdf")],
    "extract_pdf_media attachment_for": lambda t: [
        "-c", ATTACHMENT, noisy_pdf(t / "paper.pdf")],
    "remap_citations source": lambda t: ["-c", REMAP, noisy_pdf(t / "paper.pdf")],
    "extract_book_figures --json": lambda t: [
        script("extract_book_figures.py"), book_zip(t), "--workdir", str(t / "work"),
        "--json"],
    "pdf_check": lambda t: [script("pdf_check.py"), noisy_pdf(t / "paper.pdf")],
}


@pytest.mark.parametrize("case", CASES)
def test_mupdf_messages_reach_stderr_not_stdout(case, tmp_path):
    proc = subprocess.run([sys.executable, *CASES[case](tmp_path)],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": SCRIPTS})
    assert "MuPDF error" in proc.stderr, proc.stderr[-2000:]
    assert "MuPDF" not in proc.stdout, proc.stdout[:2000]
    if case == "pdf_check":
        assert re.fullmatch(r"pages=1 avg_chars=\d+ slides_suspect=\w+\n", proc.stdout)
    else:
        json.loads(proc.stdout)
