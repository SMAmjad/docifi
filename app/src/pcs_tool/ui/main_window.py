"""Main window: choose folders, run, watch progress, review results."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QCloseEvent, QColor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication, QIcon,
    QKeySequence, QPainter,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_FULL_NAME, APP_NAME, APP_PUBLISHER, APP_TAGLINE, __version__
from ..config import load_config
from ..engine.pipeline import PipelineError, validate_inputs
from ..models import RunResult
from . import theme
from .brand import paint_mark, paint_wordmark
from .dialogs import EnvironmentDialog, SecureDeleteDialog, can_secure_delete
from .style import STATUS_COLORS, SYMBOL, THEMES
from .worker import PipelineWorker


def _card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("class", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setProperty("class", "cardTitle")
        lay.addWidget(t)
    return frame, lay


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("class", "hint")
    lab.setWordWrap(True)
    return lab


def default_output_dir() -> Path:
    """The app's own 'output' folder: next to DociFi.exe, or the project root when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "output"
    return Path(__file__).resolve().parents[4] / "output"


def _open(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class HeaderBrand(QWidget):
    """Logo mark + 'DociFi' word mark + what the app does, painted as vectors (sharp at any scaling)."""

    SUBTITLE = f"{APP_TAGLINE}  ·  by {APP_PUBLISHER}"

    def __init__(self):
        super().__init__()
        self._font = QFont("Segoe UI")
        self._font.setPixelSize(12)
        width = 64 + QFontMetricsF(self._font).horizontalAdvance(self.SUBTITLE) + 24  # never clipped
        self.setFixedSize(int(max(width, 240)), 58)
        self.setToolTip(f"{APP_FULL_NAME} {__version__}")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_mark(p, QRectF(0, 4, 50, 50))
        paint_wordmark(p, 62, 33, 27)
        p.setFont(self._font)
        p.setPen(QColor("#C9D3E6"))
        p.drawText(QPointF(64, 52), self.SUBTITLE)
        p.end()


class FolderRow(QWidget):
    def __init__(self, label: str, placeholder: str, on_browse):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(label)
        lab.setFixedWidth(120)
        lab.setStyleSheet("font-weight:600;")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setReadOnly(True)
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(on_browse)
        lay.addWidget(lab)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.browse)

    @property
    def path(self) -> Path | None:
        t = self.edit.text().strip()
        return Path(t) if t else None


def pick_folders(parent: QWidget, start: str = "") -> list[str]:
    """Folder picker that allows choosing several folders at once (Ctrl / Shift + click).

    The Windows folder dialog accepts only one folder, so Qt's own dialog is used with
    multi-selection switched on in its folder list.
    """
    dlg = QFileDialog(parent, "Choose the documents folder(s) - hold Ctrl or Shift to choose several", start)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    for view in dlg.findChildren(QAbstractItemView):
        if view.objectName() in ("listView", "treeView"):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    dlg.resize(820, 520)
    if not dlg.exec():
        return []
    chosen = [f for f in dlg.selectedFiles() if Path(f).is_dir()]
    # With several folders selected Qt also returns the folder that was open (their parent): drop it.
    if len(chosen) > 1:
        chosen = [f for f in chosen if not any(Path(o) != Path(f) and Path(o).is_relative_to(Path(f)) for o in chosen)]
    return chosen


