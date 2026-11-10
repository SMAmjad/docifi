"""Animated start-up screen.

Timeline (about 1.3 s; the app keeps loading underneath, so nothing waits on the animation):
  0.00-0.25  card fades in, logo grows slightly into place
  0.25-0.75  a scan line passes down the document
  0.65-0.88  the green check badge pops in
  0.42-0.82  'DociFi' and the tagline fade in and rise
then the splash fades out as the main window appears.

In the packaged .exe the frame at HANDOFF_T is shown by the .exe's own loader while it
unpacks (see render_frame and packaging/DociFi.spec); the animation then continues from
exactly that frame, so the user sees one smooth start-up.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .. import APP_PUBLISHER, APP_TAGLINE, __version__
from .brand import NAVY, paint_mark, paint_wordmark

DURATION_MS = 1300
HANDOFF_T = 0.25  # card fully shown, scan not yet started


def _seg(t: float, a: float, b: float) -> float:
    """0..1 progress of t inside the window [a, b]."""
    return 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def _back_out(x: float) -> float:  # slight overshoot for the 'pop'
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


class SplashScreen(QWidget):
    animation_done = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 340)
        self.t = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self.animation_done)
        # centred on the whole screen - the same place the .exe loader puts its still frame
        scr = self.screen().geometry() if self.screen() else None
        if scr:
            self.move(scr.center().x() - self.width() // 2, scr.center().y() - self.height() // 2)

    def start(self, from_t: float = 0.0) -> None:
        """Show and animate; from_t > 0 continues from a frame already on screen (the .exe loader's)."""
        self.t = from_t
        self._anim.setStartValue(from_t)
        self._anim.setDuration(int(DURATION_MS * (1.0 - from_t)))
        self.show()
        self._anim.start()

    def _tick(self, v: float) -> None:
        self.t = float(v)
        self.update()

    def fade_out_and_close(self) -> None:
        fade = QVariantAnimation(self, startValue=1.0, endValue=0.0, duration=220)
        fade.valueChanged.connect(lambda o: self.setWindowOpacity(float(o)))
        fade.finished.connect(self.close)
        fade.start()

    def paintEvent(self, _event) -> None:
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        appear = _ease_out(_seg(t, 0.0, HANDOFF_T))
        p.setOpacity(appear)

        # card
        card = QRectF(10, 10, self.width() - 20, self.height() - 20)
        bg = QLinearGradient(card.topLeft(), card.bottomRight())
        bg.setColorAt(0.0, QColor("#16294A"))
        bg.setColorAt(1.0, NAVY)
        path = QPainterPath()
        path.addRoundedRect(card, 18, 18)
        p.fillPath(path, bg)

        # soft light behind the logo
        glow_c = QPointF(150, 160)
        for i, a in enumerate((26, 18, 10)):
            p.setBrush(QColor(95, 168, 232, a))
            p.setPen(Qt.PenStyle.NoPen)
            rad = 92 + i * 18
            p.drawEllipse(glow_c, rad, rad)

        # logo mark
        size = 150 * (0.88 + 0.12 * appear)
        logo = QRectF(glow_c.x() - size / 2, glow_c.y() - size / 2, size, size)
        scan_p = _seg(t, HANDOFF_T, 0.75)
        scan = _ease_out(scan_p) if 0 < scan_p < 1 else None
        check = _back_out(_seg(t, 0.65, 0.88)) if t > 0.65 else 0.0
        p.setOpacity(1.0)
        paint_mark(p, logo, scan=scan, check=check, doc_opacity=appear)

        # wordmark + tagline
        words = _ease_out(_seg(t, 0.42, 0.82))
        rise = 12 * (1 - words)
        paint_wordmark(p, 262, 168 + rise, 58, opacity=words * appear)
        p.setOpacity(words * appear)
        f = QFont("Segoe UI")
        f.setPixelSize(17)
        p.setFont(f)
        p.setPen(QColor("#C9D3E6"))
        p.drawText(QPointF(266, 200 + rise), APP_TAGLINE)

        # footer: publisher, version, loading indicator
        p.setOpacity(appear)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#8FA3C4"))
        p.drawText(QRectF(36, 276, 300, 20), Qt.AlignmentFlag.AlignLeft, f"by {APP_PUBLISHER}")
        p.drawText(QRectF(card.right() - 236, 276, 210, 20), Qt.AlignmentFlag.AlignRight,
                   f"Version {__version__}  ·  Runs offline")
        bar = QRectF(36, 304, card.width() - 52, 3)
        p.setBrush(QColor(255, 255, 255, 30))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bar, 1.5, 1.5)
        # indeterminate shimmer (starts at the hand-off frame, so the loader's still image has none)
        phase = (max(0.0, t - HANDOFF_T) * 1.6) % 1.0 if t > HANDOFF_T else -1.0
        w = bar.width() * 0.28
        x = bar.left() + (bar.width() + w) * phase - w
        seg = QRectF(max(bar.left(), x), bar.top(), min(w, bar.right() - max(bar.left(), x)), bar.height())
        if phase >= 0 and seg.width() > 0:
            g = QLinearGradient(seg.topLeft(), seg.topRight())
            g.setColorAt(0, QColor(95, 168, 232, 0))
            g.setColorAt(0.5, QColor(95, 168, 232, 230))
            g.setColorAt(1, QColor(95, 168, 232, 0))
            p.setBrush(g)
            p.drawRoundedRect(seg, 1.5, 1.5)
        p.end()


def render_frame(t: float = HANDOFF_T, scale: float = 1.0):
    """One frame of the splash as a QImage (transparent around the card); used to build the .exe loader image."""
    from PySide6.QtGui import QImage
    w = SplashScreen()
    w.t = t
    img = QImage(int(w.width() * scale), int(w.height() * scale), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    img.fill(Qt.GlobalColor.transparent)
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QRegion
    w.render(img, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)  # no window background
    w.deleteLater()
    return img
