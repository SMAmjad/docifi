"""Conversion to PDF. The Word file is generated inside the test (two lines of neutral text)."""

import zipfile

import pytest
from PIL import Image

from pcs_tool.engine import pdf_backend
from pcs_tool.engine.convert import ConversionError, convert_to_pdf
from pcs_tool.environment import find_libreoffice, word_available

_CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
_DOC = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Conversion test page</w:t></w:r></w:p></w:body></w:document>')


def _make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", _DOC)


@pytest.mark.skipif(not (word_available() or find_libreoffice()), reason="needs Microsoft Word or LibreOffice")
def test_docx_to_pdf(tmp_path):
    src = tmp_path / "sample.docx"
    _make_docx(src)
    pdf, converted, method = convert_to_pdf(src, tmp_path / "out", "F001")
    assert converted and pdf.is_file() and pdf_backend.page_count(pdf) == 1
    assert "Conversion test page" in pdf_backend.page_text(pdf, 0)


def test_multi_page_tiff_to_pdf(tmp_path):
    src = tmp_path / "scan.tif"
    frames = [Image.new("RGB", (200, 260), c) for c in ("white", "lightgray", "white")]
    frames[0].save(src, save_all=True, append_images=frames[1:])
    pdf, converted, _ = convert_to_pdf(src, tmp_path / "out", "F002")
    assert converted and pdf_backend.page_count(pdf) == 3


def test_pdf_is_used_as_is_and_never_modified(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (100, 100), "white").save(img)
    pdf = tmp_path / "a.pdf"
    pdf_backend.image_to_pdf(img, pdf)
    before = pdf.read_bytes()
    out, converted, _ = convert_to_pdf(pdf, tmp_path / "out", "F003")
    assert out == pdf and not converted and pdf.read_bytes() == before


def test_damaged_image_gives_plain_reason(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not really a jpeg")
    with pytest.raises(ConversionError, match="could not be opened"):
        convert_to_pdf(bad, tmp_path / "out", "F004")


def test_unsupported_type(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"PK")
    with pytest.raises(ConversionError, match="not supported"):
        convert_to_pdf(f, tmp_path / "out", "F005")
