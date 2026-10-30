"""The desktop app's look, taken from ArcheAge's own windows: parchment panels with bronze edges,
dark brown ink, Alegreya type, and the player's own screenshot on the Play tab.

The fonts ship with the app (SIL Open Font License, see fonts/), so it looks the same on every distro.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from . import config

FONTS = Path(__file__).with_name("fonts")
SERIF, SERIF_SC, SANS = "Alegreya", "Alegreya SC", "Alegreya Sans"

FRAME = "#17130f"        # the dark surround, like the game world behind a window
TAB = "#2a231a"
TAB_TEXT = "#bfae8c"
PAPER = "#ece1c7"
PAPER_HI = "#f4ebd6"
PAPER_LO = "#e2d3b2"
FIELD = "#f7efdc"
INK = "#3d2b1d"
HEADING = "#3b2818"
FADED = "#86704f"
BRONZE = "#6d5535"
RULE = "#c7b088"
DOTS = "#cdb893"
GREEN = "#4f7d2c"
RED = "#9a3b25"
AMBER = "#b0761e"
LIGHT = "#e9dec6"         # text on the dark frame

colours = {
    "window": QColor(PAPER), "text": QColor(INK), "inactive": QColor(FADED), "base": QColor(FIELD),
    "selection": QColor("#d9c49a"), "selection_text": QColor(INK), "link": QColor("#6b4a22"),
    "positive": QColor(GREEN), "neutral": QColor(AMBER), "negative": QColor(RED),
}


def colour(key: str) -> QColor:
    return QColor(colours[key])


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(*(round(x + (y - x) * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def load_fonts() -> None:
    for f in sorted(FONTS.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


def font(family: str = SANS, points: float = 11.5, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(family)
    f.setPointSizeF(points)
    f.setWeight(weight)
    return f


def _check_image() -> str:
    """The tick for check boxes, drawn once into the cache folder (stylesheets need a file)."""
    out = config.cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / "tick.png"
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(GREEN), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    tick = QPainterPath(QPointF(8, 17))
    tick.lineTo(14, 23)
    tick.lineTo(25, 9)
    p.drawPath(tick)
    p.end()
    pix.save(str(path))
    return str(path)


def _arrow_image() -> str:
    out = config.cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / "arrow.png"
    pix = QPixmap(24, 24)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(BRONZE), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    arrow = QPainterPath(QPointF(6, 9))
    arrow.lineTo(12, 15)
    arrow.lineTo(18, 9)
    p.drawPath(arrow)
    p.end()
    pix.save(str(path))
    return str(path)


def stylesheet() -> str:
    tick, arrow = _check_image(), _arrow_image()
    return f"""
