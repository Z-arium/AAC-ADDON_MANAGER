"""Desktop app: `aacam-gui`. A Qt window over the same engine as the command line."""

from __future__ import annotations

import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, crash, github, installer, official, paths, report, syscheck, theme, wine
from .config import Settings
from .core import Manager
from .registry import Addon, Registry

APP_NAME = "ArcheAge Classic Linux Launcher"
APP_REPO = "Z-arium/AAC-ADDON_MANAGER"


def _newer(tag: str, current: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())
    try:
        return parts(tag) > parts(current)
    except ValueError:
        return False


# --- background work ----------------------------------------------------------

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """Run fn on the thread pool, report the result or the error back on the UI thread."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except (github.GitHubError, installer.InstallError, official.LauncherError, wine.WineError, OSError) as e:
            self.signals.failed.emit(str(e))
        except Exception:  # keep the window alive and show what went wrong
            self.signals.failed.emit(traceback.format_exc(limit=3))
        else:
            self.signals.done.emit(result)


# --- small building blocks ------------------------------------------------------

def _item(text: str, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _table(headers: list[str], stretch: int) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(52)
    t.setShowGrid(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    t.setFocusPolicy(Qt.NoFocus)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


def _button(text: str, role: str = "", slot: Callable | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setObjectName(role)
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def _label(text: str = "", role: str = "", wrap: bool = True, rich: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setObjectName(role)
    lab.setWordWrap(wrap)
    if rich:
        lab.setTextFormat(Qt.RichText)
    return lab


class Card(QFrame):
    """A rounded panel with a gold title; add content to .body."""

    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 18, 22, 20)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(_label(title, "cardTitle"))
        if subtitle:
            self.body.addWidget(_label(subtitle, "muted"))


def _page(title: str, subtitle: str, header_right: list[QWidget] | None = None,
          scroll: bool = False) -> tuple[QWidget, QVBoxLayout]:
    """A page with a serif title; returns the page and the layout to fill."""
    page = QWidget()
    page.setObjectName("page")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(36, 30, 36, 24)
    outer.setSpacing(18)
    head = QHBoxLayout()
    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(_label(title, "pageTitle"))
    if subtitle:
        titles.addWidget(_label(subtitle, "muted"))
    head.addLayout(titles, 1)
    for w in header_right or []:
        head.addWidget(w, 0, Qt.AlignBottom)
    outer.addLayout(head)
    if not scroll:
        return page, outer
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 12, 0)
    lay.setSpacing(16)
    area.setWidget(inner)
    outer.addWidget(area, 1)
    return page, lay


ICON = {syscheck.OK: "✔", syscheck.WARN: "⚠", syscheck.FAIL: "✘"}
COLOUR = {syscheck.OK: theme.OK, syscheck.WARN: theme.WARN, syscheck.FAIL: theme.BAD}


def _mark(ok: bool | None, text: str) -> str:
    colour, sign = {True: (theme.OK, "✔"), False: (theme.BAD, "✘"), None: (theme.WARN, "⚠")}[ok]
    return f'<span style="color:{colour}">{sign}</span>&nbsp; {text}'


# --- setup ---------------------------------------------------------------------

class SetupPanel(QWidget):
    """System check, official launcher and addon folder: the Setup page and the first-run dialog."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        box = Card("1 · Your system")
        self.distro_label = _label(role="muted")
        box.body.addWidget(self.distro_label)
        self.checks_label = _label(rich=True)
        self.checks_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.body.addWidget(self.checks_label)
        self.fixes = QPlainTextEdit()
        self.fixes.setReadOnly(True)
        self.fixes.setMaximumHeight(130)
        box.body.addWidget(self.fixes)
        row = QHBoxLayout()
        self.copy_fixes = _button("Copy fix commands", "primary",
                                  lambda: QApplication.clipboard().setText(self.fixes.toPlainText()))
        row.addWidget(self.copy_fixes)
        row.addWidget(_button("Check again", slot=self.refresh))
        row.addStretch()
        box.body.addLayout(row)
        lay.addWidget(box)

        box = Card("2 · Official ArcheAge Classic launcher",
                   "It handles login and patching. This app starts it with the right Wine settings.")
        self.launcher_label = _label(rich=True)
        self.launcher_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.body.addWidget(self.launcher_label)
        row = QHBoxLayout()
        self.download_btn = _button("Download it", "primary", self.download)
        row.addWidget(self.download_btn)
        row.addWidget(_button("Choose the file…", slot=self.choose_launcher))
        row.addStretch()
        box.body.addLayout(row)
        lay.addWidget(box)

        box = Card("3 · Addon folder", "Where the game loads addons from.")
        row = QHBoxLayout()
        self.folder_label = _label(rich=True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.folder_label, 1)
        row.addWidget(_button("Change…", slot=self.choose_folder))
        box.body.addLayout(row)
        lay.addWidget(box)
        self.refresh()

    def refresh(self) -> None:
        d = syscheck.distro()
        self.distro_label.setText(d.name)
        checks = syscheck.run()
        self.checks_label.setText("<br>".join(
            f'<span style="color:{COLOUR[c.status]}">{ICON[c.status]}</span>&nbsp; <b>{c.title}</b>: {c.detail}'
            for c in checks
        ))
        fixes = syscheck.fix_script(checks)
        self.fixes.setPlainText(fixes or "Nothing to fix.")
        self.fixes.setVisible(bool(fixes))
        self.copy_fixes.setVisible(bool(fixes))
        launcher = official.locate(self.win.settings)
        if launcher:
            self.launcher_label.setText(_mark(True, f"Found: {launcher}"))
            self.download_btn.setText("Download again")
            self.download_btn.setObjectName("")
        else:
            self.launcher_label.setText(_mark(
                False, "Not found. Download it here, or choose the AppImage if you already have it."))
            self.download_btn.setText("Download it")
            self.download_btn.setObjectName("primary")
        self.download_btn.style().polish(self.download_btn)
        mgr = self.win.mgr
        self.folder_label.setText(
            _mark(True, str(mgr.install.addon_dir)) if mgr else
            _mark(False, "Not found yet. It appears after the game's first start, or choose it now.")
        )

    def download(self) -> None:
        self.download_btn.setEnabled(False)

        def done(path: Path) -> None:
            self.download_btn.setEnabled(True)
            self.win.settings.set("official_launcher", str(path))
            self.refresh()

        def failed(_message: str) -> None:
            self.download_btn.setEnabled(True)

        self.win.run("Downloading the official launcher…", lambda: official.download(), done, failed)

    def choose_launcher(self) -> None:
        start = str(official.default_dir()) if official.default_dir().is_dir() else str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose the official launcher", start, "AppImage (*.AppImage);;All files (*)")
        if chosen:
            self.win.settings.set("official_launcher", chosen)
            self.refresh()

    def choose_folder(self) -> None:
        self.win.choose_folder()
        self.refresh()


