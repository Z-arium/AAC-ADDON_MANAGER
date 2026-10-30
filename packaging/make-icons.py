"""Draw the app icon at every size in src/aacam/icons/: a parchment plaque in a bronze frame with
"AA" in Alegreya SC, matching the launcher's window. Run with PySide6 installed:

    QT_QPA_PLATFORM=offscreen python packaging/make-icons.py
"""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

ROOT = Path(__file__).resolve().parent.parent / "src/aacam"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    outer = QRectF(8 * s, 8 * s, 240 * s, 240 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#17130f"))  # the dark frame
    p.drawRoundedRect(outer, 30 * s, 30 * s)
    inner = outer.adjusted(16 * s, 16 * s, -16 * s, -16 * s)
    grad = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    grad.setColorAt(0, QColor("#f4ebd6"))
    grad.setColorAt(1, QColor("#e2d3b2"))
    p.setBrush(grad)
    p.setPen(QPen(QColor("#6d5535"), max(1.0, 5 * s)))  # bronze edge
    p.drawRoundedRect(inner, 14 * s, 14 * s)
    font = QFont("Alegreya SC")
    font.setBold(True)
    font.setPixelSize(round(118 * s))
    p.setFont(font)
    p.setPen(QColor("#3b2818"))
    p.drawText(inner.adjusted(0, -6 * s, 0, 0), Qt.AlignCenter, "AA")
    if size >= 48:
        p.setPen(QPen(QColor("#6d5535"), max(1.0, 3 * s)))
        y = inner.bottom() - 34 * s
        p.drawLine(int(inner.left() + 44 * s), int(y), int(inner.right() - 44 * s), int(y))
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv)
    QFontDatabase.addApplicationFont(str(ROOT / "fonts/AlegreyaSC-Bold.ttf"))
    out = ROOT / "icons"
    out.mkdir(exist_ok=True)
    for size in SIZES:
        draw(size).save(str(out / f"{size}.png"))
    draw(256).save(str(ROOT / "icon.png"))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