QWidget {{ font-family: "{SANS}"; font-size: 11.5pt; color: {INK}; }}
QMainWindow, QStatusBar {{ background: {FRAME}; }}
QStatusBar {{ color: #8d7c5e; font-size: 10pt; }}
QStatusBar QLabel {{ color: #8d7c5e; }}
QStatusBar::item {{ border: none; }}
QDialog {{ background: {PAPER}; }}
QToolTip {{ background: {PAPER_HI}; color: {INK}; border: 1px solid {BRONZE}; padding: 5px; }}

QLabel#brand {{ font-family: "{SERIF}"; font-size: 20pt; color: #efe3c6; }}
QLabel#brandSub {{ color: #a3916f; font-size: 10pt; }}
QLabel#h1 {{ font-family: "{SERIF}"; font-weight: bold; font-size: 18pt; color: {HEADING}; }}
QLabel#h2 {{ font-family: "{SERIF}"; font-weight: bold; font-size: 14pt; color: {HEADING}; }}
QLabel#faded, QLabel#caption {{ color: {FADED}; }}
QLabel#caption {{ font-style: italic; font-size: 10pt; }}
QLabel#good {{ color: {GREEN}; }}
QLabel#bad {{ color: {RED}; }}
QFrame#rule {{ background: {RULE}; min-height: 1px; max-height: 1px; border: none; }}
QFrame#dots {{ border: none; border-bottom: 1px dotted {DOTS}; }}
QFrame#plaque {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PAPER_HI}, stop:1 {PAPER_LO}); border: 1px solid {BRONZE}; border-radius: 4px; }}
QFrame#plaque QLabel {{ background: transparent; }}
QFrame#plaque QLabel#h1 {{ font-size: 22pt; }}
QFrame#plaque QLabel#step {{ font-family: "{SERIF}"; font-weight: bold; font-size: 10pt; color: {LIGHT}; background: {BRONZE}; border-radius: 11px; }}

QTabWidget::pane {{
    background: qradialgradient(cx:0.4, cy:0.3, radius:1, fx:0.4, fy:0.3, stop:0 {PAPER_HI}, stop:0.8 {PAPER_LO});
    border: 1px solid {BRONZE}; border-radius: 3px; margin: 0 14px 0 14px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    font-family: "{SERIF}"; font-size: 12.5pt; color: {TAB_TEXT}; background: {TAB};
    border: 1px solid {BRONZE}; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 6px 16px 5px; margin-left: 3px; margin-top: 4px; margin-bottom: -1px;
}}
QTabBar::tab:hover {{ color: #e2d2ae; }}
QTabBar::tab:selected {{ background: {PAPER_HI}; color: #4a3422; font-weight: bold; margin-top: 0; padding-bottom: 7px; }}
QWidget#paper, QScrollArea#paper, QScrollArea#paper > QWidget > QWidget {{ background: transparent; }}

QPushButton {{
    font-family: "{SANS}"; font-weight: 500; color: {HEADING};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6ecd4, stop:1 #dcc597);
    border: 1px solid #876a42; border-radius: 4px; padding: 5px 14px;
}}
QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbf3e0, stop:1 #e5d1a8); }}
QPushButton:pressed {{ background: #d6bf92; }}
QPushButton:disabled {{ color: #a7957a; border-color: #c3ae88; background: #e9dcc0; }}
QPushButton#play {{
    font-family: "{SERIF_SC}"; font-weight: bold; font-size: 18pt; color: #3b2716;
    border-radius: 5px; padding: 8px 20px;
    border-bottom: 3px solid #b3955f;
}}
QPushButton#link {{ background: transparent; border: none; padding: 0; color: #6b4a22; font-weight: 500; }}
QPushButton#link:hover {{ text-decoration: underline; }}

QLineEdit, QComboBox, QPlainTextEdit {{
    background: {FIELD}; border: 1px solid #a88d62; border-radius: 3px; padding: 5px 8px;
    selection-background-color: #d9c49a; selection-color: {INK};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {BRONZE}; }}
QPlainTextEdit {{ font-family: monospace; font-size: 10pt; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 13px; height: 13px; }}
QComboBox QAbstractItemView {{ background: {FIELD}; border: 1px solid {BRONZE}; selection-background-color: #e3d2ad; }}

QCheckBox {{ spacing: 9px; background: transparent; }}
QCheckBox::indicator, QAbstractItemView::indicator {{
    width: 14px; height: 14px; border: 1px solid #8a6d45; border-radius: 2px; background: {FIELD};
}}
QCheckBox::indicator:hover {{ border-color: {HEADING}; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ image: url({tick}); }}
QCheckBox::indicator:disabled {{ border-color: #c3ae88; background: #e9dcc0; }}
QCheckBox:disabled {{ color: #a7957a; }}

QGroupBox {{
    font-family: "{SERIF}"; font-weight: bold; font-size: 14pt; color: {HEADING};
    border: none; border-top: 1px solid {RULE}; margin-top: 18px; padding-top: 12px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0; }}

QTableWidget {{
    background: transparent; border: none; gridline-color: transparent;
    selection-background-color: #e3d2ad; selection-color: {INK};
}}
QTableWidget::item {{ border-bottom: 1px dotted {DOTS}; padding: 0 6px; }}
QTableWidget QPushButton {{ padding: 2px 10px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: transparent; color: {FADED}; border: none; border-bottom: 1px solid {RULE};
    padding: 6px; font-family: "{SERIF}"; font-weight: bold; font-size: 11pt;
}}

QProgressBar {{ background: #2c251c; border: 1px solid #5d4a30; border-radius: 4px; color: {LIGHT};
    text-align: center; font-size: 9.5pt; max-height: 16px; }}
QProgressBar::chunk {{ background: #7d9a4a; border-radius: 3px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c9b48c; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; height: 0; }}
QMessageBox {{ background: {PAPER}; }}
"""


def apply(app: QApplication) -> None:
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, value in (
        (QPalette.Window, PAPER), (QPalette.WindowText, INK), (QPalette.Base, FIELD),
        (QPalette.AlternateBase, "#e6d8b9"), (QPalette.Text, INK), (QPalette.Button, "#e8d8b4"),
        (QPalette.ButtonText, HEADING), (QPalette.Highlight, "#d9c49a"), (QPalette.HighlightedText, INK),
        (QPalette.ToolTipBase, PAPER_HI), (QPalette.ToolTipText, INK), (QPalette.Link, "#6b4a22"),
        (QPalette.PlaceholderText, "#a7957a"),
    ):
        pal.setColor(role, QColor(value))
    app.setPalette(pal)
    app.setFont(font())
    app.setStyleSheet(stylesheet())
