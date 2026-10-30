"""The desktop app's look: the desktop's own colours, and the player's own screenshot as the header.

The AppImage ships its own Qt, which can't load KDE's or GNOME's style plugins, so we read the
desktop's colour scheme ourselves (KDE's kdeglobals, GNOME's dark-style setting) and apply it to
Qt's Fusion style. Nothing is invented: no drawn art, no colours of our own.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

from . import config

# KDE Breeze, used when the desktop doesn't say which colours it uses.
BREEZE = {
    False: {"window": "239,240,241", "text": "35,38,41", "base": "255,255,255", "alternate": "247,247,247",
            "button": "252,252,252", "inactive": "112,125,138", "selection": "61,174,233",
            "selection_text": "255,255,255", "link": "41,128,185", "negative": "218,68,83",
            "positive": "39,174,96", "neutral": "246,116,0"},
    True: {"window": "32,35,38", "text": "252,252,252", "base": "20,22,24", "alternate": "29,31,34",
           "button": "41,44,48", "inactive": "161,169,177", "selection": "61,174,233",
           "selection_text": "252,252,252", "link": "29,153,243", "negative": "218,68,83",
           "positive": "39,174,96", "neutral": "246,116,0"},
}

KDE_KEYS = {
    "window": ("Colors:Window", "BackgroundNormal"),
    "text": ("Colors:Window", "ForegroundNormal"),
    "inactive": ("Colors:Window", "ForegroundInactive"),
    "base": ("Colors:View", "BackgroundNormal"),
    "alternate": ("Colors:View", "BackgroundAlternate"),
    "button": ("Colors:Button", "BackgroundNormal"),
    "selection": ("Colors:Selection", "BackgroundNormal"),
    "selection_text": ("Colors:Selection", "ForegroundNormal"),
    "link": ("Colors:View", "ForegroundLink"),
    "negative": ("Colors:View", "ForegroundNegative"),
    "positive": ("Colors:View", "ForegroundPositive"),
    "neutral": ("Colors:View", "ForegroundNeutral"),
}

colours: dict[str, QColor] = {}


def _rgb(value: str) -> QColor | None:
    try:
        r, g, b = (int(x) for x in value.split(",")[:3])
    except ValueError:
        return None
    return QColor(r, g, b)


def kde_scheme(home: Path | None = None) -> dict[str, str] | None:
    """The colours from KDE's kdeglobals, or None when there is no KDE colour scheme."""
    home = home or Path.home()
    path = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "kdeglobals"
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Colors:Window"):
        return None
    out = {}
    for key, (section, option) in KDE_KEYS.items():
        value = parser.get(section, option, fallback=None)
        if value and _rgb(value) is not None:
            out[key] = value
    return out or None


def prefers_dark() -> bool:
    """GNOME and others: the dark style setting, or a dark GTK theme name."""
    if shutil.which("gsettings"):
        for key in ("color-scheme", "gtk-theme"):
            try:
                value = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key],
                                       capture_output=True, text=True, timeout=2).stdout.lower()
            except (OSError, subprocess.SubprocessError):
                continue
            if "dark" in value:
                return True
    return "dark" in os.environ.get("GTK_THEME", "").lower()


