"""Light / dark appearance.

The user picks System (follow Windows), Light or Dark; the choice is saved. Applying a theme
swaps the style sheet, tells Qt the colour scheme (so standard dialogs match) and switches the
Windows title bar of every window to the matching light or dark frame.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .style import build_qss

MODES = ("system", "light", "dark")
_current = {"theme": "light"}


def system_is_dark() -> bool:
    """True when Windows is set to dark mode for apps."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    return False


def resolve(mode: str) -> str:
    if mode == "dark":
        return "dark"
    if mode == "light":
        return "light"
    return "dark" if system_is_dark() else "light"


def current() -> str:
    return _current["theme"]


def set_title_bar(widget: QWidget, dark: bool) -> None:
    """Ask Windows (10 1809+ / 11) to draw this window's title bar dark or light."""
    if sys.platform != "win32" or not widget.isWindow():
        return
    try:
        import ctypes
        value = ctypes.c_int(1 if dark else 0)
        hwnd = int(widget.winId())
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


class _TitleBarFollower(QObject):
    """Gives every window that opens (dialogs, message boxes) the title bar of the current theme."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            set_title_bar(obj, _current["theme"] == "dark")
        return False


_follower: _TitleBarFollower | None = None


def apply(mode: str) -> str:
    """Apply System / Light / Dark to the whole application. Returns the theme in effect."""
    app = QApplication.instance()
    global _follower
    if _follower is None and app is not None:
        _follower = _TitleBarFollower(app)
        app.installEventFilter(_follower)
    theme = resolve(mode)
    _current["theme"] = theme
    if app is not None:
        try:
            if mode == "system":
                app.styleHints().unsetColorScheme()
            else:
                app.styleHints().setColorScheme(Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)
        except AttributeError:
            pass
        app.setStyleSheet(build_qss(theme))
        for w in app.topLevelWidgets():
            if w.isVisible():
                set_title_bar(w, theme == "dark")
    return theme


def toggle_icon(theme: str, size: int = 40) -> QIcon:
    """Icon for the header button: a moon while light (click for dark), a sun while dark (click for light)."""
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(size / 2, size / 2)
    white = QColor("#FFFFFF")
    if theme == "light":  # moon
        r = size * 0.30
        moon = QPainterPath()
        moon.addEllipse(c, r, r)
        bite = QPainterPath()
        bite.addEllipse(QPointF(c.x() + r * 0.62, c.y() - r * 0.48), r * 0.92, r * 0.92)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawPath(moon.subtracted(bite))
    else:  # sun
        import math
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFD66B"))
        p.drawEllipse(c, size * 0.17, size * 0.17)
        p.setPen(QPen(QColor("#FFD66B"), size * 0.07, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * size * 0.27, c.y() + math.sin(a) * size * 0.27),
                       QPointF(c.x() + math.cos(a) * size * 0.37, c.y() + math.sin(a) * size * 0.37))
    p.end()
    return QIcon(pm)