class InputFoldersRow(QWidget):
    """'Documents folders': one or many folders, shown as a list; Browse adds, Remove takes out."""

    def __init__(self, on_browse, on_change):
        super().__init__()
        self._on_change = on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("Documents folders")
        lab.setFixedWidth(120)
        lab.setStyleSheet("font-weight:600;")
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lab.setContentsMargins(0, 7, 0, 0)
        self.list = QListWidget()
        self.list.setObjectName("folderList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setFixedHeight(86)
        self.list.setToolTip("The folders that will be processed together in one run. "
                             "Each folder gets its own check sheet.")
        btns = QVBoxLayout()
        btns.setSpacing(6)
        self.browse = QPushButton("Browse…")
        self.browse.setToolTip("Add one or more folders (hold Ctrl or Shift to choose several)")
        self.browse.clicked.connect(on_browse)
        self.remove = QPushButton("Remove")
        self.remove.setToolTip("Remove the selected folder(s) from the list")
        self.remove.clicked.connect(self._remove_selected)
        btns.addWidget(self.browse)
        btns.addWidget(self.remove)
        btns.addStretch(1)
        lay.addWidget(lab)
        lay.addWidget(self.list, 1)
        lay.addLayout(btns)
        self._refresh()

    @property
    def paths(self) -> list[Path]:
        return [Path(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())
                if self.list.item(i).data(Qt.ItemDataRole.UserRole)]

    def add(self, folders: list[str]) -> None:
        have = {str(p.resolve()).lower() for p in self.paths}
        for f in folders:
            if str(Path(f).resolve()).lower() not in have:
                have.add(str(Path(f).resolve()).lower())
                it = QListWidgetItem(str(Path(f)))
                it.setData(Qt.ItemDataRole.UserRole, str(Path(f)))
                it.setToolTip(str(Path(f)))
                self.list.addItem(it)
        self._refresh()

    def clear(self) -> None:
        self.list.clear()
        self._refresh()

    def _remove_selected(self) -> None:
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._refresh()

    def _refresh(self) -> None:
        self.remove.setEnabled(bool(self.paths))
        self._on_change()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("PointCapitalServices", "DociFi")
        self.cfg = load_config()
        self.thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.result: RunResult | None = None
        self.output_root: Path | None = None
        self.setWindowTitle(APP_FULL_NAME)
        # Fits a 1366x768 laptop at 125% scaling (about 1093x614 usable) - the smallest common office screen.
        avail = self.screen().availableGeometry() if self.screen() else None
        self.setMinimumSize(900, 560)
        if avail:
            self.resize(min(1280, int(avail.width() * 0.92)), min(860, int(avail.height() * 0.92)))
        else:
            self.resize(1280, 860)
        icon = Path(__file__).parent / "assets" / "app.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 14, 22, 18)
        self.tabs = QTabWidget()
        scroll = QScrollArea()  # on small laptop screens the steps scroll instead of being squeezed
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._process_tab())
        self.tabs.addTab(scroll, "1   Process documents")
        self.tabs.addTab(self._results_tab(), "2   Results")
        self.tabs.setTabEnabled(1, False)
        bl.addWidget(self.tabs)
        outer.addWidget(body, 1)
        self.statusBar().showMessage("Ready. All processing happens on this computer.")
        self._build_menu()
        self.theme_mode = str(self.settings.value("theme", "system"))
        self._set_theme(self.theme_mode)
        # follow Windows when the user switches its light/dark setting while DociFi is open
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        self._set_tab_order()
        self._restore()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        QTimer.singleShot(250, self._first_run_check)

    # ------------------------------------------------------------------ layout
    def _header(self) -> QWidget:
        h = QFrame()
        h.setObjectName("header")
        h.setFixedHeight(78)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(24, 10, 18, 10)
        lay.addWidget(HeaderBrand())
        lay.addStretch(1)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("themeButton")
        self.theme_btn.setFixedSize(38, 38)
        self.theme_btn.setIconSize(QSize(26, 26))
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return h

    def _process_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(14)

        card, cl = _card("Step 1 - Choose folders")
        self.input_row = InputFoldersRow(self._browse_input, lambda: None)
        self.output_row = FolderRow("Results folder", "Where the labeled documents and the check sheet will be saved",
                                    self._browse_output)
        self.output_default_btn = QPushButton("Default")
        self.output_default_btn.setToolTip(f"Use the app's own results folder:\n{default_output_dir()}")
        self.output_default_btn.clicked.connect(self._use_default_output)
        self.output_row.layout().addWidget(self.output_default_btn)
        self.output_row.browse.setToolTip("Choose any folder on this PC for the results")
        cl.addWidget(self.input_row)
        cl.addWidget(self.output_row)
        cl.addWidget(_hint("Your original files are never changed, moved or deleted. Sub-folders are included. "
                           "Several folders are processed together in one run; each gets its own folder and check "
                           "sheet inside the results."))
        lay.addWidget(card)

        card, cl = _card("Step 2 - Options")
        self.redact = QCheckBox("Hide names, dates of birth and addresses in the check sheet")
        cl.addWidget(self.redact)
        cl.addWidget(_hint("Use this for a copy you will share. SSNs are always hidden (only the last 4 digits are shown)."))
        grow = QHBoxLayout()
        self.selftest = QCheckBox("Also test the naming rules against guide/rules:")
        self.guide_edit = QLineEdit()
        self.guide_edit.setReadOnly(True)
        self.guide_edit.setPlaceholderText("Optional - choose a guide/rules file (.xlsx)")
        self.guide_btn = QPushButton("Browse…")
        self.guide_btn.clicked.connect(self._browse_guide)
        grow.addWidget(self.selftest)
        grow.addWidget(self.guide_edit, 1)
        grow.addWidget(self.guide_btn)
        cl.addLayout(grow)
        cl.addWidget(_hint("Optional. Documents are always named with DociFi's built-in rules - no guide is needed. "
                           "Tick this only if you want an extra report comparing those rules with your own guide."))
        lay.addWidget(card)

        card, cl = _card("Step 3 - Run")
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)
        row.addSpacing(16)
        self.stage = QLabel("Waiting to start.")
        self.stage.setProperty("class", "muted")
        row.addWidget(self.stage, 1)
        cl.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        cl.addWidget(self.bar)
        self.log = QListWidget()
        self.log.setMinimumHeight(110)
        self.log.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # read-only activity list: skip it when tabbing
        cl.addWidget(self.log, 1)
        lay.addWidget(card, 1)
        return w

    def _results_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(12)

        kpis = QHBoxLayout()
        self.kpi: dict[str, QLabel] = {}
        self._kpi_colors: dict[str, str] = {}
        for key, label, color in (("docs", "Documents checked", "accent"), ("Correct", "Correct", "kpi_correct"),
                                  ("Issue", "Issue - documents disagree", "kpi_issue"),
                                  ("Check manually", "Check manually", "kpi_check"), ("people", "People", "accent")):
            card, cl = _card()
            v = QLabel("–")
            v.setProperty("class", "kpiValue")
            self._kpi_colors[key] = color
            cl.addWidget(v)
            cl.addWidget(_hint(label))
            self.kpi[key] = v
            kpis.addWidget(card)
        lay.addLayout(kpis)

        bar = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(["Everything", "Issue", "Check manually", "Correct"])
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search document, person or reason…")
        self.search.textChanged.connect(self._apply_filter)
        self.folder_filter = QComboBox()  # shown only when several folders were processed
        self.folder_filter.setMinimumWidth(150)
        self.folder_filter.currentIndexChanged.connect(self._apply_filter)
        self.folder_label = QLabel("Folder:")
        bar.addWidget(QLabel("Show:"))
        bar.addWidget(self.filter)
        bar.addWidget(self.folder_label)
        bar.addWidget(self.folder_filter)
        bar.addWidget(self.search, 1)
        lay.addLayout(bar)
        self.folder_label.hide()
        self.folder_filter.hide()

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(["Result", "Document (double-click to open)", "Type", "Person",
                                              "Name", "Date of birth", "SSN", "Home address", "What to review",
                                              "Folder", "Role on document"])
        hdr = self.table.horizontalHeader()
        # Shown order: Result, Folder, Document, Type, Person, Role, Name ... (column numbers in code stay the same)
        hdr.moveSection(9, 1)
        hdr.moveSection(10, 5)
        self.table.setColumnWidth(9, 120)
        self.table.setColumnWidth(10, 230)
        self.table.setColumnHidden(9, True)
        for c, width in enumerate((118, 320, 170, 130, 96, 96, 104, 104)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, width)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        hdr.setMinimumSectionSize(60)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)  # one clean line per document; the full reason is in the tooltip
        self.table.setAlternatingRowColors(False)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemSelectionChanged.connect(self._show_details)
        lay.addWidget(self.table, 1)

        self.details = QLabel("Select a row to see everything the tool found for that document.")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setObjectName("details")
        self.details.setMinimumHeight(64)
        lay.addWidget(self.details)

        btns = QHBoxLayout()
        self.open_sheet_btn = QPushButton("Open Excel check sheet")
        self.open_sheet_btn.setObjectName("primary")
        self.open_sheet_btn.clicked.connect(self._open_sheet)
        self.open_docs_btn = QPushButton("Open labeled documents")
        self.open_docs_btn.clicked.connect(self._open_documents)
        self.open_out_btn = QPushButton("Open results folder")
        self.open_out_btn.clicked.connect(lambda: self.result and _open(self.result.run_dir))
        self.delete_btn = QPushButton("Securely delete results…")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._secure_delete)
        for b in (self.open_sheet_btn, self.open_docs_btn, self.open_out_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.delete_btn)
        lay.addLayout(btns)
        return w

    def _build_menu(self) -> None:
        """Menu bar holds only what is not already on screen; on-screen buttons keep their keyboard shortcuts."""
        mb = self.menuBar()

        def act(menu, text, slot, shortcut=None, tip=""):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            (menu or self).addAction(a)  # menu=None: keyboard shortcut only, not listed twice on screen
            return a

        f = mb.addMenu("&File")
        self.act_new = act(f, "&New check", self._new_check, "Ctrl+N", "Clear the screen and start with another borrower")
        f.addSeparator()
        act(f, "E&xit", self.close, "Ctrl+Q")

        v = mb.addMenu("&View")
        tm = v.addMenu("&Appearance")
        group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for mode, text in (("system", "Same as &Windows"), ("light", "&Light"), ("dark", "&Dark")):
            a = QAction(text, self, checkable=True)
            a.triggered.connect(lambda _c=False, m=mode: self._set_theme(m))
            group.addAction(a)
            tm.addAction(a)
            self.theme_actions[mode] = a

        h = mb.addMenu("&Help")
        act(h, "&How to use", self._help, "F1")
        act(h, "&Environment check…", lambda: EnvironmentDialog(self).exec(),
            tip="Check that the programs DociFi uses are installed")
        h.addSeparator()
        act(h, f"&About {APP_NAME}", self._about)

        # Keyboard shortcuts for the buttons on screen.
        act(None, "Choose documents folder", self._browse_input, "Ctrl+O")
        act(None, "Choose results folder", self._browse_output, "Ctrl+Shift+O")
        self.act_start = act(None, "Start", self._start, "Ctrl+R")
        self.act_sheet = act(None, "Open check sheet", self._open_sheet, "Ctrl+E")
        self.act_folder = act(None, "Open results folder", lambda: self.result and _open(self.result.run_dir))
        act(None, "Process documents", lambda: self.tabs.setCurrentIndex(0), "Ctrl+1")
        act(None, "Results", lambda: self.tabs.isTabEnabled(1) and self.tabs.setCurrentIndex(1), "Ctrl+2")
        act(None, "Switch light/dark", self._toggle_theme, "Ctrl+Shift+L")
        self._update_actions()

    # ------------------------------------------------------------------ appearance
    def _set_theme(self, mode: str) -> None:
        self.theme_mode = mode if mode in theme.MODES else "system"
        active = theme.apply(self.theme_mode)
        self.settings.setValue("theme", self.theme_mode)
        self.theme_actions[self.theme_mode].setChecked(True)
        self.theme_btn.setIcon(theme.toggle_icon(active))
        self.theme_btn.setToolTip("Switch to dark mode (Ctrl+Shift+L)" if active == "light"
                                  else "Switch to light mode (Ctrl+Shift+L)")
        colors = THEMES[active]
        for key, lab in self.kpi.items():
            lab.setStyleSheet(f"color:{colors[self._kpi_colors[key]]};")

    def _toggle_theme(self) -> None:
        self._set_theme("light" if theme.current() == "dark" else "dark")

    def _on_system_scheme_changed(self, *_a) -> None:
        if self.theme_mode == "system":
            self._set_theme("system")

    def _new_check(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, "Processing is running", "Please wait for the current run to finish, "
                                    "or cancel it first.")
            return
        self.input_row.clear()
        self._use_default_output()
        self.redact.setChecked(False)
        self.result = None
        self.output_root = None
        self._rows = []
        self.table.setRowCount(0)
        for lab in self.kpi.values():
            lab.setText("–")
        self.details.setText("Select a row to see everything the tool found for that document.")
        self.log.clear()
        self.bar.setValue(0)
        self.stage.setText("Waiting to start.")
        self.tabs.setTabEnabled(1, False)
        self.tabs.setCurrentIndex(0)
        self._update_actions()
        self.statusBar().showMessage("Ready for a new check.")

    def _update_actions(self) -> None:
        has = self.result is not None
        for a in (self.act_sheet, self.act_folder):
            a.setEnabled(has)

    def _help(self) -> None:
        QMessageBox.information(self, "How to use", (
            "<b>1. Choose folders</b> - the folder with the borrower's documents, and a folder for the results.<br>"
            "<b>2. Options</b> - tick 'Hide names…' only for a copy you will share.<br>"
            "<b>3. Start</b> - the tool converts, splits, names and checks every document.<br><br>"
            "<b>Results</b> - <span style='color:#9C0006'>Issue</span> = documents disagree; "
            "<span style='color:#9C5700'>Check manually</span> = the tool could not read a value; "
            "<span style='color:#006100'>Correct</span> = nothing to do. Double-click a row to open the document.<br><br>"
            "When you are finished, use <b>Securely delete results</b> on the Results tab.<br><br>"
            "<b>Keyboard</b> - Ctrl+O documents folder · Ctrl+Shift+O results folder · Ctrl+R start · "
            "Ctrl+E open check sheet · Ctrl+1 / Ctrl+2 switch tabs · Ctrl+Shift+L light/dark · Ctrl+N new check."))

    def _about(self) -> None:
        muted = THEMES[theme.current()]["muted"]
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<h3>{APP_NAME} <span style='font-weight:normal;color:{muted}'>- {APP_TAGLINE}</span></h3>"
            f"<p>Version {__version__} &nbsp;·&nbsp; by {APP_PUBLISHER}</p>"
            "<p>Converts, splits, names and checks mortgage loan documents (Name, Date of birth, SSN, Address).</p>"
            "<p>All processing happens on this computer. No document or value is sent to the internet.</p>"
            f"<p style='color:{muted}'>Uses Tesseract OCR (Apache 2.0), RapidOCR / ONNX Runtime (Apache 2.0 / MIT), "
            "Qt for Python (LGPL), PyMuPDF (AGPL - see licensing note in the documentation).</p>"))

    def _set_tab_order(self) -> None:
        """Keyboard users move through the screen in reading order: step 1, step 2, step 3, then results."""
        chain = [self.input_row.browse, self.input_row.remove, self.output_row.browse, self.output_default_btn, self.redact, self.selftest, self.guide_btn,
                 self.start_btn, self.cancel_btn, self.filter, self.search, self.table, self.open_sheet_btn,
                 self.open_docs_btn, self.open_out_btn, self.delete_btn]
        for a, b in zip(chain, chain[1:]):
            self.setTabOrder(a, b)
        for edit in (self.output_row.edit, self.guide_edit):
            edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # read-only path boxes: chosen with Browse
        self.start_btn.setDefault(True)

    # ------------------------------------------------------------------ settings
    def _restore(self) -> None:
        # Every start is clean: no documents folder is pre-filled, results go to the app's own folder.
        for old in ("input", "inputs", "output"):
            self.settings.remove(old)
        self._use_default_output()
        g = self.settings.value("guide", "")
        if g and Path(g).exists():
            self.guide_edit.setText(g)
            self.selftest.setChecked(self.settings.value("selftest", "false") == "true")

    def _save(self) -> None:
        self.settings.setValue("guide", self.guide_edit.text())
        self.settings.setValue("selftest", "true" if self.selftest.isChecked() else "false")

    def _first_run_check(self) -> None:
        if self.settings.value("env_ok", "false") == "true":
            return
        dlg = EnvironmentDialog(self, self.cfg.settings.get("ocr.tesseract_path", "") or "")
        if not dlg.ready:
            dlg.exec()
        else:
            self.settings.setValue("env_ok", "true")

    # ------------------------------------------------------------------ browsing
    def _browse_input(self) -> None:
        have = self.input_row.paths
        chosen = pick_folders(self, str(have[-1].parent) if have else "")
        if chosen:
            self.input_row.add(chosen)

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the results folder", self.output_row.edit.text())
        if d:
            self.output_row.edit.setText(str(Path(d)))

    def _use_default_output(self) -> None:
        self.output_row.edit.setText(str(default_output_dir()))

    def _browse_guide(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Choose the guide/rules file", self.guide_edit.text(),
                                           "Excel files (*.xlsx)")
        if not f:
            return
        from ..engine.selftest import load_guide
        try:
            n = len(load_guide(Path(f)))
        except Exception:  # noqa: BLE001 - any unreadable or differently laid-out workbook
            n = 0
        if n == 0:
            QMessageBox.warning(self, "Cannot use this file", (
                "This file does not look like a naming guide. A guide needs a first sheet with the columns "
                "'Document Name', 'Type', 'Label' and 'Proposed filename'.\n\n"
                "The naming test is optional - you can run without it."))
            return
        self.guide_edit.setText(f)
        self.selftest.setChecked(True)
        self.statusBar().showMessage(f"Guide/rules file loaded: {n} example names.")

    # ------------------------------------------------------------------ run
    def _start(self) -> None:
        inp, out = self.input_row.paths, self.output_row.path
        if not inp or not out:
            QMessageBox.information(self, "Choose folders", "Please choose the documents folder(s) and the results folder.")
            return
        missing = [p for p in inp if not p.is_dir()]
        if missing:
            QMessageBox.warning(self, "Cannot start", f"This folder no longer exists:\n{missing[0]}\n\n"
                                "Remove it from the list and try again.")
            return
        try:
            validate_inputs(inp, out)
        except PipelineError as exc:
            QMessageBox.warning(self, "Cannot start", str(exc))
            return
        if self.selftest.isChecked() and not self.guide_edit.text():
            QMessageBox.information(self, "No guide/rules file", "The naming test is ticked but no guide/rules file "
                                    "is chosen. Choose one with Browse, or untick the option - it is not required.")
            return
        guide = Path(self.guide_edit.text()) if self.selftest.isChecked() and self.guide_edit.text() else None
        self._save()
        self.log.clear()
        self.bar.setValue(0)
        self._set_running(True)
        self.output_root = out
        self.thread = QThread(self)
        self.worker = PipelineWorker(inp if len(inp) > 1 else inp[0], out, self.redact.isChecked(), guide)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        for sig in (self.worker.finished, self.worker.failed, self.worker.cancelled):
            sig.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        self._log("Started. Large scans can take a few seconds per page the first time; repeat runs are fast.")

    def _set_running(self, running: bool) -> None:
        self.act_start.setEnabled(not running)
        self.act_new.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.input_row.setEnabled(not running)
        self.output_row.setEnabled(not running)
        self.output_default_btn.setEnabled(not running)
        self.redact.setEnabled(not running)
        self.selftest.setEnabled(not running)
        self.guide_btn.setEnabled(not running)
        self.delete_btn.setEnabled(not running)

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.stage.setText("Stopping after the current page…")

    def _log(self, text: str) -> None:
        self.log.addItem(text)
        self.log.scrollToBottom()

    def _on_progress(self, stage: str, done: int, total: int, msg: str, pct: int) -> None:
        if pct >= 0:
            self.bar.setValue(pct)
        self.stage.setText(f"{stage}  ({done}/{total})" if total > 1 else stage)
        if stage != getattr(self, "_last_stage", None) or done == total:
            self._log(f"{stage}: {msg}")
            self._last_stage = stage

    def _on_failed(self, message: str, details: str) -> None:
        self._set_running(False)
        self.stage.setText("Stopped because of a problem.")
        box = QMessageBox(QMessageBox.Icon.Warning, "Something went wrong", message, parent=self)
        if details:
            box.setDetailedText(details)
        box.exec()

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.bar.setValue(0)
        self.stage.setText("Cancelled. Nothing was changed in your documents folder.")
        self._log("Cancelled by user.")

    def _on_finished(self, result: RunResult) -> None:
        self._set_running(False)
        self.result = result
        self.bar.setValue(100)
        s = result.stats
        self.stage.setText(f"Finished in {result.duration_s:.0f} seconds.")
        self._log(f"Done: {s['documents']} documents - {s['documents_correct']} correct, {s['documents_issue']} with "
                  f"issues, {s['documents_check']} to check manually.")
        self._fill_results(result)
        self._update_actions()
        self.tabs.setTabEnabled(1, True)
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(f"Results saved in {result.run_dir}")

    # ------------------------------------------------------------------ results
    def _fill_results(self, result: RunResult) -> None:
        from ..engine.report import document_rows
        people = [v["person"] for v in result.persons.values()]
        rows = document_rows(result, people, self.cfg, redact=self.redact.isChecked())
        s = result.stats
        self.kpi["docs"].setText(str(s["documents"]))
        self.kpi["Correct"].setText(str(s["documents_correct"]))
        self.kpi["Issue"].setText(str(s["documents_issue"]))
        self.kpi["Check manually"].setText(str(s["documents_check"]))
        self.kpi["people"].setText(str(s["people"]))
        self.table.setRowCount(len(rows))
        self._rows = rows
        multi = len(result.sheets) > 1
        self.table.setColumnHidden(9, not multi)
        self.folder_filter.blockSignals(True)
        self.folder_filter.clear()
        self.folder_filter.addItems(["All folders"] + list(result.sheets))
        self.folder_filter.blockSignals(False)
        self.folder_label.setVisible(multi)
        self.folder_filter.setVisible(multi)
        self.open_sheet_btn.setText("Open Excel check sheet" + (" ▾" if multi else ""))
        self.open_docs_btn.setText("Open labeled documents" + (" ▾" if multi else ""))
        tip = "Opens the folder of the selected row (or the Folder filter); otherwise choose the folder from a list."
        self.open_sheet_btn.setToolTip(tip if multi else "")
        self.open_docs_btn.setToolTip(tip if multi else "")
        for r, row in enumerate(rows):
            res = row["result"].value
            values = [res, row["title"], row["type"], row["person"]]
            for c, v in enumerate(values):
                it = QTableWidgetItem(v)
                if c == 0:
                    self._paint(it, res, res)
                self.table.setItem(r, c, it)
            for c, key in enumerate(("name", "dob", "ssn", "address"), start=4):
                st = row[key].value
                it = QTableWidgetItem(SYMBOL[st])
                self._paint(it, st, SYMBOL[st])
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, c, it)
            self.table.setItem(r, 8, QTableWidgetItem(row["reasons"].replace("• ", "").replace("\n", "  •  ")))
            self.table.setItem(r, 9, QTableWidgetItem(row.get("folder") or ""))
            role = QTableWidgetItem(row.get("role") or "")
            role.setToolTip(row.get("role") or "")
            self.table.setItem(r, 10, role)
            self.table.item(r, 1).setToolTip(row["title"])
            self.table.item(r, 8).setToolTip(row["reasons"] or "Nothing to review.")
        self._apply_filter()

    @staticmethod
    def _paint(item: QTableWidgetItem, status: str, text: str) -> None:
        bg, fg = STATUS_COLORS.get(status, ("#FFFFFF", "#000000"))
        item.setText(text)
        item.setBackground(QColor(bg))
        item.setForeground(QColor(fg))
        f = item.font()
        f.setBold(status != "Not applicable")
        item.setFont(f)

    def _apply_filter(self) -> None:
        want = self.filter.currentText()
        folder = self.folder_filter.currentText() if not self.folder_filter.isHidden() else ""
        q = self.search.text().strip().lower()
        for r, row in enumerate(getattr(self, "_rows", [])):
            show = (want == "Everything" or row["result"].value == want)
            if folder and folder != "All folders":
                show &= row.get("folder") == folder
            if q:
                show &= q in " ".join([row["title"], row["type"], row["person"], row.get("role", ""),
                                       row["reasons"]]).lower()
            self.table.setRowHidden(r, not show)

    def _show_details(self) -> None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows or not hasattr(self, "_rows"):
            return
        row = self._rows[min(rows)]
        reasons = row["reasons"].replace("\n", "<br>") or "Nothing to review - every value matches the person's other documents."
        role = f" ({row['role']})" if row.get("role") else ""
        self.details.setText(f"<b>{row['title']}</b> &nbsp;·&nbsp; {row['type']} &nbsp;·&nbsp; {row['person']}{role} "
                             f"&nbsp;·&nbsp; <b>{row['result'].value}</b><br>{reasons}")

    def _chosen_folder(self) -> str | None:
        """Several folders: the folder of the selected row, else the one chosen in the Folder filter."""
        sel = {i.row() for i in self.table.selectedItems()}
        if sel and getattr(self, "_rows", None):
            return self._rows[min(sel)].get("folder") or None
        if self.result and self.folder_filter.currentText() in self.result.sheets:
            return self.folder_filter.currentText()
        return None

    def _for_folder(self, button: QPushButton, action) -> None:
        """Run ``action(folder)`` for the selected folder; with none selected, let the user pick one from a menu."""
        folder = self._chosen_folder()
        if folder:
            action(folder)
            return
        menu = QMenu(self)
        for name in self.result.sheets:
            menu.addAction(name, lambda n=name: action(n))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _open_sheet(self) -> None:
        """One folder: its check sheet. Several: the chosen folder's check sheet."""
        if not self.result:
            return
        if len(self.result.sheets) > 1:
            self._for_folder(self.open_sheet_btn, lambda f: _open(self.result.sheets[f]))
        elif self.result.sheet_path:
            _open(self.result.sheet_path)

    def _open_documents(self) -> None:
        """One folder: run/documents. Several: the chosen folder's documents."""
        if not self.result:
            return
        if len(self.result.sheets) > 1:
            def go(folder: str) -> None:
                target = self.result.run_dir / folder / "documents"
                _open(target if target.exists() else self.result.run_dir / folder)
            self._for_folder(self.open_docs_btn, go)
        else:
            target = self.result.run_dir / "documents"
            _open(target if target.exists() else self.result.run_dir)

    def _open_row(self, r: int, _c: int) -> None:
        path = self._rows[r].get("path") if hasattr(self, "_rows") else None
        if path and Path(path).exists():
            _open(Path(path))
        else:
            QMessageBox.information(self, "Not available", "This document cannot be opened from here "
                                    "(personal values are hidden, or the file was not saved).")

    def _secure_delete(self) -> None:
        folder = self.output_root or self.output_row.path
        if not folder:
            return
        reason = can_secure_delete(folder)
        if reason:
            QMessageBox.warning(self, "Cannot delete", reason)
            return
        dlg = SecureDeleteDialog(folder, self)
        if dlg.exec() and dlg.deleted:
            self.result = None
            self._update_actions()
            self.table.setRowCount(0)
            self.tabs.setTabEnabled(1, False)
            self.tabs.setCurrentIndex(0)
            self.statusBar().showMessage("Results were securely deleted.")

    # ------------------------------------------------------------------ close
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread and self.thread.isRunning():
            ans = QMessageBox.question(self, "Processing is running",
                                       "Documents are still being processed. Stop and close?")
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.worker:
                self.worker.cancel()
            self.thread.quit()
            self.thread.wait(15000)
        self._save()
        self.settings.setValue("geometry", self.saveGeometry())
        event.accept()
