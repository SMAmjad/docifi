"""Step 1 - Convert every supported input file to PDF.

* PDF            -> used as is (copied reference, never modified)
* Images         -> PDF via Pillow (multi-page TIFF supported, phone EXIF rotation applied)
* Word / RTF/ODT -> Microsoft Word (primary) or LibreOffice (fallback), both local
* Plain text     -> simple PDF
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..environment import find_libreoffice, word_available
from . import pdf_backend

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
OFFICE_EXT = {".docx", ".doc", ".rtf", ".odt"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ConversionError(Exception):
    """Plain-English reason why a file could not be converted."""


def _word_to_pdf(src: Path, out_pdf: Path) -> None:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
        try:
            doc.ExportAsFixedFormat(str(out_pdf.resolve()), 17)  # 17 = wdExportFormatPDF
        finally:
            doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def _libreoffice_to_pdf(soffice: str, src: Path, out_pdf: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pcs_lo_") as tmp:
        profile = Path(tmp) / "profile"
        cmd = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", tmp, str(src)]
        proc = subprocess.run(cmd, capture_output=True, timeout=180, check=False, creationflags=_NO_WINDOW)
        produced = Path(tmp) / (src.stem + ".pdf")
        if proc.returncode != 0 or not produced.is_file():
            raise ConversionError("LibreOffice could not convert this file.")
        shutil.move(str(produced), out_pdf)


def office_to_pdf(src: Path, out_pdf: Path) -> str:
    """Convert a Word-type document. Returns the engine used."""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    if word_available() and src.suffix.lower() in (".docx", ".doc", ".rtf"):
        try:
            _word_to_pdf(src, out_pdf)
            return "Microsoft Word"
        except Exception as exc:  # noqa: BLE001 - fall back to LibreOffice
            errors.append(f"Word: {type(exc).__name__}")
    soffice = find_libreoffice()
    if soffice:
        _libreoffice_to_pdf(soffice, src, out_pdf)
        return "LibreOffice"
    if errors:
        raise ConversionError("Microsoft Word could not convert this file and LibreOffice is not installed.")
    raise ConversionError("Word files need Microsoft Word or LibreOffice, and neither is installed.")


def convert_to_pdf(src: Path, out_dir: Path, file_id: str) -> tuple[Path, bool, str]:
    """Return (pdf_path, was_converted, method). Raises ConversionError with a plain reason."""
    ext = src.suffix.lower()
    if ext == ".pdf":
        if pdf_backend.is_encrypted(src):
            raise ConversionError("The PDF is password-protected.")
        return src, False, "already PDF"
    out_pdf = out_dir / f"{file_id}.pdf"
    if ext in IMAGE_EXT:
        try:
            pdf_backend.image_to_pdf(src, out_pdf)
        except Exception as exc:  # noqa: BLE001
            raise ConversionError("The image file could not be opened (it may be damaged).") from exc
        return out_pdf, True, "image to PDF"
    if ext == ".heic":
        raise ConversionError("HEIC photos are not supported yet. Save the photo as JPG and add it again.")
    if ext in OFFICE_EXT:
        method = office_to_pdf(src, out_pdf)
        return out_pdf, True, method
    if ext == ".txt":
        pdf_backend.text_to_pdf(src.read_text(encoding="utf-8", errors="replace"), out_pdf)
        return out_pdf, True, "text to PDF"
    raise ConversionError(f"File type '{ext}' is not supported.")
