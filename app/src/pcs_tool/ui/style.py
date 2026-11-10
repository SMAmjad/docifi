"""Colours and the Qt style sheet, for the light and the dark theme (one place, so the look can be re-branded)."""

from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
CHECK_IMG = (ASSETS / "check.png").as_posix()

# Brand colours - the same in both themes.
NAVY = "#1F3864"
NAVY_DARK = "#16294A"
BLUE = "#2E75B6"

LIGHT = {
    "bg": "#F4F6FA", "card": "#FFFFFF", "border": "#D9DEE7", "text": "#1E2430", "muted": "#5F6B7A",
    "input": "#FFFFFF", "input_ro": "#FAFBFD", "hover": "#EEF2F8", "select": "#DCE8F7",
    "disabled_text": "#A0A8B4", "disabled_bg": "#F3F4F7", "header": NAVY, "header_hover": NAVY_DARK,
    "table_head": NAVY, "grid": "#E8ECF2", "scroll": "#C3CBD8", "scroll_hover": "#9AA5B4",
    "indicator_border": "#9AA5B4", "progress_bg": "#EEF1F6", "accent": NAVY, "primary_disabled": "#A9C3DE",
    "danger": "#9C0006", "danger_border": "#E8A3AC", "danger_hover": "#FDECEE",
    "kpi_correct": "#006100", "kpi_issue": "#9C0006", "kpi_check": "#9C5700",
}

DARK = {
    "bg": "#15191F", "card": "#1E242C", "border": "#323B47", "text": "#E3E8EF", "muted": "#9AA6B5",
    "input": "#171C22", "input_ro": "#1A2027", "hover": "#2A323D", "select": "#2B4766",
    "disabled_text": "#5E6875", "disabled_bg": "#20262E", "header": "#121A28", "header_hover": "#1C2840",
    "table_head": "#22324D", "grid": "#2A323C", "scroll": "#3C4654", "scroll_hover": "#566272",
    "indicator_border": "#5E6A79", "progress_bg": "#20262E", "accent": "#8DBEF0",
    "primary_disabled": "#2C4560", "danger": "#FF8A95", "danger_border": "#7A3A42", "danger_hover": "#3A2328",
    "kpi_correct": "#6FD08C", "kpi_issue": "#FF8A95", "kpi_check": "#F2C46B",
}

THEMES = {"light": LIGHT, "dark": DARK}

# Result colours (background, text). Kept identical in both themes: they match the Excel sheet,
# and dark text on these soft colours stays readable on a dark window too.
STATUS_COLORS = {
    "Correct": ("#C6EFCE", "#006100"),
    "Issue": ("#FFC7CE", "#9C0006"),
    "Check manually": ("#FFEB9C", "#9C5700"),
    "Not applicable": ("#F2F2F2", "#808080"),
}
SYMBOL = {"Correct": "✔ Match", "Issue": "✘ Differs", "Check manually": "? Not readable", "Not applicable": "— n/a"}

# Backwards-compatible names (light theme values).
BG, CARD, BORDER, TEXT, MUTED = LIGHT["bg"], LIGHT["card"], LIGHT["border"], LIGHT["text"], LIGHT["muted"]


def build_qss(theme: str = "light") -> str:
    c = THEMES.get(theme, LIGHT)
    return f"""
* {{ font-family: "Segoe UI"; font-size: 10pt; color: {c['text']}; }}
QMainWindow, QWidget#root, QDialog, QMessageBox {{ background: {c['bg']}; }}
QFrame#header {{ background: {c['header']}; border: none; }}
QFrame[class="card"] {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel[class="cardTitle"] {{ font-size: 11.5pt; font-weight: 600; color: {c['accent']}; }}
QLabel[class="hint"] {{ color: {c['muted']}; font-size: 9pt; }}
QLabel[class="muted"] {{ color: {c['muted']}; }}
QLabel[class="kpiValue"] {{ font-size: 20pt; font-weight: 600; }}
QLabel#details {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 10px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLineEdit {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 6px 8px;
            min-height: 18px; selection-background-color: {BLUE}; selection-color: white; }}
QLineEdit:read-only {{ background: {c['input_ro']}; }}
QLineEdit:focus {{ border-color: {BLUE}; }}
QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 7px 14px;
              min-height: 18px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled_bg']}; }}
QPushButton#primary {{ background: {BLUE}; color: white; border: none; font-weight: 600; padding: 9px 26px; }}
QPushButton#primary:hover {{ background: #2563A0; }}
QPushButton#primary:disabled {{ background: {c['primary_disabled']}; color: #E6EDF5; }}
QPushButton#danger {{ color: {c['danger']}; border: 1px solid {c['danger_border']}; }}
QPushButton#danger:hover {{ background: {c['danger_hover']}; }}
QToolButton#themeButton {{ background: transparent; border: 1px solid transparent; border-radius: 18px; }}
QToolButton#themeButton:hover {{ background: {c['header_hover']}; border-color: #5C7099; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 5px; background: {c['progress_bg']}; height: 18px;
               text-align: center; }}
QProgressBar::chunk {{ background: {BLUE}; border-radius: 4px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 9px 20px; margin-right: 4px; color: {c['muted']}; font-weight: 600;
               border-bottom: 3px solid transparent; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 3px solid {BLUE}; }}
QTabBar::tab:disabled {{ color: {c['disabled_text']}; }}
QTableWidget {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 6px;
               gridline-color: {c['grid']}; selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['table_head']}; color: white; padding: 6px; border: none; font-weight: 600; }}
QListWidget {{ background: {c['input_ro']}; border: 1px solid {c['border']}; border-radius: 5px; font-family: "Consolas";
              font-size: 9pt; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['indicator_border']}; border-radius: 3px;
                       background: {c['input']}; }}
QCheckBox::indicator:hover {{ border-color: {BLUE}; }}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; image: url("{CHECK_IMG}"); }}
QCheckBox::indicator:disabled {{ background: {c['disabled_bg']}; border-color: {c['border']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scroll']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QComboBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['border']};
                              selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QMenuBar {{ background: {c['card']}; border-bottom: 1px solid {c['border']}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {c['hover']}; }}
QMenu {{ background: {c['card']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 28px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['disabled_text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
QMenu::indicator:checked {{ image: url("{CHECK_IMG}"); background: {BLUE}; border-radius: 7px; }}
QStatusBar {{ background: {c['card']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar QLabel {{ color: {c['muted']}; }}
QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}
"""


QSS = build_qss("light")
