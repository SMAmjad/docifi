"""Environment check and secure-delete dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from ..environment import ToolStatus, check_environment
from ..secure_delete import SecureDeleteRefused, check_folder, secure_delete
from . import theme
from .style import THEMES


def palette() -> dict[str, str]:
    """Colours of the theme currently in effect."""
    return THEMES[theme.current()]


def _label(text: str, cls: str | None = None, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    if cls:
        lab.setProperty("class", cls)
    return lab


class EnvironmentDialog(QDialog):
    """First-run / on-demand check of the programs the tool relies on."""

    def __init__(self, parent=None, tesseract_configured: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Environment check")
        self.setMinimumWidth(640)
        self.results: list[ToolStatus] = check_environment(tesseract_configured)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        title = _label("Is this computer ready?", "cardTitle")
        lay.addWidget(title)
        lay.addWidget(_label("The tool works fully offline. It uses these programs on this computer:", "hint"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for r, t in enumerate(self.results):
            ok = t.found
            badge = QLabel("Ready" if ok else ("Missing" if t.required else "Optional"))
            color = ("#C6EFCE", "#006100") if ok else (("#FFC7CE", "#9C0006") if t.required else ("#FFEB9C", "#9C5700"))
            badge.setStyleSheet(f"background:{color[0]}; color:{color[1]}; border-radius:4px; padding:3px 10px; font-weight:600;")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(84)
            grid.addWidget(badge, r, 0, Qt.AlignmentFlag.AlignTop)
            text = f"<b>{t.name}</b><br><span style='color:{palette()['muted']}'>{t.detail}</span>"
            if t.fix:
                text += f"<br>{t.fix}"
            info = QLabel(text)
            info.setWordWrap(True)
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(info, r, 1)
        lay.addLayout(grid)

        ready = all(t.found or not t.required for t in self.results)
        summary = _label("All required programs are installed. You can start." if ready else
                         "A required program is missing. Install it with the command shown, then restart the app.")
        summary.setStyleSheet(f"font-weight:600; color:{palette()['kpi_correct' if ready else 'kpi_issue']};")
        lay.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    @property
    def ready(self) -> bool:
        return all(t.found or not t.required for t in self.results)


class SecureDeleteDialog(QDialog):
    """Confirm and run secure deletion of an output folder created by this tool."""

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.deleted = False
        self.setWindowTitle("Securely delete results")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        n_files = sum(1 for p in self.folder.rglob("*") if p.is_file())
        size_mb = sum(p.stat().st_size for p in self.folder.rglob("*") if p.is_file()) / 1_048_576

        lay.addWidget(_label("Permanently delete these results?", "cardTitle"))
        box = QFrame()
        box.setProperty("class", "card")
        bl = QVBoxLayout(box)
        path_lab = _label(f"<b>Folder:</b> {self.folder}")
        path_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bl.addWidget(path_lab)
        bl.addWidget(_label(f"<b>Contains:</b> {n_files} files ({size_mb:.1f} MB) - labeled documents, check sheets, "
                            "logs and the reading cache."))
        lay.addWidget(box)
        lay.addWidget(_label("Each file is overwritten with random data and then removed. This cannot be undone. "
                             "Your original files in the input folder are not touched.", "hint"))
        lay.addWidget(_label("Note: on SSD drives overwriting cannot guarantee that every physical copy is erased; "
                             "full-disk encryption (BitLocker) is the reliable protection.", "hint"))
        lay.addWidget(_label(f"To confirm, type the folder name: <b>{self.folder.name}</b>"))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(self.folder.name)
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.delete_btn = QPushButton("Delete permanently")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(cancel)
        row.addWidget(self.delete_btn)
        lay.addLayout(row)
        self.edit.textChanged.connect(lambda t: self.delete_btn.setEnabled(t.strip() == self.folder.name))

    def _delete(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rep = secure_delete(self.folder, self.edit.text())
        except SecureDeleteRefused as exc:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Not deleted", str(exc))
            return
        finally:
            if QGuiApplication.overrideCursor():
                QGuiApplication.restoreOverrideCursor()
        self.deleted = True
        msg = f"{rep.files} files ({rep.bytes / 1_048_576:.1f} MB) were securely deleted."
        if rep.failures:
            msg += f"\n{rep.failures} items could not be removed (a file may be open in another program)."
        QMessageBox.information(self, "Deleted", msg)
        self.accept()


def can_secure_delete(folder: Path) -> str | None:
    """None if the folder may be deleted, otherwise the plain-English reason."""
    try:
        check_folder(folder)
        return None
    except SecureDeleteRefused as exc:
        return str(exc)
