"""The desktop app's look: colours, fonts, the stylesheet and the painted banner art.

Everything is drawn by the app itself, so there are no image files from the game or its publisher.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import config

BG = "#0e1317"
SIDEBAR = "#0a0e12"
SURFACE = "#151c22"
SURFACE_HI = "#1c252d"
BORDER = "#26313a"
TEXT = "#ebe4d3"
MUTED = "#8f9aa3"
GOLD = "#d9a845"
GOLD_HI = "#f2c96a"
GOLD_LO = "#9c6f1f"
OK = "#78c27c"
WARN = "#e0b04a"
BAD = "#e06a55"

SERIFS = ("Cinzel", "Cormorant Garamond", "EB Garamond", "Noto Serif Display", "Noto Serif",
          "Liberation Serif", "DejaVu Serif", "Serif")


def heading_font() -> str:
    """The first decorative serif installed: Noto Serif on most Arch/KDE desktops."""
    have = set(QFontDatabase.families())
    return next((f for f in SERIFS if f in have), "serif")


def _assets() -> dict[str, str]:
    """Small images the stylesheet needs (check mark, combo arrow), drawn into the cache folder."""
    out = config.cache_dir() / "theme"
    out.mkdir(parents=True, exist_ok=True)
    files = {}

    check = QPixmap(32, 32)
    check.fill(Qt.transparent)
    p = QPainter(check)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(BG), 4.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath(QPointF(8, 17))
    path.lineTo(14, 23)
    path.lineTo(25, 10)
    p.drawPath(path)
    p.end()
    files["check"] = str(out / "check.png")
    check.save(files["check"])

    arrow = QPixmap(24, 24)
    arrow.fill(Qt.transparent)
    p = QPainter(arrow)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(GOLD), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath(QPointF(6, 9))
    path.lineTo(12, 15)
    path.lineTo(18, 9)
    p.drawPath(path)
    p.end()
    files["arrow"] = str(out / "arrow.png")
    arrow.save(files["arrow"])
    return files


def stylesheet() -> str:
    a = _assets()
    serif = heading_font()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: 10.5pt; }}
QMainWindow, QDialog, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget#page {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#muted, QLabel#hint {{ color: {MUTED}; }}
QLabel#hint {{ font-size: 9.5pt; }}
QLabel#pageTitle {{ font-family: "{serif}"; font-size: 22pt; font-weight: 600; color: {TEXT}; letter-spacing: 1px; }}
QLabel#cardTitle {{ font-family: "{serif}"; font-size: 13pt; font-weight: 600; color: {GOLD_HI}; letter-spacing: 1px; }}
QLabel#bigNumber {{ font-family: "{serif}"; font-size: 24pt; font-weight: 600; color: {TEXT}; }}
QLabel#heroTitle {{ font-family: "{serif}"; font-size: 34pt; font-weight: 700; color: #fff6e0; letter-spacing: 3px; }}
QLabel#heroSub {{ font-family: "{serif}"; font-size: 13pt; color: {GOLD_HI}; letter-spacing: 6px; }}
QLabel#heroStatus {{ font-size: 11pt; color: #f3ead6; }}
QLabel#brand {{ font-family: "{serif}"; font-size: 12.5pt; font-weight: 700; color: {GOLD_HI}; letter-spacing: 2px; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 9pt; letter-spacing: 3px; }}
QLabel#version {{ color: #5d6872; font-size: 9pt; }}
QLabel a {{ color: {GOLD_HI}; }}

QFrame#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{
    background: {SURFACE_HI}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 16px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {GOLD_LO}; background: #222d36; }}
QPushButton:pressed {{ background: #11171c; }}
QPushButton:disabled {{ color: #5d6872; border-color: #1c242b; background: #141a1f; }}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {GOLD_HI}, stop:1 {GOLD});
    color: #1a1206; border: 1px solid {GOLD_LO}; font-weight: 700;
}}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffe08f, stop:1 {GOLD_HI}); }}
QPushButton#primary:disabled {{ background: #3a3222; color: #7d6d4c; border-color: #3a3222; }}
QPushButton#danger {{ color: #f0a596; }}
QPushButton#danger:hover {{ border-color: {BAD}; }}
QPushButton#play {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffd97a, stop:0.5 {GOLD}, stop:1 #b5821f);
    color: #1d1405; border: 2px solid #ffe7a8; border-radius: 14px;
    font-family: "{serif}"; font-size: 22pt; font-weight: 800; letter-spacing: 6px;
    padding: 12px 40px;
}}
QPushButton#play:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fff0b8, stop:0.5 {GOLD_HI}, stop:1 #c99325); }}
QPushButton#play:pressed {{ background: {GOLD_LO}; }}
QPushButton#play:disabled {{ background: #2f2a1f; color: #8a7a55; border-color: #4a3f28; }}
QPushButton#nav {{
    background: transparent; border: none; border-left: 3px solid transparent; border-radius: 0;
    text-align: left; padding: 11px 22px; color: {MUTED}; font-size: 11pt;
}}
QPushButton#nav:hover {{ color: {TEXT}; background: #10161b; }}
QPushButton#nav:checked {{ color: {GOLD_HI}; border-left: 3px solid {GOLD}; background: #141b21; font-weight: 600; }}
QPushButton#link {{ background: transparent; border: none; color: {GOLD_HI}; padding: 2px 0; text-align: left; }}
QPushButton#link:hover {{ text-decoration: underline; }}

QLineEdit, QComboBox, QPlainTextEdit {{
    background: #0b1014; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {GOLD_LO};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {GOLD_LO}; }}
QPlainTextEdit {{ font-family: monospace; font-size: 9.5pt; color: #d8d0bd; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({a["arrow"]}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: #2a2414;
    selection-color: {GOLD_HI}; padding: 4px;
}}

QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a4752; background: #0b1014; }}
QCheckBox::indicator:hover {{ border-color: {GOLD}; }}
QCheckBox::indicator:checked {{ background: {GOLD}; border-color: {GOLD_HI}; image: url({a["check"]}); }}
QCheckBox::indicator:disabled {{ background: #161c21; border-color: #222a31; }}
QCheckBox:disabled {{ color: #66717b; }}

QTableWidget {{
    background: {SURFACE}; alternate-background-color: #171f26; border: 1px solid {BORDER};
    border-radius: 12px; gridline-color: transparent; selection-background-color: #2a2414;
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 0 10px; border-bottom: 1px solid #1d262d; }}
QTableWidget QPushButton {{ padding: 5px 12px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 10px; font-size: 9pt; font-weight: 600; letter-spacing: 1px; text-transform: uppercase;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #2b3640; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {GOLD_LO}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #2b3640; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QStatusBar {{ background: {SIDEBAR}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {SURFACE_HI}; color: {TEXT}; border: 1px solid {GOLD_LO}; padding: 6px; border-radius: 6px; }}
QMessageBox {{ background: {SURFACE}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: #2a2414; color: {GOLD_HI}; }}
"""


