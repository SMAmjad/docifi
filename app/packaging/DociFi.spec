# PyInstaller spec for DociFi.exe - one self-contained file. Built by app/scripts/build_exe.py
# (which first copies the Tesseract run-time files into app/build/vendor/tesseract).
# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
SRC = APP / "src"
VENDOR = APP / "build" / "vendor" / "tesseract"

EXCLUDES = [
    "tkinter", "pytest", "_pytest", "IPython", "matplotlib", "pandas", "scipy", "ruff", "pygments",
    "pymupdf.layout", "PIL._avif", "PIL.AvifImagePlugin",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtMultimedia",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtSvg", "PySide6.QtNetwork",
]

# Run-time files nobody uses: software OpenGL, Qt Quick/QML/PDF, video codecs, PyMuPDF developer
# libraries and its optional layout-analysis models.
DROP = (
    "opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qt6network", "qt6opengl",
    "qt6svg", "qtvirtualkeyboard", "qpdf.dll", "qsvg", "opencv_videoio_ffmpeg", "mupdf-devel",
    "pymupdf/layout", "pymupdf\\layout", "_avif",
)

datas = [
    (str(APP / "config"), "config"),
    (str(SRC / "pcs_tool" / "ui" / "assets"), "pcs_tool/ui/assets"),
    (str(VENDOR), "vendor/tesseract"),
]
datas += collect_data_files("rapidocr_onnxruntime")

a = Analysis(  # noqa: F821
    [str(APP / "scripts" / "docifi_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("pcs_tool") + collect_submodules("rapidocr_onnxruntime") + ["win32com.client"],
    excludes=EXCLUDES,
    noarchive=False,
)

vendor_names = {p.name.lower() for p in VENDOR.glob("*.dll")}


def keep(entry) -> bool:
    dest = entry[0].replace("\\", "/").lower()
    if dest.startswith("vendor/"):
        return True
    if "/" not in dest and dest in vendor_names:  # Tesseract DLLs copied a second time to the root
        return False
    return not any(d.replace("\\", "/") in dest for d in DROP)


a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]

# Still first frame of the start-up animation, shown by the .exe loader while it unpacks
# (otherwise nothing would be visible for the first seconds after a double-click).
splash = Splash(  # noqa: F821
    str(APP / "build" / "loader_splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    minify_script=True,
    always_on_top=True,
)

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="DociFi",
    icon=str(SRC / "pcs_tool" / "ui" / "assets" / "app.ico"),
    version=str(APP / "build" / "version_info.txt"),
    console=False,
    upx=False,
    runtime_tmpdir=None,
    debug=False,
    strip=False,
)
_ = os  # keep import for editors
