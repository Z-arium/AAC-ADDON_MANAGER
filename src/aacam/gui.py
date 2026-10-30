"""Desktop app: `aacam-gui`. A Qt window over the same engine as the command line."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, crash, github, installer, logs, official, paths, report, syscheck, theme, wine
from .config import Settings
from .core import Manager
from .registry import Addon, Registry

APP_NAME = "ArcheAge Classic Linux Launcher"
APP_REPO = "Z-arium/AAC-ADDON_MANAGER"

log = logging.getLogger("aacam.gui")


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
            log.error("%s", e)
            self.signals.failed.emit(str(e))
        except Exception:  # keep the window alive and show what went wrong
            log.exception("unexpected error")
            self.signals.failed.emit(
                f"Something unexpected went wrong. The details are in {logs.path()}.\n\n"
                "Setup > Copy diagnostics copies them for a bug report.\n\n" + traceback.format_exc(limit=1))
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
    t.verticalHeader().setDefaultSectionSize(38)
    t.setShowGrid(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


def _button(text: str, slot: Callable | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def _muted(label: QLabel) -> QLabel:
    pal = label.palette()
    pal.setColor(QPalette.WindowText, theme.colour("inactive"))
    label.setPalette(pal)
    return label


def _label(text: str = "", muted: bool = False, rich: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    if rich:
        lab.setTextFormat(Qt.RichText)
    return _muted(lab) if muted else lab


def _word(key: str, text: str) -> str:
    """A status word in the desktop's colour for good, warning or bad."""
    return f'<span style="color:{theme.colour(key).name()}">{text}</span>'


STATUS = {syscheck.OK: ("positive", "Found"), syscheck.WARN: ("neutral", "Warning"),
          syscheck.FAIL: ("negative", "Missing")}


def _scrolled(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setObjectName("paper")
    holder = QWidget()
    holder.setObjectName("paper")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 8, 0)
    lay.addWidget(widget)
    lay.addStretch()
    area.setWidget(holder)
    return area


# Short words for crash causes in the Play tab's session list.
CAUSES = {
    "oom_killed": "out of memory", "address_space": "out of memory", "address_space_close": "near 4 GB",
    "device_lost": "GPU lost", "gpu_fault": "GPU error", "no_vulkan": "no Vulkan",
    "too_many_files": "too many files", "unhandled": "crashed", "quick_exit": "closed early",
}


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    return line


def _cell(text: str, role: str = "") -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setObjectName(role)
    return lab


def _clear(grid: QGridLayout) -> None:
    while grid.count():
        item = grid.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _fill_grid(grid: QGridLayout, rows: list[list[QWidget]]) -> None:
    _clear(grid)
    for r, cells in enumerate(rows):
        for c, cell in enumerate(cells):
            grid.addWidget(cell, r, c)