def _scrolled(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    holder = QWidget()
    holder.setObjectName("page")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 12, 0)
    lay.addWidget(widget)
    lay.addStretch()
    area.setWidget(holder)
    return area


class SetupDialog(QDialog):
    """First-run setup; the same panel is the Setup page of the main window."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__(win)
        self.setWindowTitle(f"{APP_NAME} setup")
        self.resize(820, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)
        lay.addWidget(_label("Welcome", "pageTitle"))
        lay.addWidget(_label(
            "Three things get ArcheAge Classic running on Linux: the drivers the game needs, "
            "the official launcher (it handles login and patching), and your addon folder.", "muted"))
        self.panel = SetupPanel(win)
        lay.addWidget(_scrolled(self.panel), 1)
        buttons = QDialogButtonBox()
        copy_report = buttons.addButton("Copy report for help", QDialogButtonBox.ActionRole)
        copy_report.clicked.connect(win.copy_report)
        done = buttons.addButton("Done", QDialogButtonBox.AcceptRole)
        done.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    def refresh(self) -> None:
        self.panel.refresh()


# --- crash fixes ----------------------------------------------------------------

class FixesPanel(QWidget):
    """Crash fixes: switches applied the next time the game starts, and what the last crash showed."""

    def __init__(self, win: MainWindow, findings: list[crash.Finding] | None = None,
                 report_path: Path | None = None) -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        if findings is not None:
            box = Card("What the last crash shows")
            text = _label("<br><br>".join(
                f"<b>{f.title}</b>. {f.detail}" for f in findings
            ) or "No known cause was found. Turn on diagnostics below and play again to record one.", rich=True)
            box.body.addWidget(text)
            if report_path is not None:
                where = _label(f"Everything was saved to {report_path}. Attach it when you ask for help.", "muted")
                where.setTextInteractionFlags(Qt.TextSelectableByMouse)
                box.body.addWidget(where)
            lay.addWidget(box)

        box = Card("Wine build the game runs with")
        self.wine_combo = QComboBox()
        current = win.settings.get("wine")
        self.wow64_opt_in = QCheckBox("Test WoW64 Wine on this PC (not yet OK'd by the ArcheAge Classic team; "
                                      "your own risk)")
        self.wow64_opt_in.setChecked(bool(win.settings.get("wow64_opt_in")))
        self.wow64_opt_in.toggled.connect(self.fill_wine)
        self.fill_wine()
        index = self.wine_combo.findData(current)
        self.wine_combo.setCurrentIndex(max(index, 0))
        box.body.addWidget(self.wine_combo)
        row = QHBoxLayout()
        row.addWidget(self.wow64_opt_in, 1)
        self.wow64_get = _button("Download WoW64 Wine", slot=self.download_wow64)
        self.wow64_get.setVisible(not any(wine.is_wow64(b) for b in wine.builds()))
        row.addWidget(self.wow64_get)
        box.body.addLayout(row)
        chosen = wine.choose(current, opt_in=bool(win.settings.get("wow64_opt_in")))
        box.body.addWidget(_label(
            (f"Now: {chosen.name}. " if chosen else "Now: the official launcher's own choice. ")
            + "Under Proton the game's anti-cheat closes it after server select, so Wine-Staging is the safe "
            "choice. A WoW64 build moves the graphics driver out of the game's 4 GB.", "muted"))
        lay.addWidget(box)

        suggested = {f.fix for f in findings or [] if f.fix}
        on = set(win.settings.get("fixes", []))
        box = Card("Fixes", "Applied the next time the game starts, as settings the official launcher already knows.")
        self.boxes: dict[str, QCheckBox] = {}
        for fix in crash.FIXES:
            title = fix.title + ("  (suggested)" if fix.key in suggested and fix.key not in on else "")
            cb = QCheckBox(title)
            cb.setChecked(fix.cleared and (fix.key in on or fix.key in suggested))
            cb.setToolTip(fix.why)
            if not fix.cleared:
                cb.setEnabled(False)
                cb.setText(f"{fix.title}  (waiting for the ArcheAge Classic team's OK)")
            box.body.addWidget(cb)
            why = _label(fix.why, "hint")
            why.setContentsMargins(30, 0, 0, 8)
            box.body.addWidget(why)
            self.boxes[fix.key] = cb
        self.diagnostics = QCheckBox("Diagnostics: record extra Wine, Proton and DXVK logs (slower, for bug reports)")
        self.diagnostics.setChecked(bool(win.settings.get("diagnostics")) or (findings is not None and not suggested))
        box.body.addWidget(self.diagnostics)
        lay.addWidget(box)

        recent = crash.history(5)
        if recent:
            box = Card("Recent sessions")
            lines = [
                f"{h.get('time', '?')[5:16]}: {h.get('minutes') or '?'} min, "
                + (", ".join(h.get("causes") or []) or ("crashed" if h.get("crashed") else "closed normally"))
                for h in reversed(recent)
            ]
            box.body.addWidget(_label("<br>".join(lines), "muted", rich=True))
            lay.addWidget(box)

    def fill_wine(self) -> None:
        """The Wine builds to choose from; WoW64 ones only for players who opted in to test them."""
        keep = self.wine_combo.currentData()
        self.wine_combo.clear()
        self.wine_combo.addItem("Automatic: Wine-Staging when it is installed", None)
        self.wine_combo.addItem("Let the official launcher choose (may pick Proton)", wine.LAUNCHER)
        opt_in = self.wow64_opt_in.isChecked()
        for build in wine.builds():
            ok = wine.allowed(build, opt_in)
            self.wine_combo.addItem(wine.label(build) + ("" if ok else "  (waiting for the ArcheAge Classic team's OK)"),
                                    str(build))
            if not ok:
                self.wine_combo.model().item(self.wine_combo.count() - 1).setEnabled(False)
        index = self.wine_combo.findData(keep)
        if opt_in and index <= 0:
            index = next((i for i in range(self.wine_combo.count())
                          if (data := self.wine_combo.itemData(i)) and data != wine.LAUNCHER
                          and wine.is_wow64(Path(data))), 0)
        self.wine_combo.setCurrentIndex(max(index, 0))

    def download_wow64(self) -> None:
        self.wow64_get.setEnabled(False)

        def done(_result) -> None:
            self.wow64_get.setVisible(False)
            self.wow64_opt_in.setChecked(True)
            self.fill_wine()

        def work():
            return wine.download_wow64(), wine.prepare_wow64_prefix()

        self.win.run("Downloading WoW64 Wine and copying your Wine prefix…", work, done,
                     lambda _msg: self.wow64_get.setEnabled(True))

    def save(self) -> bool:
        for key, cb in self.boxes.items():
            if cb.isEnabled():
                crash.set_fix(self.win.settings, key, cb.isChecked())
        self.win.settings.set("diagnostics", self.diagnostics.isChecked())
        chosen = self.wine_combo.currentData()
        wow64 = bool(chosen) and chosen != wine.LAUNCHER and wine.is_wow64(Path(chosen))
        if wow64:
            try:
                prefix = wine.prepare_wow64_prefix()  # quick when the copy already exists
            except (wine.WineError, OSError) as e:
                QMessageBox.warning(self, APP_NAME, f"Could not prepare a prefix for WoW64 Wine: {e}")
                return False
        self.win.settings.set("wow64_opt_in", self.wow64_opt_in.isChecked())
        self.win.settings.set("wine", chosen)
        # WoW64 tests run in a copy of the prefix; anything else uses the launcher's own.
        self.win.settings.set("wineprefix", str(prefix) if wow64 else None)
        self.win.statusBar().showMessage("Saved. Fixes apply the next time you press Play.", 6000)
        self.win.refresh_home()
        return True


def open_reports() -> None:
    crash.reports_dir().mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(crash.reports_dir())))


class FixesDialog(QDialog):
    """Crash fixes in a window of their own: opened after a crash with what it showed."""

    def __init__(self, win: MainWindow, findings: list[crash.Finding] | None = None,
                 report_path: Path | None = None) -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"{APP_NAME}: crash fixes")
        self.resize(780, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)
        lay.addWidget(_label("Crash fixes", "pageTitle"))
        self.panel = FixesPanel(win, findings, report_path)
        lay.addWidget(_scrolled(self.panel), 1)
        buttons = QDialogButtonBox()
        save = buttons.addButton("Save a crash report now", QDialogButtonBox.ActionRole)
        save.setToolTip("Collect logs and kernel messages from the last hour into a zip")
        save.clicked.connect(lambda: win.collect_crash(None, None, show=True))
        folder = buttons.addButton("Open reports folder", QDialogButtonBox.ActionRole)
        folder.clicked.connect(open_reports)
        buttons.addButton(QDialogButtonBox.Save).setObjectName("primary")
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def save(self) -> None:
        if self.panel.save():
            self.accept()


# --- home -----------------------------------------------------------------------

class Hero(theme.Banner):
    """The banner on the home page: the game's name, what is happening, and the Play button."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__()
        self.setMinimumHeight(330)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(44, 40, 44, 34)
        lay.setSpacing(4)
        lay.addWidget(_label("ARCHEAGE CLASSIC", "heroTitle", wrap=False))
        lay.addWidget(_label("LINUX LAUNCHER", "heroSub", wrap=False))
        lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(24)
        words = QVBoxLayout()
        words.setSpacing(4)
        self.status = _label("Ready to play.", "heroStatus")
        words.addWidget(self.status)
        self.detail = _label("", "muted")
        words.addWidget(self.detail)
        row.addLayout(words, 1)
        self.play_btn = _button("PLAY", "play", win.play,
                                "Update addons, then start the game through the official launcher")
        self.play_btn.setMinimumSize(QSize(250, 76))
        row.addWidget(self.play_btn, 0, Qt.AlignBottom)
        lay.addLayout(row)


