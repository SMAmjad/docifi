"""Desktop entry point:  docifi   (or  python -m pcs_tool.ui.app).

Start-up: the animated splash appears at once; meanwhile the heavier parts of the app
(PDF, OCR and Excel libraries) load on a background thread. The main window opens as
soon as both the animation and the loading have finished.
"""

from __future__ import annotations

import importlib
import multiprocessing
import sys
import threading


def main() -> int:
    multiprocessing.freeze_support()  # required for the parallel page readers inside a packaged .exe
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .. import APP_FULL_NAME, APP_NAME
    from . import theme
    from .splash import HANDOFF_T, SplashScreen
    from .style import ASSETS

    if sys.platform == "win32":
        try:  # own taskbar icon instead of the Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PointCapitalServices.DociFi.1")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_FULL_NAME)
    app.setOrganizationName("PointCapitalServices")
    app.setWindowIcon(QIcon(str(ASSETS / "app.ico")))
    app.setFont(QFont("Segoe UI", 10))
    theme.apply(str(QSettings("PointCapitalServices", "DociFi").value("theme", "system")))

    try:  # inside the packaged .exe its loader already shows the first splash frame while unpacking
        import pyi_splash  # type: ignore
    except ImportError:
        pyi_splash = None

    splash = SplashScreen()
    splash.start(HANDOFF_T if pyi_splash else 0.0)
    if pyi_splash:
        QTimer.singleShot(60, pyi_splash.close)  # after the animated splash has painted on top of it

    state: dict = {"loaded": False, "animated": False, "error": None, "window": None}

    def load() -> None:
        try:
            importlib.import_module("pcs_tool.ui.main_window")
            importlib.import_module("pcs_tool.engine.pipeline")
        except Exception as exc:  # noqa: BLE001 - reported on screen below
            state["error"] = exc
        state["loaded"] = True

    threading.Thread(target=load, name="docifi-loader", daemon=True).start()
    splash.animation_done.connect(lambda: state.update(animated=True))

    timer = QTimer()
    timer.setInterval(25)

    def ready() -> None:
        if not (state["loaded"] and state["animated"]):
            return
        timer.stop()
        if state["error"]:
            splash.close()
            QMessageBox.critical(None, APP_NAME, f"{APP_NAME} could not start.\n\n{state['error']}")
            app.quit()
            return
        from .main_window import MainWindow
        win = MainWindow()
        state["window"] = win
        win.show()
        win.raise_()
        win.activateWindow()
        splash.fade_out_and_close()

    timer.timeout.connect(ready)
    timer.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