def _fill_columns(grid: QGridLayout, pairs: list[tuple[QWidget, QWidget]], columns: int) -> None:
    """Name/value pairs laid out down `columns` columns, each pair on a dotted line."""
    _clear(grid)
    per = max(1, -(-len(pairs) // columns))
    for i, (left, right) in enumerate(pairs):
        line = QFrame()
        line.setObjectName("dots")
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 6, 0, 6)
        row.addWidget(left)
        row.addStretch()
        row.addWidget(right)
        grid.addWidget(line, i % per, i // per)
    for c in range(columns):
        grid.setColumnStretch(c, 1)


def _tab(margins: int = 22) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("paper")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(10)
    return w, lay


# --- setup ---------------------------------------------------------------------

class SetupPanel(QWidget):
    """System check, official launcher and addon folder: the Setup tab and the first-run dialog."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        box = QGroupBox("1. Your system")
        form = QVBoxLayout(box)
        self.distro_label = _label(muted=True)
        form.addWidget(self.distro_label)
        self.checks = QGridLayout()
        self.checks.setHorizontalSpacing(16)
        self.checks.setVerticalSpacing(6)
        self.checks.setColumnStretch(2, 1)
        form.addLayout(self.checks)
        self.fixes = QPlainTextEdit()
        self.fixes.setReadOnly(True)
        self.fixes.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.fixes.setMaximumHeight(110)
        form.addWidget(self.fixes)
        row = QHBoxLayout()
        self.copy_fixes = _button("Copy fix commands",
                                  lambda: QApplication.clipboard().setText(self.fixes.toPlainText()))
        row.addWidget(self.copy_fixes)
        row.addWidget(_button("Check again", self.refresh))
        row.addStretch()
        form.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("2. Official ArcheAge Classic launcher")
        form = QVBoxLayout(box)
        self.launcher_label = _label(rich=True)
        self.launcher_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.launcher_label)
        row = QHBoxLayout()
        self.download_btn = _button("Download it", self.download)
        row.addWidget(self.download_btn)
        row.addWidget(_button("Choose the file…", self.choose_launcher))
        row.addStretch()
        form.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("3. Addon folder")
        form = QHBoxLayout(box)
        self.folder_label = _label(rich=True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.folder_label, 1)
        form.addWidget(_button("Change…", self.choose_folder))
        lay.addWidget(box)

        box = QGroupBox("4. GitHub token (optional)")
        form = QVBoxLayout(box)
        form.addWidget(_label(
            "Addon updates come from GitHub, which allows 60 lookups an hour without a token. With many "
            "addons that runs out. A token raises it to 5,000: make one at github.com/settings/tokens "
            "(a fine-grained token with no permissions is enough) and paste it here. It stays on this PC.",
            muted=True))
        row = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("github_pat_… or ghp_…")
        row.addWidget(self.token_edit, 1)
        row.addWidget(_button("Save", self.save_token))
        self.clear_token_btn = _button("Clear", self.clear_token)
        row.addWidget(self.clear_token_btn)
        row.addWidget(_button("Check limit", self.check_limit, "Ask GitHub how many lookups are left this hour"))
        form.addLayout(row)
        self.token_label = _label(muted=True)
        form.addWidget(self.token_label)
        lay.addWidget(box)
        self.refresh()

    def refresh(self) -> None:
        d = syscheck.distro()
        self.distro_label.setText(d.name)
        checks = syscheck.run()
        while self.checks.count():
            item = self.checks.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row, c in enumerate(checks):
            key, word = STATUS[c.status]
            title = QLabel(f"<b>{c.title}</b>")
            title.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.checks.addWidget(title, row, 0)
            status = QLabel(_word(key, word))
            status.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.checks.addWidget(status, row, 1)
            detail = _label("" if c.status == syscheck.OK else c.detail, muted=True)
            self.checks.addWidget(detail, row, 2)
        fixes = syscheck.fix_script(checks)
        self.fixes.setPlainText(fixes or "Nothing to fix.")
        self.fixes.setVisible(bool(fixes))
        self.copy_fixes.setVisible(bool(fixes))
        launcher = official.locate(self.win.settings)
        if launcher:
            self.launcher_label.setText(f"{_word('positive', 'Found')}: {launcher}")
            self.download_btn.setText("Download again")
        else:
            self.launcher_label.setText(
                f"{_word('negative', 'Not found')}. Download it here, or choose the AppImage if you already have it."
            )
            self.download_btn.setText("Download it")
        saved = bool(self.win.settings.get("github_token"))
        self.token_label.setText(
            "A token from the GITHUB_TOKEN variable is in use." if github.token() and not saved else
            "A token is saved." if saved else "No token: 60 lookups an hour.")
        self.clear_token_btn.setEnabled(saved)
        mgr = self.win.mgr
        self.folder_label.setText(
            str(mgr.install.addon_dir) if mgr else
            f"{_word('negative', 'Not found yet')}. It appears after the game's first start, or choose it now."
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

    def save_token(self) -> None:
        value = self.token_edit.text().strip()
        if not value:
            return
        self.win.settings.set("github_token", value)
        github.clear_cache()
        self.token_edit.clear()
        self.refresh()
        self.check_limit()

    def clear_token(self) -> None:
        self.win.settings.set("github_token", None)
        self.refresh()

    def check_limit(self) -> None:
        def show(rate: dict) -> None:
            reset = time.strftime("%H:%M", time.localtime(rate.get("reset", 0)))
            self.token_label.setText(f"{self.token_label.text().split(' GitHub')[0]} GitHub: {rate.get('remaining')} "
                                     f"of {rate.get('limit')} lookups left this hour, resets at {reset}.")

        self.win.run("Asking GitHub…", github.rate_limit, show)

    def choose_launcher(self) -> None:
        start = str(official.default_dir()) if official.default_dir().is_dir() else str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose the official launcher", start, "AppImage (*.AppImage);;All files (*)")
        if chosen:
            self.win.settings.set("official_launcher", chosen)
            self.refresh()

    def choose_folder(self) -> None:
        self.win.choose_folder()
        self.refresh()


class SetupDialog(QDialog):
    """First-run setup; the same panel is the Setup tab of the main window."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__(win)
        self.setWindowTitle(f"{APP_NAME} setup")
        self.resize(780, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(_label(
            "Three things get ArcheAge Classic running on Linux: the drivers the game needs, "
            "the official launcher (it handles login and patching), and your addon folder."))
        self.panel = SetupPanel(win)
        lay.addWidget(_scrolled(self.panel), 1)
        buttons = QDialogButtonBox()
        copy_report = buttons.addButton("Copy diagnostics", QDialogButtonBox.ActionRole)
        copy_report.clicked.connect(win.copy_report)
        buttons.addButton("Done", QDialogButtonBox.AcceptRole)
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
        lay.setSpacing(12)
        if findings is not None:
            box = QGroupBox("What the last crash shows")
            form = QVBoxLayout(box)
            form.addWidget(_label("<br><br>".join(
                f"<b>{f.title}</b>. {f.detail}" for f in findings
            ) or "No known cause was found. Turn on diagnostics below and play again to record one.", rich=True))
            if report_path is not None:
                where = _label(f"Everything was saved to {report_path}. Attach it when you ask for help.", muted=True)
                where.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addWidget(where)
            lay.addWidget(box)

        box = QGroupBox("Wine build the game runs with")
        form = QVBoxLayout(box)
        self.wine_combo = QComboBox()
        current = win.settings.get("wine")
        self.wow64_opt_in = QCheckBox("Test WoW64 Wine on this PC (not yet OK'd by the ArcheAge Classic team; "
                                      "your own risk)")
        self.wow64_opt_in.setChecked(bool(win.settings.get("wow64_opt_in")))
        self.wow64_opt_in.toggled.connect(self.fill_wine)
        self.fill_wine()
        index = self.wine_combo.findData(current)
        self.wine_combo.setCurrentIndex(max(index, 0))
        form.addWidget(self.wine_combo)
        row = QHBoxLayout()
        row.addWidget(self.wow64_opt_in, 1)
        self.wow64_get = _button("Download WoW64 Wine", self.download_wow64)
        self.wow64_get.setVisible(not any(wine.is_wow64(b) for b in wine.builds()))
        row.addWidget(self.wow64_get)
        form.addLayout(row)
        chosen = wine.choose(current, opt_in=bool(win.settings.get("wow64_opt_in")))
        form.addWidget(_label(
            (f"Now: {chosen.name}. " if chosen else "Now: the official launcher's own choice. ")
            + "Under Proton the game's anti-cheat closes it after server select, so Wine-Staging is the safe "
            "choice. A WoW64 build moves the graphics driver out of the game's 4 GB.", muted=True))
        lay.addWidget(box)

        suggested = {f.fix for f in findings or [] if f.fix}
        on = set(win.settings.get("fixes", []))
        box = QGroupBox("Fixes, applied the next time the game starts")
        form = QVBoxLayout(box)
        self.boxes: dict[str, QCheckBox] = {}
        for fix in crash.FIXES:
            title = fix.title + ("  (suggested)" if fix.key in suggested and fix.key not in on else "")
            cb = QCheckBox(title)
            cb.setChecked(fix.cleared and (fix.key in on or fix.key in suggested))
            cb.setToolTip(fix.why)
            if not fix.cleared:
                cb.setEnabled(False)
                cb.setText(f"{fix.title}  (waiting for the ArcheAge Classic team's OK)")
            form.addWidget(cb)
            why = _label(fix.why, muted=True)
            why.setContentsMargins(24, 0, 0, 6)
            form.addWidget(why)
            self.boxes[fix.key] = cb
        self.diagnostics = QCheckBox("Diagnostics: record extra Wine, Proton and DXVK logs (slower, for bug reports)")
        self.diagnostics.setChecked(bool(win.settings.get("diagnostics")) or (findings is not None and not suggested))
        form.addWidget(self.diagnostics)
        lay.addWidget(box)

        recent = crash.history(5)
        if recent:
            box = QGroupBox("Recent sessions")
            form = QVBoxLayout(box)
            lines = [
                f"{h.get('time', '?')[5:16]}: {h.get('minutes') or '?'} min, "
                + (", ".join(h.get("causes") or []) or ("crashed" if h.get("crashed") else "closed normally"))
                for h in reversed(recent)
            ]
            form.addWidget(_label("<br>".join(lines), rich=True))
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
        self.win.refresh_header()
        return True


def open_reports() -> None:
    crash.reports_dir().mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(crash.reports_dir())))


class FixesDialog(QDialog):
    """Crash fixes in a window of their own, opened after a crash with what it showed."""

    def __init__(self, win: MainWindow, findings: list[crash.Finding] | None = None,
                 report_path: Path | None = None) -> None:
        super().__init__(win)
        self.setWindowTitle(f"{APP_NAME}: crash fixes")
        self.resize(720, 640)
        lay = QVBoxLayout(self)
        self.panel = FixesPanel(win, findings, report_path)
        lay.addWidget(_scrolled(self.panel), 1)
        buttons = QDialogButtonBox()
        save = buttons.addButton("Save a crash report now", QDialogButtonBox.ActionRole)
        save.setToolTip("Collect logs and kernel messages from the last hour into a zip")
        save.clicked.connect(lambda: win.collect_crash(None, None, show=True))
        folder = buttons.addButton("Open reports folder", QDialogButtonBox.ActionRole)
        folder.clicked.connect(open_reports)
        buttons.addButton(QDialogButtonBox.Save)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def save(self) -> None:
        if self.panel.save():
            self.accept()


# --- main window --------------------------------------------------------------

class MainWindow(QMainWindow):
    PLAY, ADDONS, BROWSE, FIXES, SETUP = range(5)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.setMinimumSize(980, 660)
        self.settings = Settings()
        self.mgr: Manager | None = Manager.from_settings(self.settings)
        self.registry: Registry | None = None
        self.updates: dict[str, github.Release] = {}
        self.untracked: dict[str, Addon] = {}  # installed by hand, found in the catalogue
        self.checks: list[syscheck.Check] | None = None
        self.pool = QThreadPool.globalInstance()
        self.jobs: set[Job] = set()
        self.busy = 0
        self.watch: official.GameWatch | None = None
        self.memory_warned = False
        self.game_state: tuple[str, str] | None = None
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(2000)
        self.watch_timer.timeout.connect(self.poll_game)

        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()  # the header has its own tab bar, to the right of the name
        self.tabs.addTab(self._build_play_tab(), "Play")
        self.tabs.addTab(self._build_installed_tab(), "My Addons")
        self.tabs.addTab(self._build_browse_tab(), "Get Addons")
        self.tabs.addTab(self._build_fixes_tab(), "Crash fixes")
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.tabs.currentChanged.connect(self._tab_shown)
        self.tab_bar = QTabBar()
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(False)
        for i in range(self.tabs.count()):
            self.tab_bar.addTab(self.tabs.tabText(i))
        self.tab_bar.currentChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.tab_bar.setCurrentIndex)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 14, 0)
        head.addWidget(self._build_brand(), 0, Qt.AlignBottom)
        head.addStretch()
        head.addWidget(self.tab_bar, 0, Qt.AlignBottom)
        lay.addLayout(head)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        # Live readout of the game's 4 GB while it runs; full means a likely out-of-memory crash.
        self.memory_bar = QProgressBar()
        self.memory_bar.setRange(0, crash.WARN_KIB)
        self.memory_bar.setFixedWidth(340)
        self.memory_bar.setToolTip("How much of its 4 GB of address space the 32-bit game uses. Sessions that "
                                   "crashed ran out at about 3.85 GB. Lower texture quality if it gets close.")
        self.memory_bar.hide()
        self.statusBar().addPermanentWidget(self.memory_bar)

        self.refresh_installed()
        QTimer.singleShot(0, self.load_registry)
        QTimer.singleShot(0, self.check_app_update)
        QTimer.singleShot(0, self.check_system)
        if not self.settings.get("setup_done"):
            QTimer.singleShot(0, self.show_setup)

    # layout

    def _build_brand(self) -> QWidget:
        brand = QWidget()
        lay = QVBoxLayout(brand)
        lay.setContentsMargins(26, 0, 0, 6)
        lay.setSpacing(0)
        name = QLabel("ArcheAge Classic")
        name.setObjectName("brand")
        lay.addWidget(name)
        sub = QLabel(f"Linux Launcher {__version__}")
        sub.setObjectName("brandSub")
        lay.addWidget(sub)
        return brand

    def _build_play_tab(self) -> QWidget:
        """A plaque with what is ready and the Play button, then your addons, the checks and recent sessions."""
        w = QWidget()
        w.setObjectName("paper")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(26, 22, 26, 18)
        lay.setSpacing(0)

        plaque = QFrame()
        plaque.setObjectName("plaque")
        top = QHBoxLayout(plaque)
        top.setContentsMargins(26, 20, 22, 20)
        top.setSpacing(24)
        words = QVBoxLayout()
        words.setSpacing(4)
        self.state = QLabel()
        self.state.setObjectName("h1")
        self.state.setWordWrap(True)
        words.addWidget(self.state)
        self.detail = QLabel()
        self.detail.setObjectName("faded")
        self.detail.setWordWrap(True)
        words.addWidget(self.detail)
        words.addSpacing(14)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.steps = []
        for i in range(4):
            num = QLabel(str(i + 1))
            num.setObjectName("step")
            num.setAlignment(Qt.AlignCenter)
            num.setFixedSize(22, 22)
            steps.addWidget(num)
            text = QLabel()
            steps.addWidget(text)
            steps.addSpacing(10)
            self.steps.append(text)
        steps.addStretch()
        words.addLayout(steps)
        words.addStretch()
        top.addLayout(words, 1)
        go = QVBoxLayout()
        go.setSpacing(6)
        self.play_btn = _button("Play", self.play, "Update addons, then start the game through the official launcher")
        self.play_btn.setObjectName("play")
        self.play_btn.setFixedSize(280, 58)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        go.addStretch()
        go.addWidget(self.play_btn)
        go.addStretch()
        top.addLayout(go)
        lay.addWidget(plaque)
        lay.addSpacing(8)

        cols = QHBoxLayout()
        cols.setSpacing(30)
        addons = self._section("Your addons", "update_all", "Update all", self.update_all)
        self.home_addons = QGridLayout()
        self.home_addons.setVerticalSpacing(0)
        holder = QWidget()
        holder.setObjectName("paper")
        holder.setLayout(self.home_addons)
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(holder)
        inner.addStretch()
        box = QWidget()
        box.setObjectName("paper")
        box.setLayout(inner)
        area = QScrollArea()
        area.setObjectName("paper")
        area.setFrameShape(QFrame.NoFrame)
        area.setWidgetResizable(True)
        area.setWidget(box)
        addons.addWidget(area, 1)
        cols.addLayout(addons, 5)

        checks = self._section("Before you play", "checks", "Setup", lambda: self.go(self.SETUP))
        self.facts = QGridLayout()
        self.facts.setVerticalSpacing(0)
        checks.addLayout(self.facts)
        checks.addStretch()
        cols.addLayout(checks, 4)

        sessions = self._section("Recent sessions", "sessions", "Crash fixes", lambda: self.go(self.FIXES))
        self.home_sessions = QGridLayout()
        self.home_sessions.setVerticalSpacing(0)
        sessions.addLayout(self.home_sessions)
        sessions.addStretch()
        cols.addLayout(sessions, 4)
        lay.addLayout(cols, 1)
        return w

    def _section(self, title: str, key: str, action: str, slot: Callable) -> QVBoxLayout:
        """A serif heading with a faded note and a link on the right, over a bronze rule."""
        box = QVBoxLayout()
        box.setSpacing(4)
        box.setContentsMargins(0, 14, 0, 0)
        row = QHBoxLayout()
        head = QLabel(title)
        head.setObjectName("h2")
        row.addWidget(head)
        note = QLabel()
        note.setObjectName("faded")
        row.addWidget(note, 0, Qt.AlignBottom)
        row.addStretch()
        link = _button(action, slot)
        link.setObjectName("link")
        link.setCursor(Qt.PointingHandCursor)
        row.addWidget(link)
        box.addLayout(row)
        box.addWidget(_rule())
        setattr(self, f"{key}_note", note)
        setattr(self, f"{key}_link", link)
        return box

    def _build_installed_tab(self) -> QWidget:
        w, lay = _tab()
        row = QHBoxLayout()
        self.folder_label = _label(muted=True)
        self.folder_label.setWordWrap(False)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.folder_label)
        row.addWidget(_button("Change folder…", self.choose_folder))
        row.addStretch()
        self.check_btn = _button("Check for updates", self.check_updates)
        row.addWidget(self.check_btn)
        self.update_all_btn = _button("Update all", self.update_all)
        row.addWidget(self.update_all_btn)
        lay.addLayout(row)
        self.track_row = QWidget()
        tr = QHBoxLayout(self.track_row)
        tr.setContentsMargins(0, 0, 0, 0)
        self.track_label = _label(muted=True)
        tr.addWidget(self.track_label, 1)
        self.track_all_btn = _button("Track all", lambda: self.track(sorted(self.untracked)),
                                     "Check these addons for updates from now on; their settings stay as they are")
        tr.addWidget(self.track_all_btn)
        self.track_row.hide()
        lay.addWidget(self.track_row)
        self.installed = _table(["On", "Addon", "Installed", "Latest", ""], stretch=1)
        self.installed.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.installed.setColumnWidth(4, 180)
        lay.addWidget(self.installed, 1)
        self.empty_label = _label("No addons yet. Find some in Get Addons.", muted=True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_label)
        return w

    def _build_browse_tab(self) -> QWidget:
        w, lay = _tab()
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search addons")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_browse)
        row.addWidget(self.search, 1)
        self.tag_filter = QComboBox()
        self.tag_filter.addItem("All tags")
        self.tag_filter.currentIndexChanged.connect(self.refresh_browse)
        row.addWidget(self.tag_filter)
        lay.addLayout(row)
        self.browse = _table(["Addon", "Author", "Tags", "Description", ""], stretch=3)
        self.browse.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.browse.setColumnWidth(4, 110)
        lay.addWidget(self.browse, 1)
        return w

    def _build_fixes_tab(self) -> QWidget:
        w, lay = _tab()
        self.fixes_area = QScrollArea()
        self.fixes_area.setWidgetResizable(True)
        self.fixes_area.setFrameShape(QFrame.NoFrame)
        self.fixes_area.setObjectName("paper")
        lay.addWidget(self.fixes_area, 1)
        row = QHBoxLayout()
        row.addWidget(_button("Save a crash report now", lambda: self.collect_crash(None, None, show=True),
                              "Collect logs and kernel messages from the last hour into a zip"))
        row.addWidget(_button("Open reports folder", open_reports))
        row.addStretch()
        row.addWidget(_button("Save", lambda: self.fixes_panel.save()))
        lay.addLayout(row)
        self._fill_fixes()
        return w

    def _fill_fixes(self) -> None:
        """Rebuilt each time the tab is shown, so it shows the saved settings and the latest sessions."""
        self.fixes_panel = FixesPanel(self)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(self.fixes_panel)
        lay.addStretch()
        old = self.fixes_area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.fixes_area.setWidget(holder)

    def _build_setup_tab(self) -> QWidget:
        w, lay = _tab()
        self.setup_panel = SetupPanel(self)
        lay.addWidget(_scrolled(self.setup_panel), 1)
        row = QHBoxLayout()
        row.addWidget(_button("Copy diagnostics", self.copy_report,
                              "Your system details, the launcher's log and the official launcher's log, "
                              "for a bug report or anyone helping you"))
        row.addWidget(_button("Open log folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(logs.path().parent))), str(logs.path())))
        row.addWidget(_button("Report a bug", lambda: QDesktopServices.openUrl(
            QUrl(f"https://github.com/{APP_REPO}/issues/new?template=bug_report.yml")),
            "Opens GitHub; paste the diagnostics there"))
        row.addStretch()
        lay.addLayout(row)
        return w

    def _tab_shown(self, index: int) -> None:
        if index == self.FIXES:
            self._fill_fixes()
        elif index == self.SETUP:
            self.setup_panel.refresh()

    def go(self, index: int) -> None:
        if self.tabs.currentIndex() == index:
            self._tab_shown(index)
        self.tabs.setCurrentIndex(index)

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
            box = QMessageBox(QMessageBox.Warning, APP_NAME, message, parent=self)
            token = box.addButton("Add a token", QMessageBox.ActionRole) if github.TOKEN_HINT in message else None
            box.addButton(QMessageBox.Ok)
            box.exec()
            if token is not None and box.clickedButton() is token:
                self.go(self.SETUP)
                self.setup_panel.token_edit.setFocus()
            if failed:
                failed(message)

        job.signals.done.connect(ok)
        job.signals.failed.connect(fail)
        self.pool.start(job)

    def _set_enabled(self, on: bool) -> None:
        for widget in (self.check_btn, self.update_all_btn, self.installed, self.browse):
            widget.setEnabled(on)
        if on:
            self.update_all_btn.setEnabled(bool(self.updates))

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
            self.refresh_installed()  # also finds hand-installed addons that are in the catalogue
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
            link = QLabel(f'<a href="{data["html_url"]}">Version {data["tag_name"]} is available</a>')
            link.setOpenExternalLinks(True)
            self.statusBar().addPermanentWidget(link)

        job = Job(fetch)
        self.jobs.add(job)
        job.signals.done.connect(show)
        job.signals.done.connect(lambda _: self.jobs.discard(job))
        self.pool.start(job)

    def check_system(self) -> None:
        """Run the driver checks off the UI thread for the Play tab."""
        def show(checks: list[syscheck.Check]) -> None:
            self.checks = checks
            self.refresh_header()

        job = Job(syscheck.run)
        self.jobs.add(job)
        job.signals.done.connect(show)
        job.signals.done.connect(lambda _: self.jobs.discard(job))
        self.pool.start(job)

    def refresh_header(self) -> None:
        """The Play tab: what is ready, the player's addons and their last sessions."""
        n = len(self.updates)
        local = self.mgr.local() if self.mgr else []
        launcher = official.locate(self.settings) or self.settings.get("launch")
        bad = [c for c in self.checks or [] if c.status == syscheck.FAIL]
        if self.game_state:
            self.state.setText(self.game_state[0])
            self.detail.setText(self.game_state[1])
        elif not launcher or bad:
            self.state.setText("Almost ready")
            self.detail.setText("Setup has something to fix first.")
        else:
            self.state.setText("Ready to play")
            self.detail.setText(f"{n} addon update{'s go' if n != 1 else ' goes'} in first." if n else
                                "Your addons are up to date.")

        for label, text in zip(self.steps, (
                f"Update {n} addon{'s' if n != 1 else ''}" if n else "Addons are up to date",
                "The official launcher opens",
                "Log in and press Play there",
                "Crashes get explained here")):
            label.setText(text)

        facts: list[tuple[str, str, str]] = []
        if self.mgr is None:
            facts.append(("Addons", "No addon folder yet", "bad"))
        else:
            on = sum(1 for a in local if a.enabled)
            facts.append(("Addons", f"{len(local)} installed, {on} on" + (f" · {n} update{'s' if n != 1 else ''}" if n else ""),
                          "good" if n else ""))
        chosen = wine.choose(self.settings.get("wine"), opt_in=bool(self.settings.get("wow64_opt_in")))
        facts.append(("Wine", chosen.name if chosen else "The official launcher's choice", ""))
        if self.checks is None:
            facts.append(("Drivers", "Checking…", ""))
        else:
            facts.append(("Drivers", f"{len(bad)} missing, see Setup" if bad else "All found", "bad" if bad else ""))
        facts.append(("Official launcher", "Found" if launcher else "Not set up, see Setup", "" if launcher else "bad"))
        on = [crash.BY_KEY[k].title for k in self.settings.get("fixes", []) if k in crash.BY_KEY]
        facts.append(("Crash fixes", f"{len(on)} on" if on else "None on", ""))
        _fill_columns(self.facts, [(_cell(k, "faded"), _cell(v, role)) for k, v, role in facts], 1)

        rows = []
        for a in local:
            meta = self.registry.get(a.name) if self.registry else None
            box = QCheckBox(meta.alias if meta else a.name)
            box.setChecked(a.enabled)
            box.setToolTip("Load this addon in game")
            box.toggled.connect(lambda checked, name=a.name: self.toggle(name, checked))
            latest = self.updates.get(a.name)
            rows.append((box, _cell(f"{a.version} → {latest.version}" if latest else a.version,
                                    "good" if latest else "faded")))
        _fill_columns(self.home_addons, rows, 1)
        self.update_all_note.setText("")
        self.update_all_link.setText(f"Update all ({n})" if n else "")
        self.update_all_link.setVisible(bool(n))

        sessions = []
        for h in reversed(crash.history(6)):
            try:
                when = time.strftime("%a %-d %b, %H:%M", time.strptime(h.get("time", ""), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                when = h.get("time", "")[:16]
            mins = h.get("minutes")
            length = (f"{int(mins // 60)} h {int(mins % 60):02d}" if mins and mins >= 60 else
                      f"{round(mins)} min" if mins else "")
            if h.get("crashed"):
                what = ", ".join(CAUSES.get(c, c) for c in h.get("causes") or []) or "crashed"
                sessions.append((_cell(when), _cell(f"{length} · {what}".strip(" ·"), "bad")))
            else:
                sessions.append((_cell(when), _cell(f"{length} · closed normally".strip(" ·"), "faded")))
        if not sessions:
            sessions.append((_cell("None yet. Sessions you start with Play show up here.", "faded"), _cell("")))
        _fill_columns(self.home_sessions, sessions, 1)
        self.tab_bar.setTabText(self.ADDONS, f"My Addons ({n})" if n else "My Addons")

    def refresh_installed(self) -> None:
        if self.mgr is None:
            self.folder_label.setText("No addon folder found")
            self.installed.setRowCount(0)
            self.track_row.hide()
            self.empty_label.setText("Couldn't find ArcheAge Classic. Use Change folder… to pick ~/Documents/AAClassic.")
            self.empty_label.show()
            self.refresh_header()
            return
        self.folder_label.setText(str(self.mgr.install.addon_dir))
        local = self.mgr.local()
        self.untracked = self.mgr.untracked(self.registry) if self.registry else {}
        n = len(self.untracked)
        self.track_label.setText(
            f"{n} addon{'s' if n != 1 else ''} you installed by hand {'are' if n != 1 else 'is'} in the catalogue "
            f"({', '.join(self.untracked)}). Track {'them' if n != 1 else 'it'} to get updates.")
        self.track_row.setVisible(bool(n))
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
            failed = self.mgr.check_errors.get(a.name)
            if latest:
                status = _item(latest.version)
            elif failed:
                status = _item("could not check", failed)
            elif a.name in self.untracked:
                status = _item("not tracked", "Installed by hand. Track it to get updates from the catalogue.")
            else:
                status = _item("" if not a.managed else "up to date")
            status.setForeground(theme.colour("positive" if latest else "negative" if failed else "inactive"))
            self.installed.setItem(row, 3, status)
            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(4, 2, 4, 2)
            if latest:
                al.addWidget(_button("Update", lambda _=False, n=a.name: self.update([n])))
            elif a.name in self.untracked:
                al.addWidget(_button("Track", lambda _=False, n=a.name: self.track([n]),
                                     "Check it for updates from now on"))
            al.addWidget(_button("Remove", lambda _=False, n=a.name: self.remove(n)))
            self.installed.setCellWidget(row, 4, actions)
        self.update_all_btn.setText(f"Update all ({len(self.updates)})" if self.updates else "Update all")
        self.update_all_btn.setEnabled(bool(self.updates) and not self.busy)
        self.refresh_header()
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
            self.browse.setItem(row, 0, _item(a.alias, f"{a.name}\nhttps://github.com/{a.repo}"))
            self.browse.setItem(row, 1, _item(a.author))
            self.browse.setItem(row, 2, _item(", ".join(a.tags)))
            needs = f"\nNeeds: {', '.join(a.dependencies)}" if a.dependencies else ""
            self.browse.setItem(row, 3, _item(a.description, a.description + needs))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(4, 2, 4, 2)
            btn = _button("Installed" if a.name in installed else "Install", lambda _=False, addon=a: self.add(addon))
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

    def track(self, names: list[str]) -> None:
        if not self._guard():
            return
        for name in names:
            if name in self.untracked:
                self.mgr.track(name, self.untracked[name])
        self.statusBar().showMessage(f"Tracking {', '.join(names)}", 4000)
        self.refresh_installed()
        self.check_updates()

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
        self.statusBar().showMessage("Diagnostics copied. Paste them into a bug report or where you ask for help.", 6000)

    def _game(self, state: str | None, detail: str = "", button: str | None = None) -> None:
        """Show what the game is doing on the Play tab; a button text disables Play while it lasts."""
        self.game_state = (state, detail) if state else None
        self.play_btn.setText(button or "Play")
        self.play_btn.setEnabled(button is None)
        self.refresh_header()

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
                self._game(None)
                QMessageBox.warning(self, APP_NAME, f"Could not start the game: {e}")
                return
            self.watch = official.GameWatch(proc, sampler=crash.Sampler())
            self.memory_warned = False
            self.watch_timer.start()
            self.statusBar().showMessage("Official launcher started. Log in and press Play there.")
            self._game("The official launcher is open.", "Log in and press Play there.", "Starting…")

        if self.updates and self.mgr is not None and not paths.game_running():
            self._game("Updating your addons…", "", "Updating…")
            self.update(list(self.updates), then=start)
        else:
            start()

    def poll_game(self) -> None:
        if self.watch is None:
            return
        state = self.watch.poll()
        self._show_memory(self.watch)
        if state is None:
            self._check_memory(self.watch)
            return
        if state == "game":
            self.statusBar().showMessage("ArcheAge Classic is running.")
            self._game("ArcheAge Classic is running.", "", "In game")
            return
        watch = self.watch
        self.watch_timer.stop()
        self.watch = None
        self._game(None)
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
                self.go(self.SETUP)
            elif box.clickedButton() is copy:
                self.copy_report()

    def _show_memory(self, watch: official.GameWatch | None) -> None:
        used = watch.sampler.used_kib() if watch is not None and watch.sampler and watch.state == "game" else None
        if used is None:
            self.memory_bar.hide()
            return
        self.memory_bar.setValue(min(used, crash.WARN_KIB))
        self.memory_bar.setFormat(f"Game memory: {crash.meter(used)}")
        level = "negative" if used >= crash.WARN_KIB else "neutral" if used >= crash.CAUTION_KIB else "positive"
        colour = {"positive": "#7d9a4a", "neutral": "#c08a2e", "negative": "#b5452c"}[level]
        self.memory_bar.setStyleSheet(f"QProgressBar::chunk {{ background: {colour}; }}")
        self.memory_bar.show()

    def _check_memory(self, watch: official.GameWatch) -> None:
        """Warn once per session when the 32-bit game is about to run out of its 4 GB."""
        sampler = watch.sampler
        if sampler is None or self.memory_warned or not sampler.near_limit():
            return
        self.memory_warned = True
        used = (sampler.used_kib() or 0) / 1e6
        text = (f"ArcheAge Classic is using {used:.2f} of its 4 GB and may crash at the next zone load. "
                "Lower texture quality in the game's options, or log out somewhere safe.")
        self.statusBar().showMessage(text)
        notify(APP_NAME, text)
        QApplication.alert(self, 0)

    def collect_crash(self, sampler: crash.Sampler | None, played: float | None, show: bool) -> None:
        """Gather crash evidence in the background; show it if it points to a crash or if asked."""

        def done(result: tuple[Path, list[crash.Finding]]) -> None:
            path, findings = result
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
    logs.setup("aacam-gui")
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
