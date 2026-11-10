"""Build DociFi.exe - one self-contained file, no installer, nothing else needed next to it.

Bundled inside the .exe:
  - Python, the DociFi engine and desktop app
  - Tesseract OCR (program, its libraries and the English + orientation data)
  - RapidOCR second reader with its ONNX models
  - the YAML rules (config/) and the app icons

Not bundled (too large / licensed separately): Microsoft Word or LibreOffice, used only to
convert .docx files. Without them, Word files are listed for review; PDFs and images still work.

Run from the project root:   app\\.venv\\Scripts\\python.exe app\\scripts\\build_exe.py
Result:                      app\\dist\\DociFi.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))
from pcs_tool import APP_FULL_NAME, APP_NAME, APP_PUBLISHER, __version__  # noqa: E402

TESSERACT_SRC = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR"
VENDOR = APP / "build" / "vendor" / "tesseract"
SEP = ";"  # PyInstaller add-data separator on Windows

EXCLUDES = [
    "tkinter", "pytest", "_pytest", "IPython", "matplotlib", "pandas", "scipy", "ruff", "pygments",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtMultimedia",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtSvg", "PySide6.QtNetwork",
]


def vendor_tesseract() -> None:
    """Copy only what tesseract.exe needs at run time (no training tools, no docs except the licence)."""
    if not (TESSERACT_SRC / "tesseract.exe").is_file():
        sys.exit(f"Tesseract not found in {TESSERACT_SRC}. Install it: winget install UB-Mannheim.TesseractOCR")
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    (VENDOR / "tessdata").mkdir(parents=True)
    shutil.copy2(TESSERACT_SRC / "tesseract.exe", VENDOR)
    for dll in TESSERACT_SRC.glob("*.dll"):
        shutil.copy2(dll, VENDOR)
    for name in ("eng.traineddata", "osd.traineddata"):
        shutil.copy2(TESSERACT_SRC / "tessdata" / name, VENDOR / "tessdata")
    lic = TESSERACT_SRC / "doc" / "LICENSE"
    if lic.is_file():
        shutil.copy2(lic, VENDOR / "LICENSE-tesseract.txt")


def version_file() -> Path:
    major, minor, patch = (int(x) for x in __version__.split("."))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_PUBLISHER}'),
      StringStruct('FileDescription', '{APP_FULL_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 {APP_PUBLISHER}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = APP / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def loader_image() -> Path:
    """The first frame of the animated splash, shown by the .exe loader while it unpacks.

    The loader marks transparent areas with pure magenta and has no partial transparency,
    so edge pixels are made either fully opaque (blended on the card colour) or magenta.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PIL import Image
    from PySide6.QtCore import QBuffer
    from PySide6.QtWidgets import QApplication

    from pcs_tool.ui.splash import render_frame

    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed to paint
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.ReadWrite)
    render_frame().save(buf, "PNG")
    import io
    rgba = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    card = Image.new("RGBA", rgba.size, (0x16, 0x29, 0x4A, 255))
    flat = Image.alpha_composite(card, rgba)
    alpha = rgba.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    out = Image.new("RGB", rgba.size, (255, 0, 255))
    out.paste(flat.convert("RGB"), (0, 0), alpha)
    path = APP / "build" / "loader_splash.png"
    out.save(path)
    return path


def main() -> None:
    vendor_tesseract()
    version_file()
    loader_image()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(APP / "dist"), "--workpath", str(APP / "build" / "pyinstaller"),
                    str(APP / "packaging" / "DociFi.spec")], check=True)
    exe = APP / "dist" / f"{APP_NAME}.exe"
    print(f"\nBuilt {exe}  ({exe.stat().st_size / 1_048_576:.0f} MB)")


if __name__ == "__main__":
    main()
