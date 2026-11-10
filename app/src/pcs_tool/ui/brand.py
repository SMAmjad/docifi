"""DociFi brand mark, drawn as vectors so it is sharp at every size and screen scaling.

One function draws the mark; the splash screen animates it, the window header shows it,
and scripts/make_icon.py renders it into app.ico / app.png. No font is used inside the mark.

The mark: a rounded tile (navy -> blue gradient) holding a white document with a folded
corner and three text lines, a scan line passing over it, and a green check badge -
"documents read and checked".
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

NAVY = QColor("#1F3864")
BLUE = QColor("#2E75B6")
SKY = QColor("#5FA8E8")
GREEN = QColor("#2FA05A")
WHITE = QColor("#FFFFFF")
FOLD = QColor("#C8D4E6")
LINE = QColor("#9DBFE3")


def paint_mark(p: QPainter, rect: QRectF, *, scan: float | None = None, check: float = 1.0,
               doc_opacity: float = 1.0) -> None:
    """Draw the mark inside ``rect`` (square).

    scan   - position of the scan line, 0 (top of the page) .. 1 (bottom); None = not drawn
    check  - scale of the check badge, 0 .. 1 (values slightly above 1 give a 'pop')
    """
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = rect.width() / 512.0
    ox, oy = rect.x(), rect.y()

    def pt(x: float, y: float) -> QPointF:
        return QPointF(ox + x * s, oy + y * s)

    def r(x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(ox + x * s, oy + y * s, w * s, h * s)

    # tile
    grad = QLinearGradient(pt(0, 0), pt(512, 512))
    grad.setColorAt(0.0, NAVY)
    grad.setColorAt(1.0, BLUE)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(r(16, 16, 480, 480), 110 * s, 110 * s)

    # document with folded corner
    p.setOpacity(doc_opacity)
    page = QPainterPath()
    page.moveTo(pt(142, 92))
    page.lineTo(pt(318, 92))
    page.lineTo(pt(386, 160))
    page.lineTo(pt(386, 420))
    page.quadTo(pt(386, 432), pt(374, 432))
    page.lineTo(pt(154, 432))
    page.quadTo(pt(142, 432), pt(142, 420))
    page.closeSubpath()
    p.setBrush(WHITE)
    p.drawPath(page)
    fold = QPainterPath()
    fold.moveTo(pt(318, 92))
    fold.lineTo(pt(318, 148))
    fold.quadTo(pt(318, 160), pt(330, 160))
    fold.lineTo(pt(386, 160))
    fold.closeSubpath()
    p.setBrush(FOLD)
    p.drawPath(fold)
    p.setBrush(LINE)
    for y, w in ((196, 168), (240, 168), (284, 120), (328, 96)):
        p.drawRoundedRect(r(178, y, w, 20), 10 * s, 10 * s)

    # scan line (only while animating)
    if scan is not None and 0.0 <= scan <= 1.0:
        y = 100 + scan * 320
        glow = QLinearGradient(pt(0, y - 26), pt(0, y + 2))
        glow.setColorAt(0.0, QColor(95, 168, 232, 0))
        glow.setColorAt(1.0, QColor(95, 168, 232, 110))
        p.setBrush(QBrush(glow))
        p.drawRect(r(142, y - 26, 244, 28))
        p.setPen(QPen(SKY, 6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(pt(126, y), pt(402, y))
        p.setPen(Qt.PenStyle.NoPen)
    p.setOpacity(1.0)

    # check badge
    if check > 0:
        cx, cy, rad = 350, 384, 84 * check
        p.setBrush(GREEN)
        p.setPen(QPen(WHITE, 14 * s * min(1.0, check)))
        p.drawEllipse(QPointF(ox + cx * s, oy + cy * s), rad * s, rad * s)
        if check > 0.45:
            k = check
            tick = QPainterPath()
            tick.moveTo(pt(cx - 36 * k, cy + 2 * k))
            tick.lineTo(pt(cx - 10 * k, cy + 28 * k))
            tick.lineTo(pt(cx + 40 * k, cy - 26 * k))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(WHITE, 20 * s * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(tick)
    p.restore()


def paint_wordmark(p: QPainter, x: float, baseline: float, size_px: float, *, light: bool = True,
                   opacity: float = 1.0) -> float:
    """'DociFi' - 'Doci' in white (or navy) and 'Fi' in sky blue. Returns the width drawn."""
    from PySide6.QtGui import QFont, QFontMetricsF
    p.save()
    p.setOpacity(opacity)
    f = QFont("Segoe UI")
    f.setWeight(QFont.Weight.DemiBold)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(int(size_px))
    p.setFont(f)
    fm = QFontMetricsF(f)
    p.setPen(WHITE if light else NAVY)
    p.drawText(QPointF(x, baseline), "Doci")
    w1 = fm.horizontalAdvance("Doci")
    p.setPen(SKY if light else BLUE)
    p.drawText(QPointF(x + w1, baseline), "Fi")
    p.restore()
    return w1 + fm.horizontalAdvance("Fi")
