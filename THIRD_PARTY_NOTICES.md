# Third-party notices

DociFi uses the following components. They keep their own licences.

| Component | Used for | Licence |
|---|---|---|
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) 5.4 (bundled in DociFi.exe, with eng + osd data) | Reading scanned pages | Apache 2.0 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) (rapidocr-onnxruntime 1.2.3) and its PP-OCR models | Second, independent OCR reading | Apache 2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Runs the RapidOCR models locally | MIT |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | PDF rendering, text layer, page assembly | **GNU AGPL v3** (commercial licence available from Artifex) |
| [Qt 6 / PySide6](https://www.qt.io/qt-for-python) | Desktop user interface | LGPL v3 |
| [OpenCV](https://opencv.org/) (opencv-python-headless) | Image clean-up, card detection | Apache 2.0 |
| [Pillow](https://python-pillow.org/) | Image loading and conversion | MIT-CMU (HPND) |
| [NumPy](https://numpy.org/) | Image arrays | BSD 3-Clause |
| [pytesseract](https://github.com/madmaze/pytesseract) | Python wrapper for Tesseract | Apache 2.0 |
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | Fuzzy name and address matching | MIT |
| [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | Excel check sheet | MIT |
| [PyYAML](https://pyyaml.org/) | Rules files | MIT |
| [pywin32](https://github.com/mhammond/pywin32) | Microsoft Word automation | PSF |
| [Shapely](https://github.com/shapely/shapely), [pyclipper](https://github.com/fonttools/pyclipper), six | RapidOCR dependencies | BSD / MIT |
| [PyInstaller](https://pyinstaller.org/) | Building DociFi.exe | GPL v2 with bootloader exception (output may be distributed under any licence) |
| [Python](https://www.python.org/) 3.14 | Runtime | PSF |

Tesseract's licence text is included inside DociFi.exe (`vendor/tesseract/LICENSE-tesseract.txt`).
