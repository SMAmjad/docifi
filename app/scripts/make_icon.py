"""Render the DociFi icon files from the vector mark in pcs_tool/ui/brand.py.

Writes app.ico (16-256 px, each size rendered natively for sharpness), app.png (512 px)
and check.png (tick used in check boxes). Run from the project root:

    app\\.venv\\Scripts\\python.exe app\\scripts\\make_icon.py
"""

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402

from pcs_tool.ui.brand import WHITE, paint_mark  # noqa: E402

OUT = ROOT / "src" / "pcs_tool" / "ui" / "assets"


def render(size: int) -> Image.Image:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_mark(p, QRectF(0, 0, size, size))
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.ReadWrite)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def tick(size: int = 64) -> Image.Image:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 16
    path = QPainterPath(QPointF(3.5 * s, 8.5 * s))
    path.lineTo(QPointF(6.5 * s, 11.5 * s))
    path.lineTo(QPointF(12.5 * s, 4.5 * s))
    p.setPen(QPen(WHITE, 2.2 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.ReadWrite)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    render(512).save(OUT / "app.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]
    frames[-1].save(OUT / "app.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    tick().save(OUT / "check.png")
    print(f"Icons written to {OUT}")