class Stat(Card):
    """A home page tile: title, a big number or word, a line under it, and one action."""

    def __init__(self, title: str, action: QPushButton) -> None:
        super().__init__(title)
        self.value = _label("–", "bigNumber", wrap=False)
        self.body.addWidget(self.value)
        self.line = _label("", "muted")
        self.body.addWidget(self.line)
        self.body.addStretch()
        self.action = action
        row = QHBoxLayout()
        row.addWidget(action)
        row.addStretch()
        self.body.addLayout(row)


# --- main window --------------------------------------------------------------

class MainWindow(QMainWindow):
    PAGES = ("Play", "My Addons", "Get Addons", "Crash fixes", "Setup")

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 780)
        self.setMinimumSize(980, 660)
        self.settings = Settings()
        self.mgr: Manager | None = Manager.from_settings(self.settings)
        self.registry: Registry | None = None
        self.updates: dict[str, github.Release] = {}
        self.checks: list[syscheck.Check] | None = None
        self.pool = QThreadPool.globalInstance()
        self.jobs: set[Job] = set()
        self.busy = 0
        self.watch: official.GameWatch | None = None
        self.memory_warned = False
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(2000)
        self.watch_timer.timeout.connect(self.poll_game)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_home())
        self.stack.addWidget(self._build_installed_tab())
        self.stack.addWidget(self._build_browse_tab())
        self.fixes_page = QWidget()
        self.stack.addWidget(self.fixes_page)
        self.stack.addWidget(self._build_setup_page())
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar())
        row.addWidget(self.stack, 1)
        self.setCentralWidget(shell)
        self.statusBar()

        self.refresh_installed()
        self.refresh_home()
        QTimer.singleShot(0, self.load_registry)
        QTimer.singleShot(0, self.check_app_update)
        QTimer.singleShot(0, self.check_system)
        if not self.settings.get("setup_done"):
            QTimer.singleShot(0, self.show_setup)

    # layout

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 26, 0, 18)
        lay.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 16, 0)
        brand.setSpacing(12)
        icon = Path(__file__).with_name("icon.png")
        if icon.exists():
            logo = QLabel()
            logo.setPixmap(QPixmap(str(icon)).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_label("ARCHEAGE", "brand", wrap=False))
        names.addWidget(_label("CLASSIC · LINUX", "brandSub", wrap=False))
        brand.addLayout(names, 1)
        lay.addLayout(brand)
        lay.addSpacing(26)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for i, name in enumerate(self.PAGES):
            b = _button(name, "nav")
            b.setCheckable(True)
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            lay.addWidget(b)
        self.nav.idClicked.connect(self.go)
        self.nav_buttons[0].setChecked(True)
        lay.addStretch()

        self.app_update = _label("", rich=True)
        self.app_update.setOpenExternalLinks(True)
        self.app_update.setContentsMargins(22, 0, 16, 6)
        self.app_update.hide()
        lay.addWidget(self.app_update)
        lay.addWidget(_label(f"   Version {__version__}", "version"))
        return side

    def _build_home(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.hero = Hero(self)
        self.play_btn = self.hero.play_btn
        lay.addWidget(self.hero, 1)

        tiles = QGridLayout()
        tiles.setContentsMargins(36, 8, 36, 30)
        tiles.setHorizontalSpacing(18)
        self.addons_tile = Stat("Addons", _button("Manage addons", slot=lambda: self.go(1)))
        self.home_update_btn = _button("Update all", "primary", self.update_all)
        self.addons_tile.body.itemAt(self.addons_tile.body.count() - 1).layout().insertWidget(0, self.home_update_btn)
        self.system_tile = Stat("System", _button("Open setup", slot=lambda: self.go(4)))
        self.wine_tile = Stat("Wine & crash fixes", _button("Crash fixes", slot=lambda: self.go(3)))
        for col, tile in enumerate((self.addons_tile, self.system_tile, self.wine_tile)):
            tile.setMaximumHeight(210)
            tiles.addWidget(tile, 0, col)
            tiles.setColumnStretch(col, 1)
        lay.addLayout(tiles)
        return page

    def _build_installed_tab(self) -> QWidget:
        self.check_btn = _button("Check for updates", slot=self.check_updates)
        self.update_all_btn = _button("Update all", "primary", self.update_all)
        w, lay = _page("My Addons", "Everything in your addon folder. Untick an addon to turn it off in game.",
                       [self.check_btn, self.update_all_btn])
        folder = QHBoxLayout()
        self.folder_label = _label(role="muted", wrap=False)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder.addWidget(self.folder_label)
        folder.addWidget(_button("Change folder…", "link", self.choose_folder))
        folder.addStretch()
        lay.addLayout(folder)
        self.installed = _table(["On", "Addon", "Installed", "Latest", ""], stretch=1)
        self.installed.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.installed.setColumnWidth(0, 64)
        self.installed.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.installed.setColumnWidth(4, 230)
        lay.addWidget(self.installed, 1)
        self.empty_label = _label("No addons yet. Find some in Get Addons.", "muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_label)
        return w

    def _build_browse_tab(self) -> QWidget:
        w, lay = _page("Get Addons", "The public addon catalogue. Install pulls in anything an addon needs.")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search addons")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_browse)
        row.addWidget(self.search, 1)
        self.tag_filter = QComboBox()
        self.tag_filter.setMinimumWidth(180)
        self.tag_filter.addItem("All tags")
        self.tag_filter.currentIndexChanged.connect(self.refresh_browse)
        row.addWidget(self.tag_filter)
        lay.addLayout(row)
        self.browse = _table(["Addon", "Author", "Tags", "Description", ""], stretch=3)
        self.browse.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.browse.setColumnWidth(4, 140)
        lay.addWidget(self.browse, 1)
        return w

    def _build_setup_page(self) -> QWidget:
        copy = _button("Copy report for help", slot=self.copy_report,
                       tip="Your system details and the official launcher's log, for anyone helping you")
        w, lay = _page("Setup", "Drivers, the official launcher and your addon folder.", [copy], scroll=True)
        self.setup_panel = SetupPanel(self)
        lay.addWidget(self.setup_panel)
        lay.addStretch()
        return w

    def _build_fixes_page(self) -> QWidget:
        report_btn = _button("Save a crash report now", slot=lambda: self.collect_crash(None, None, show=True),
                             tip="Collect logs and kernel messages from the last hour into a zip")
        folder = _button("Open reports folder", slot=open_reports)
        w, lay = _page("Crash fixes", "Settings passed to the official launcher. No game files are touched.",
                       [report_btn, folder], scroll=True)
        panel = FixesPanel(self)
        lay.addWidget(panel)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(_button("Save", "primary", panel.save))
        lay.addLayout(row)
        lay.addStretch()
        return w

    def go(self, index: int) -> None:
        if index == 3:  # rebuilt each time so it shows the saved settings and the latest sessions
            old = self.fixes_page
            self.fixes_page = self._build_fixes_page()
            self.stack.insertWidget(3, self.fixes_page)
            self.stack.removeWidget(old)
            old.deleteLater()
        elif index == 4:
            self.setup_panel.refresh()
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    # background helpers

    def run(
        self, label: str, fn: Callable[[], Any], done: Callable[[Any], None],
        failed: Callable[[str], None] | None = None,
    ) -> None:
        job = Job(fn)
        self.jobs.add(job)
        self.busy += 1
        self.statusBar().showMessage(label)
        self._set_enabled(False)

        def finish() -> None:
            self.jobs.discard(job)
            self.busy -= 1
            if not self.busy:
                self._set_enabled(True)

        def ok(result: Any) -> None:
            finish()
            done(result)

        def fail(message: str) -> None:
            finish()
            self.statusBar().showMessage("Something went wrong", 5000)
            QMessageBox.warning(self, APP_NAME, message)
            if failed:
                failed(message)

        job.signals.done.connect(ok)
        job.signals.failed.connect(fail)
        self.pool.start(job)

    def _set_enabled(self, on: bool) -> None:
        for widget in (self.check_btn, self.update_all_btn, self.home_update_btn, self.installed, self.browse):
            widget.setEnabled(on)
        if on:
            self.update_all_btn.setEnabled(bool(self.updates))
            self.home_update_btn.setEnabled(bool(self.updates))

    def _guard(self) -> bool:
        """False (after telling the user) if changes are unsafe right now."""
        if self.mgr is None:
            QMessageBox.information(self, APP_NAME, "Choose your ArcheAge Classic addon folder first.")
            self.choose_folder()
            return False
        if paths.game_running():
            QMessageBox.warning(
                self, APP_NAME,
                "ArcheAge is running. Close the game first, it rewrites addons.txt when it exits.",
            )
            return False
        return True

    # data

    def load_registry(self, refresh: bool = False) -> None:
        def loaded(reg: Registry) -> None:
            self.registry = reg
            tags = sorted({t for a in reg.addons.values() for t in a.tags})
            self.tag_filter.blockSignals(True)
            self.tag_filter.clear()
            self.tag_filter.addItems(["All tags", *tags])
            self.tag_filter.blockSignals(False)
            self.refresh_browse()
            self.statusBar().showMessage(f"{len(reg.addons)} addons in the catalogue", 4000)
            self.check_updates()

        self.run("Loading addon catalogue…", lambda: Registry.load(refresh=refresh), loaded)

    def check_app_update(self) -> None:
        """Quietly look for a newer release of this app and show a link if there is one."""
        def fetch() -> dict | None:
            try:
                data = github.get_json(f"{github.API}/repos/{APP_REPO}/releases/latest")
            except github.GitHubError:
                return None
            return data if isinstance(data, dict) else None

        def show(data: dict | None) -> None:
            if not data or not _newer(data.get("tag_name", ""), __version__):
                return
            text = f'<a href="{data["html_url"]}">Version {data["tag_name"]} is available</a>'
            self.app_update.setText(text)
            self.app_update.show()
            link = QLabel(text)
            link.setOpenExternalLinks(True)
            self.statusBar().addPermanentWidget(link)

        job = Job(fetch)
        self.jobs.add(job)
        job.signals.done.connect(show)
        job.signals.done.connect(lambda _: self.jobs.discard(job))
        self.pool.start(job)

    def check_system(self) -> None:
        """Run the driver checks off the UI thread for the System tile."""
        def show(checks: list[syscheck.Check]) -> None:
            self.checks = checks
            self.refresh_home()

        job = Job(syscheck.run)
        self.jobs.add(job)
        job.signals.done.connect(show)
        job.signals.done.connect(lambda _: self.jobs.discard(job))
        self.pool.start(job)

    def refresh_home(self) -> None:
        local = self.mgr.local() if self.mgr else []
        n = len(self.updates)
        if self.mgr is None:
            self.addons_tile.value.setText("No folder")
            self.addons_tile.line.setText("Choose your addon folder in Setup.")
        else:
            on = sum(1 for a in local if a.enabled)
            self.addons_tile.value.setText(str(len(local)))
            self.addons_tile.line.setText(
                f"installed, {on} turned on. " + (f"{n} update{'s' if n != 1 else ''} ready." if n else "All up to date."))
        self.home_update_btn.setVisible(bool(n))
        self.home_update_btn.setText(f"Update all ({n})")
        self.nav_buttons[1].setText(f"My Addons   ({n})" if n else "My Addons")

        if self.checks is None:
            self.system_tile.value.setText("Checking…")
            self.system_tile.line.setText("")
        else:
            bad = [c for c in self.checks if c.status == syscheck.FAIL]
            warn = [c for c in self.checks if c.status == syscheck.WARN]
            launcher = official.locate(self.settings) or self.settings.get("launch")
            self.system_tile.value.setText("Ready" if not bad and launcher else "Needs a fix")
            parts = [f"{len(bad)} missing" if bad else "Drivers found", f"{len(warn)} warning{'s' if len(warn) != 1 else ''}" if warn else ""]
            parts.append("official launcher found" if launcher else "official launcher not set up")
            self.system_tile.line.setText(", ".join(p for p in parts if p) + ".")

        chosen = wine.choose(self.settings.get("wine"), opt_in=bool(self.settings.get("wow64_opt_in")))
        self.wine_tile.value.setText(chosen.name if chosen else "Launcher's choice")
        fixes = len(self.settings.get("fixes", []))
        last = crash.history(1)
        line = f"{fixes} fix{'es' if fixes != 1 else ''} on."
        if last:
            h = last[-1]
            line += " Last session: " + ("crashed" if h.get("crashed") else "closed normally") + \
                (f" after {h['minutes']} min." if h.get("minutes") else ".")
        self.wine_tile.line.setText(line)

        if self.watch is None:
            self.hero.status.setText("Ready to play." if self.mgr else "Ready to play. Addons need a folder first.")
            self.hero.detail.setText(
                "Play updates your addons, then opens the official launcher. Log in and press Play there.")

    def refresh_installed(self) -> None:
        if self.mgr is None:
            self.folder_label.setText("No addon folder found")
            self.installed.setRowCount(0)
            self.empty_label.setText("Couldn't find ArcheAge Classic. Use Change folder… to pick ~/Documents/AAClassic.")
            self.empty_label.show()
            self.refresh_home()
            return
        self.folder_label.setText(str(self.mgr.install.addon_dir))
        local = self.mgr.local()
        self.empty_label.setVisible(not local)
        self.installed.setRowCount(len(local))
        for row, a in enumerate(local):
            box = QCheckBox()
            box.setChecked(a.enabled)
            box.setToolTip("Load this addon in game")
            box.toggled.connect(lambda on, n=a.name: self.toggle(n, on))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addWidget(box, 0, Qt.AlignCenter)
            self.installed.setCellWidget(row, 0, cell)
            meta = self.registry.get(a.name) if self.registry else None
            title = meta.alias if meta else a.name
            self.installed.setItem(row, 1, _item(title, meta.description if meta else ""))
            self.installed.setItem(row, 2, _item(a.version))
            latest = self.updates.get(a.name)
            status = _item(latest.version if latest else ("" if not a.managed else "up to date"))
            status.setForeground(QColor(theme.GOLD_HI if latest else theme.MUTED))
            self.installed.setItem(row, 3, status)
            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(8, 8, 14, 8)
            al.setSpacing(8)
            al.addStretch()
            if latest:
                al.addWidget(_button("Update", "primary", lambda _=False, n=a.name: self.update([n])))
            al.addWidget(_button("Remove", "danger", lambda _=False, n=a.name: self.remove(n)))
            self.installed.setCellWidget(row, 4, actions)
        self.update_all_btn.setText(f"Update all ({len(self.updates)})" if self.updates else "Update all")
        self.update_all_btn.setEnabled(bool(self.updates) and not self.busy)
        self.refresh_home()
        self.refresh_browse()

    def refresh_browse(self) -> None:
        if self.registry is None:
            return
        text = self.search.text().strip()
        tag = self.tag_filter.currentText()
        hits = self.registry.search(text) if text else sorted(self.registry.addons.values(), key=lambda a: a.name.lower())
        if tag and tag != "All tags":
            hits = [a for a in hits if tag in a.tags]
        installed = {a.name for a in self.mgr.local()} if self.mgr else set()
        self.browse.setRowCount(len(hits))
        for row, a in enumerate(hits):
            name = _item(a.alias, f"{a.name}\nhttps://github.com/{a.repo}")
            font = name.font()
            font.setBold(True)
            name.setFont(font)
            self.browse.setItem(row, 0, name)
            self.browse.setItem(row, 1, _item(a.author))
            tags = _item(", ".join(a.tags))
            tags.setForeground(QColor(theme.MUTED))
            self.browse.setItem(row, 2, tags)
            needs = f"\nNeeds: {', '.join(a.dependencies)}" if a.dependencies else ""
            self.browse.setItem(row, 3, _item(a.description, a.description + needs))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(8, 8, 14, 8)
            btn = _button("Installed" if a.name in installed else "Install",
                          "" if a.name in installed else "primary", lambda _=False, addon=a: self.add(addon))
            btn.setEnabled(a.name not in installed)
            cl.addWidget(btn)
            self.browse.setCellWidget(row, 4, cell)

    # actions

    def choose_folder(self) -> None:
        start = str(Path.home() / "Documents/AAClassic")
        chosen = QFileDialog.getExistingDirectory(self, "Choose the AAClassic folder or its Addon folder", start)
        if not chosen:
            return
        inst = paths.resolve_user_path(Path(chosen))
        self.settings.set("addon_dir", str(inst.addon_dir))
        self.mgr = Manager(inst)
        self.updates = {}
        self.refresh_installed()
        self.check_updates()

    def check_updates(self) -> None:
        if self.mgr is None or self.registry is None or not self.mgr.db.addons:
            return
        mgr, reg = self.mgr, self.registry

        def found(updates: dict[str, github.Release]) -> None:
            self.updates = updates
            self.refresh_installed()
            n = len(updates)
            self.statusBar().showMessage(f"{n} update{'s' if n != 1 else ''} available" if n else "Everything is up to date", 6000)

        self.run("Checking for updates…", lambda: mgr.check_updates(reg), found)

    def add(self, addon: Addon) -> None:
        if not self._guard() or self.registry is None:
            return
        mgr, reg = self.mgr, self.registry

        def done(result) -> None:
            names = ", ".join(f"{a.alias} {r.version}" for a, r in result)
            self.statusBar().showMessage(f"Installed {names}", 6000)
            self.refresh_installed()

        self.run(f"Installing {addon.alias}…", lambda: mgr.add(reg, addon.name), done)

    def update(self, names: list[str], then: Callable[[], None] | None = None) -> None:
        """Update addons; `then` runs afterwards whether or not the update worked."""
        if self.registry is None or not self._guard():
            if then:
                then()
            return
        mgr, reg, pending = self.mgr, self.registry, dict(self.updates)

        def work() -> list[str]:
            done = []
            for name in names:
                addon = mgr.addon_for(reg, name)
                if addon and name in pending:
                    mgr.install_one(addon, pending[name])
                    done.append(name)
            return done

        def finished(done: list[str]) -> None:
            for name in done:
                self.updates.pop(name, None)
            self.statusBar().showMessage(f"Updated {len(done)} addon{'s' if len(done) != 1 else ''}", 6000)
            self.refresh_installed()
            if then:
                then()

        self.run("Updating…", work, finished, (lambda _msg: then()) if then else None)

    def update_all(self) -> None:
        self.update(sorted(self.updates))

    def toggle(self, name: str, on: bool) -> None:
        if not self._guard():
            self.refresh_installed()
            return
        (installer.enable if on else installer.disable)(self.mgr.install, name)
        self.statusBar().showMessage(f"{'Enabled' if on else 'Disabled'} {name}", 3000)
        self.refresh_home()

    def remove(self, name: str) -> None:
        if not self._guard():
            return
        answer = QMessageBox.question(
            self, APP_NAME, f"Remove {name}?\n\nIts saved settings are kept in case you reinstall it.",
        )
        if answer != QMessageBox.Yes:
            return
        self.mgr.remove(name)
        self.updates.pop(name, None)
        self.refresh_installed()
        self.statusBar().showMessage(f"Removed {name}", 4000)

    def show_setup(self) -> None:
        SetupDialog(self).exec()
        self.settings.set("setup_done", True)
        self.mgr = Manager.from_settings(self.settings)
        self.refresh_installed()
        self.setup_panel.refresh()

    def copy_report(self) -> None:
        QApplication.clipboard().setText(report.build(self.settings))
        self.statusBar().showMessage("Report copied. Paste it where you ask for help.", 6000)

    def _hero(self, status: str, detail: str = "", button: str | None = None) -> None:
        self.hero.status.setText(status)
        self.hero.detail.setText(detail)
        if button is None:
            self.play_btn.setText("PLAY")
            self.play_btn.setEnabled(True)
        else:
            self.play_btn.setText(button)
            self.play_btn.setEnabled(False)

    def play(self) -> None:
        if self.watch is not None:
            self.statusBar().showMessage("The game is already starting or running.", 4000)
            return
        command = self.settings.get("launch")
        appimage = None if command else official.locate(self.settings)
        if not command and appimage is None:
            QMessageBox.information(
                self, APP_NAME, "The official ArcheAge Classic launcher isn't set up yet. Let's do that first.",
            )
            self.show_setup()
            return

        def start() -> None:
            extra = crash.launch_env(self.settings)
            try:
                if command:
                    proc = subprocess.Popen(["sh", "-c", command], start_new_session=True,
                                            env=official.environment(True, extra=extra))
                else:
                    proc = official.start(appimage, syscheck.has_lib("libfuse.so.2", 64), extra)
            except (official.LauncherError, OSError) as e:
                self._hero("Could not start the game.", str(e))
                QMessageBox.warning(self, APP_NAME, f"Could not start the game: {e}")
                return
            self.watch = official.GameWatch(proc, sampler=crash.Sampler())
            self.memory_warned = False
            self.watch_timer.start()
            self.statusBar().showMessage("Official launcher started. Log in and press Play there.")
            self._hero("The official launcher is open.", "Log in and press Play there.", "STARTING…")

        if self.updates and self.mgr is not None and not paths.game_running():
            self._hero("Updating your addons…", "", "UPDATING…")
            self.update(list(self.updates), then=start)
        else:
            start()

    def poll_game(self) -> None:
        if self.watch is None:
            return
        state = self.watch.poll()
        if state is None:
            self._check_memory(self.watch)
            return
        if state == "game":
            self.statusBar().showMessage("ArcheAge Classic is running.")
            self._hero("ArcheAge Classic is running.",
                       "This window keeps an eye on the game's memory and checks for a crash when it closes.",
                       "IN GAME")
            return
        watch = self.watch
        self.watch_timer.stop()
        self.watch = None
        self._hero("Ready to play.")
        if state in ("closed", "crashed"):
            # A crash later in a session looks like a normal close; the logs tell them apart.
            self.statusBar().showMessage("ArcheAge Classic closed. Checking for signs of a crash…")
            self.collect_crash(watch.sampler, watch.played, show=False)
        elif state == "launcher_closed":
            self.statusBar().showMessage("The official launcher closed.", 6000)
        else:
            self.statusBar().clearMessage()
            box = QMessageBox(QMessageBox.Warning, APP_NAME,
                              "The official launcher stopped with an error.\n\n"
                              "Setup shows whether a driver is missing. A report with your system "
                              "details and the launcher's log helps anyone who helps you.", parent=self)
            setup = box.addButton("Open setup", QMessageBox.ActionRole)
            copy = box.addButton("Copy report", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is setup:
                self.go(4)
            elif box.clickedButton() is copy:
                self.copy_report()
        self.refresh_home()

    def _check_memory(self, watch: official.GameWatch) -> None:
        """Warn once per session when the 32-bit game is about to run out of its 4 GB."""
        sampler = watch.sampler
        if sampler is None or self.memory_warned or not sampler.near_limit():
            return
        self.memory_warned = True
        used = sampler.last.get("VmSize", 0) / 1e6
        text = (f"ArcheAge Classic is using {used:.2f} of its 4 GB and may crash at the next zone load. "
                "Lower texture quality in the game's options, or log out somewhere safe.")
        self.statusBar().showMessage(text)
        self.hero.detail.setText(text)
        notify(APP_NAME, text)
        QApplication.alert(self, 0)

    def collect_crash(self, sampler: crash.Sampler | None, played: float | None, show: bool) -> None:
        """Gather crash evidence in the background; show it if it points to a crash or if asked."""

        def done(result: tuple[Path, list[crash.Finding]]) -> None:
            path, findings = result
            self.refresh_home()
            if not show and not crash.crashed(findings):
                path.unlink(missing_ok=True)
                self.statusBar().showMessage("ArcheAge Classic closed.", 6000)
                return
            self.statusBar().clearMessage()
            FixesDialog(self, findings, path).exec()

        self.run("Collecting crash evidence…",
                 lambda: crash.collect(self.settings, sampler, played=played), done)


def notify(title: str, text: str) -> None:
    """A desktop notification, which shows over the game's window."""
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", APP_NAME, "-u", "critical", title, text],
                             env=official.environment(True), start_new_session=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("aac-linux-launcher")
    icon = Path(__file__).with_name("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply(app)
    win = MainWindow()
    if "--selftest" in app.arguments():
        # Used by CI to prove the AppImage starts on each distro: Qt, its plugins and our code load.
        SetupDialog(win).refresh()
        print(f"{APP_NAME} {__version__}: window and setup created on {app.platformName()}")
        return 0
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