def _brightness(c: QColor) -> float:
    return (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 255000


def scheme() -> dict[str, QColor]:
    kde = kde_scheme()
    dark = (_brightness(_rgb(kde["window"])) < 0.5) if kde and "window" in kde else prefers_dark()
    values = dict(BREEZE[dark], **(kde or {}))
    return {k: _rgb(v) for k, v in values.items()}


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(*(round(x + (y - x) * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def apply(app: QApplication) -> None:
    """Use the desktop's own style when this Qt can load it, else Fusion with the desktop's colours."""
    colours.clear()
    if "breeze" in (k.lower() for k in QStyleFactory.keys()):
        app.setStyle("Breeze")  # running from source on KDE: the real thing
        pal = app.palette()
        c = colours
        c.update(window=pal.window().color(), text=pal.windowText().color(), base=pal.base().color(),
                 selection=pal.highlight().color(), selection_text=pal.highlightedText().color(),
                 link=pal.link().color(), inactive=pal.placeholderText().color())
        for key in ("negative", "positive", "neutral"):
            c[key] = scheme()[key]
        return
    c = scheme()
    colours.update(c)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in (
        (QPalette.Window, "window"), (QPalette.WindowText, "text"), (QPalette.Base, "base"),
        (QPalette.AlternateBase, "alternate"), (QPalette.Text, "text"), (QPalette.Button, "button"),
        (QPalette.ButtonText, "text"), (QPalette.Highlight, "selection"),
        (QPalette.HighlightedText, "selection_text"), (QPalette.ToolTipBase, "button"),
        (QPalette.ToolTipText, "text"), (QPalette.Link, "link"), (QPalette.PlaceholderText, "inactive"),
        (QPalette.BrightText, "selection_text"),
    ):
        pal.setColor(role, c[key])
    off = mix(c["text"], c["window"], 0.55)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, off)
    pal.setColor(QPalette.Disabled, QPalette.Highlight, mix(c["selection"], c["window"], 0.6))
    # Fusion draws frames and lines from these
    dark = _brightness(c["window"]) < 0.5
    pal.setColor(QPalette.Mid, mix(c["window"], c["text"], 0.18 if dark else 0.25))
    pal.setColor(QPalette.Dark, mix(c["window"], QColor(0, 0, 0), 0.35))
    pal.setColor(QPalette.Light, mix(c["button"], QColor(255, 255, 255), 0.12 if dark else 0.6))
    pal.setColor(QPalette.Midlight, mix(c["button"], QColor(255, 255, 255), 0.06 if dark else 0.3))
    pal.setColor(QPalette.Shadow, QColor(0, 0, 0))
    app.setPalette(pal)
    app.setStyleSheet(_checkbox_style(c, pal.color(QPalette.Mid)))


def _checkbox_style(c: dict[str, QColor], border: QColor) -> str:
    """Fusion draws check boxes without a visible edge on dark colour schemes; give them Breeze's."""
    check = QPixmap(32, 32)
    check.fill(Qt.transparent)
    p = QPainter(check)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(c["selection_text"], 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath(QPointF(8, 17))
    path.lineTo(14, 23)
    path.lineTo(25, 10)
    p.drawPath(path)
    p.end()
    out = config.cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    image = out / "check.png"
    check.save(str(image))
    edge, fill, accent = border.name(), c["base"].name(), c["selection"].name()
    return f"""
QCheckBox::indicator, QAbstractItemView::indicator {{
    width: 14px; height: 14px; border: 1px solid {edge}; border-radius: 3px; background: {fill};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{
    background: {accent}; border-color: {accent}; image: url({image});
}}
QCheckBox::indicator:disabled {{ border-color: {mix(border, c["window"], 0.5).name()}; background: {c["window"].name()}; }}
"""


def colour(key: str) -> QColor:
    return colours.get(key) or _rgb(BREEZE[True][key])


# --- the header -----------------------------------------------------------------

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def latest_screenshot(root: Path | None) -> Path | None:
    """The newest picture the player took in game, from a Screenshot(s) folder of the install."""
    if root is None:
        return None
    folders = []
    for parent in (root, root.parent):
        try:
            folders += [p for p in parent.iterdir() if p.is_dir() and p.name.lower().startswith("screenshot")]
        except OSError:
            continue
    best: tuple[float, Path] | None = None
    for folder in folders:
        try:
            for f in folder.iterdir():
                if f.suffix.lower() in IMAGE_SUFFIXES and f.is_file():
                    t = f.stat().st_mtime
                    if best is None or t > best[0]:
                        best = (t, f)
        except OSError:
            continue
    return best[1] if best else None


class Header(QWidget):
    """The top of the window: the player's own latest screenshot when there is one, else nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.picture: QPixmap | None = None

    def set_picture(self, path: Path | None) -> None:
        pix = QPixmap(str(path)) if path else QPixmap()
        self.picture = None if pix.isNull() else pix
        self.update()

    @property
    def on_picture(self) -> bool:
        return self.picture is not None

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt name)
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self.picture is None:
            p.fillRect(self.rect(), self.palette().window())
            p.setPen(self.palette().mid().color())
            p.drawLine(0, h - 1, w, h - 1)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pw, ph = self.picture.width(), self.picture.height()
        scale = max(w / pw, h / ph)
        sw, sh = w / scale, h / scale
        p.drawPixmap(QRectF(0, 0, w, h), self.picture, QRectF((pw - sw) / 2, (ph - sh) * 0.4, sw, sh))
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0, QColor(0, 0, 0, 40))
        shade.setColorAt(1, QColor(0, 0, 0, 170))
        p.fillRect(self.rect(), shade)


def title_font(base: QFont, points: float, bold: bool = True) -> QFont:
    f = QFont(base)
    f.setPointSizeF(points)
    f.setBold(bold)
    return f

