"""End-to-end robustness: awkward input files must never stop a run.

All files are generated inside the test and contain no personal data (blank pages,
broken bytes, a one-line Word file). They exercise error handling, not document reading.
"""

import zipfile

import pymupdf
import pytest
from PIL import Image

from pcs_tool.config import load_config
from pcs_tool.engine.pipeline import OUTPUT_MARKER, PipelineError, run_pipeline
from pcs_tool.environment import find_libreoffice, find_tesseract, word_available

pytestmark = pytest.mark.skipif(not find_tesseract(), reason="needs Tesseract")


def _blank_pdf(path, pages=1, password=None):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    if password:
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        doc.save(path)
    doc.close()


def _docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Meeting notes for the weekly planning call.</w:t></w:r></w:p></w:body></w:document>')


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("robust")
    inp = root / "input"
    (inp / "Sub folder" / "deeper").mkdir(parents=True)
    (inp / "empty.pdf").write_bytes(b"")
    (inp / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    _blank_pdf(inp / "locked.pdf", password="secret")
    _blank_pdf(inp / "Sub folder" / "blank two pages.pdf", pages=2)
    Image.new("RGB", (800, 1000), "white").save(inp / "Café résumé ü.png")
    Image.new("RGB", (800, 1000), "white").save(inp / "Sub folder" / "deeper" / "copy of café.png")
    frames = [Image.new("RGB", (600, 800), "white") for _ in range(3)]
    frames[0].save(inp / "scan.tif", save_all=True, append_images=frames[1:])
    (inp / "archive.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (inp / "Thumbs.db").write_bytes(b"x")
    (inp / "notes.xlsx").write_bytes(b"x")
    _docx(inp / "memo.docx")
    (inp / "identical.png").write_bytes((inp / "Café résumé ü.png").read_bytes())
    out = root / "results"
    result = run_pipeline(inp, out, cfg=load_config(), workers=2)
    return inp, out, result


def test_run_completes_and_writes_everything(run):
    _, out, result = run
    assert result.sheet_path.is_file() and (out / OUTPUT_MARKER).is_file()
    assert (result.run_dir / "logs" / "audit.jsonl").is_file()


def test_problem_files_are_reported_in_plain_english(run):
    _, _, result = run
    by_name = {f.relative.split("\\")[-1].split("/")[-1]: f for f in result.files}
    assert "Thumbs.db" not in by_name                                   # system files ignored
    assert "password" in (by_name["locked.pdf"].error or "").lower()
    assert by_name["broken.pdf"].error and by_name["empty.pdf"].error
    assert "not supported" in (by_name["archive.zip"].error or "")
    assert by_name["notes.xlsx"].skipped
    assert by_name["identical.png"].duplicate_of                        # exact copy detected, not processed twice


def test_good_files_are_converted(run):
    _, _, result = run
    by_name = {f.relative.split("\\")[-1].split("/")[-1]: f for f in result.files}
    assert by_name["scan.tif"].page_count == 3
    assert by_name["Café résumé ü.png"].converted and not by_name["Café résumé ü.png"].error
    if word_available() or find_libreoffice():
        assert by_name["memo.docx"].converted and by_name["memo.docx"].page_count == 1


def test_originals_untouched(run):
    inp, _, _ = run
    assert (inp / "broken.pdf").read_bytes() == b"%PDF-1.4 this is not really a pdf"


def test_blank_pages_go_to_review_not_into_a_label(run):
    _, _, result = run
    assert all(not d.type_id for d in result.documents)
    assert all(d.review_reasons for d in result.documents)


def test_empty_input_folder(tmp_path):
    (tmp_path / "in").mkdir()
    result = run_pipeline(tmp_path / "in", tmp_path / "out", cfg=load_config())
    assert result.stats["documents"] == 0 and result.sheet_path.is_file()


def test_bad_guide_file_never_costs_the_results(tmp_path):
    """The guide/rules naming test is optional: an unreadable guide is skipped, the check sheet is still written."""
    (tmp_path / "in").mkdir()
    bad = tmp_path / "not a guide.xlsx"
    bad.write_bytes(b"this is not a workbook")
    result = run_pipeline(tmp_path / "in", tmp_path / "out", cfg=load_config(), guide_path=bad)
    assert result.sheet_path.is_file()


def test_missing_input_folder(tmp_path):
    with pytest.raises(PipelineError):
        run_pipeline(tmp_path / "does-not-exist", tmp_path / "out", cfg=load_config())
