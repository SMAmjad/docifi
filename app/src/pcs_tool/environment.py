"""Detects the external programs the tool relies on and reports them in plain English."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ToolStatus:
    name: str
    found: bool
    detail: str
    required: bool
    fix: str


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def find_tesseract(configured: str = "") -> str | None:
    """Bundled copy first, then configured path, PATH, and the standard install folders."""
    candidates = [
        _bundle_root() / "vendor" / "tesseract" / "tesseract.exe",
        Path(configured) if configured else None,
        Path(shutil.which("tesseract") or ""),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for c in candidates:
        if c and str(c) not in ("", ".") and c.is_file():
            return str(c)
    return None


def tesseract_version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                             check=False, creationflags=_NO_WINDOW)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:  # noqa: BLE001 - any failure means "unknown"
        return "unknown version"


def tesseract_languages(path: str) -> list[str]:
    try:
        out = subprocess.run([path, "--list-langs"], capture_output=True, text=True, timeout=20,
                             check=False, creationflags=_NO_WINDOW)
        return [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


@lru_cache(maxsize=1)
def find_libreoffice() -> str | None:
    candidates = [
        shutil.which("soffice"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "LibreOffice", "program", "soffice.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "LibreOffice", "program", "soffice.exe"),
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


@lru_cache(maxsize=1)
def word_available() -> bool:
    """True when Microsoft Word can be automated (COM ProgID registered)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


def check_environment(tesseract_configured: str = "") -> list[ToolStatus]:
    """Everything the first-run screen and `pcs envcheck` show."""
    results: list[ToolStatus] = []
    tess = find_tesseract(tesseract_configured)
    if tess:
        langs = tesseract_languages(tess)
        ok = "eng" in langs
        where = "built into DociFi" if Path(tess).is_relative_to(_bundle_root() / "vendor") else tess
        results.append(ToolStatus(
            "Text reader for scans (Tesseract OCR)", ok,
            f"{tesseract_version(tess)} ({where}); languages: {', '.join(langs) or 'none'}", True,
            "" if ok else "English language data (eng.traineddata) is missing from the Tesseract folder."))
    else:
        results.append(ToolStatus(
            "Text reader for scans (Tesseract OCR)", False, "Not found", True,
            "Install it with:  winget install UB-Mannheim.TesseractOCR"))
    word = word_available()
    lo = find_libreoffice()
    results.append(ToolStatus(
        "Microsoft Word (Word files to PDF)", word, "Available" if word else "Not found", False,
        "" if word else "Optional. Without Word, LibreOffice is used for Word files."))
    results.append(ToolStatus(
        "LibreOffice (backup for Word files)", bool(lo), lo or "Not found", False,
        "" if lo else "Optional. Install with:  winget install TheDocumentFoundation.LibreOffice"))
    if not word and not lo:
        results.append(ToolStatus(
            "Word file conversion", False, "Neither Word nor LibreOffice is available", False,
            "Word files (.docx) will be listed for review instead of converted. PDFs and images still work."))
    return results