def apply(app: QApplication) -> None:
    """Fusion plus our palette and stylesheet, so the app looks the same under KDE, GNOME or anything else."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, "#0b1014"),
        (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT), (QPalette.Button, SURFACE_HI),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, GOLD_LO), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, SURFACE_HI), (QPalette.ToolTipText, TEXT), (QPalette.Link, GOLD_HI),
        (QPalette.PlaceholderText, "#5d6872"),
    ):
        pal.setColor(role, QColor(colour))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5d6872"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5d6872"))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())


# --- banner art -------------------------------------------------------------------

def _ridge(seed: int, n: int = 160) -> list[float]:
    """A mountain ridge as heights 0..1 across the width, the same every time for a seed."""
    rnd = random.Random(seed)
    waves = [(rnd.uniform(1.5, 3.5) * (k + 1), rnd.uniform(0, math.tau), 0.5 / (k + 1) ** 1.2) for k in range(6)]
    return [sum(a * math.sin(x / n * f * math.tau / 2 + ph) for f, ph, a in waves) for x in range(n + 1)]


RIDGES = [_ridge(s) for s in (3, 11, 27, 42)]
STARS = [(random.Random(i).random(), random.Random(i * 7 + 1).random() ** 1.6, random.Random(i * 13).random())
         for i in range(140)]


class Banner(QWidget):
    """Dusk over layered mountains with a keep on the near ridge, painted to fit any size."""

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt name)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        sky = QLinearGradient(0, 0, 0, h)
        for at, colour in ((0, "#08111f"), (0.35, "#1a2c4a"), (0.62, "#5b4a6a"), (0.8, "#c27a4c"), (1, "#e9aa5e")):
            sky.setColorAt(at, QColor(colour))
        p.fillRect(self.rect(), sky)

        for x, y, b in STARS:
            c = QColor(255, 244, 220, int(60 + 160 * b * (1 - y)))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            r = 0.6 + 1.1 * b
            p.drawEllipse(QPointF(x * w, y * h * 0.55), r, r)

        sun = QPointF(w * 0.7, h * 0.44)
        glow = QRadialGradient(sun, h * 0.75)
        glow.setColorAt(0, QColor(255, 214, 140, 210))
        glow.setColorAt(0.12, QColor(255, 190, 110, 120))
        glow.setColorAt(0.5, QColor(230, 140, 80, 30))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow))
        p.drawRect(self.rect())
        p.setBrush(QColor(255, 236, 190))
        p.drawEllipse(sun, h * 0.06, h * 0.06)

        layers = (
            (0.50, 0.16, "#4b3f61", 255),
            (0.60, 0.15, "#33304a", 255),
            (0.71, 0.13, "#1f2133", 255),
            (0.84, 0.10, "#10141c", 255),
        )
        for i, ((base, amp, colour, alpha), ridge) in enumerate(zip(layers, RIDGES)):
            path = QPainterPath(QPointF(0, h))
            n = len(ridge) - 1
            for k, v in enumerate(ridge):
                path.lineTo(k / n * w, h * (base - amp * v))
            path.lineTo(w, h)
            path.closeSubpath()
            c = QColor(colour)
            c.setAlpha(alpha)
            p.setBrush(c)
            p.drawPath(path)
            if i == 2:
                self._keep(p, w, h, ridge, base, amp, c)
            if i < 3:  # mist between the ranges
                mist = QLinearGradient(0, h * (base - 0.02), 0, h * (base + 0.12))
                mist.setColorAt(0, QColor(240, 180, 140, 0))
                mist.setColorAt(0.5, QColor(240, 180, 140, 28))
                mist.setColorAt(1, QColor(240, 180, 140, 0))
                p.setBrush(QBrush(mist))
                p.drawRect(QRectF(0, h * (base - 0.02), w, h * 0.14))

        # darken the left for the title, and fade the bottom into the page
        left = QLinearGradient(0, 0, w * 0.6, 0)
        left.setColorAt(0, QColor(5, 9, 14, 200))
        left.setColorAt(1, QColor(5, 9, 14, 0))
        p.setBrush(QBrush(left))
        p.drawRect(self.rect())
        bottom = QLinearGradient(0, h * 0.7, 0, h)
        bottom.setColorAt(0, QColor(14, 19, 23, 0))
        bottom.setColorAt(1, QColor(BG))
        p.setBrush(QBrush(bottom))
        p.drawRect(self.rect())
        p.end()

    @staticmethod
    def _keep(p: QPainter, w: int, h: int, ridge: list[float], base: float, amp: float, colour: QColor) -> None:
        """A small castle on the third ridge, with a few lit windows."""
        n = len(ridge) - 1
        k = int(n * 0.33)
        peak = min(range(k - 8, k + 8), key=lambda i: base - amp * ridge[i])
        x, y = peak / n * w, h * (base - amp * ridge[peak]) + 4
        s = h / 320
        p.setBrush(colour)
        towers = ((-30, 18, 12), (-14, 34, 16), (8, 46, 14), (26, 28, 12), (40, 16, 10))
        for dx, th, tw in towers:
            rect = QRectF(x + dx * s, y - th * s, tw * s, th * s + 6 * s)
            p.drawRect(rect)
            for m in range(0, int(tw), 4):  # battlements
                p.drawRect(QRectF(rect.left() + m * s, rect.top() - 3 * s, 2 * s, 3 * s))
        spire = QPainterPath(QPointF(x + 8 * s, y - 46 * s))
        spire.lineTo(x + 15 * s, y - 62 * s)
        spire.lineTo(x + 22 * s, y - 46 * s)
        p.drawPath(spire)
        p.setBrush(QColor(255, 196, 110, 220))
        for wx, wy in ((-10, 24), (12, 36), (16, 22), (30, 18)):
            p.drawRect(QRectF(x + wx * s, y - wy * s, 2.2 * s, 3.2 * s))
