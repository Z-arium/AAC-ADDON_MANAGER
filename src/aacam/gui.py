"""Desktop app: `aacam-gui`. A Qt window over the same engine as the command line."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QPlainTextEdit,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__, crash, github, installer, official, paths, report, syscheck
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
        except (github.GitHubError, installer.InstallError, official.LauncherError, OSError) as e:
            self.signals.failed.emit(str(e))
        except Exception:  # keep the window alive and show what went wrong
            self.signals.failed.emit(traceback.format_exc(limit=3))
        else:
            self.signals.done.emit(result)


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
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


# --- setup ---------------------------------------------------------------------

ICON = {syscheck.OK: "✔", syscheck.WARN: "⚠", syscheck.FAIL: "✘"}


class SetupDialog(QDialog):
    """First-run setup, also under Setup in the toolbar: system check, official launcher, addon folder."""

    def __init__(self, win: MainWindow) -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"{APP_NAME} setup")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Three things get ArcheAge Classic running on Linux: the drivers the game needs, "
            "the official launcher (it handles login and patching), and your addon folder."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

        box = QGroupBox("1. Your system")
        form = QVBoxLayout(box)
        self.distro_label = QLabel()
        form.addWidget(self.distro_label)
        self.checks_label = QLabel()
        self.checks_label.setTextFormat(Qt.RichText)
        self.checks_label.setWordWrap(True)
        form.addWidget(self.checks_label)
        self.fixes = QPlainTextEdit()
        self.fixes.setReadOnly(True)
        self.fixes.setMaximumHeight(110)
        form.addWidget(self.fixes)
        row = QHBoxLayout()
        self.copy_fixes = QPushButton("Copy fix commands")
        self.copy_fixes.clicked.connect(lambda: QApplication.clipboard().setText(self.fixes.toPlainText()))
        row.addWidget(self.copy_fixes)
        again = QPushButton("Check again")
        again.clicked.connect(self.refresh)
        row.addWidget(again)
        row.addStretch()
        form.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("2. Official ArcheAge Classic launcher")
        form = QVBoxLayout(box)
        self.launcher_label = QLabel()
        self.launcher_label.setWordWrap(True)
        self.launcher_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.launcher_label)
        row = QHBoxLayout()
        self.download_btn = QPushButton("Download it")
        self.download_btn.clicked.connect(self.download)
        row.addWidget(self.download_btn)
        pick = QPushButton("Choose the file…")
        pick.clicked.connect(self.choose_launcher)
        row.addWidget(pick)
        row.addStretch()
        form.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("3. Addon folder")
        form = QHBoxLayout(box)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        form.addWidget(self.folder_label, 1)
        pick = QPushButton("Change…")
        pick.clicked.connect(self.choose_folder)
        form.addWidget(pick)
        lay.addWidget(box)

        buttons = QDialogButtonBox()
        copy_report = buttons.addButton("Copy report for help", QDialogButtonBox.ActionRole)
        copy_report.clicked.connect(self.win.copy_report)
        buttons.addButton("Done", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        d = syscheck.distro()
        self.distro_label.setText(f"<b>{d.name}</b>")
        checks = syscheck.run()
        self.checks_label.setText("<br>".join(
            f"{ICON[c.status]} <b>{c.title}</b>: {c.detail}" for c in checks
        ))
        fixes = syscheck.fix_script(checks)
        self.fixes.setPlainText(fixes or "Nothing to fix.")
        self.fixes.setVisible(bool(fixes))
        self.copy_fixes.setVisible(bool(fixes))
        launcher = official.locate(self.win.settings)
        if launcher:
            self.launcher_label.setText(f"✔ Found: {launcher}")
            self.download_btn.setText("Download again")
        else:
            self.launcher_label.setText(
                "✘ Not found. Download it here, or choose the AppImage if you already have it."
            )
            self.download_btn.setText("Download it")
        mgr = self.win.mgr
        self.folder_label.setText(
            f"✔ {mgr.install.addon_dir}" if mgr else "✘ Not found yet. It appears after the game's first start, "
            "or choose it now."
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


class FixesDialog(QDialog):
    """Crash fixes: switches applied the next time the game starts, and what the last crash showed."""

    def __init__(self, win: MainWindow, findings: list[crash.Finding] | None = None,
                 report_path: Path | None = None) -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"{APP_NAME}: crash fixes")
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        if findings is not None:
            box = QGroupBox("What the last crash shows")
            form = QVBoxLayout(box)
            text = QLabel("<br><br>".join(
                f"<b>{f.title}</b>. {f.detail}" for f in findings
            ) or "No known cause was found. Turn on diagnostics below and play again to record one.")
            text.setTextFormat(Qt.RichText)
            text.setWordWrap(True)
            form.addWidget(text)
            if report_path is not None:
                where = QLabel(f"Everything was saved to {report_path}. Attach it when you ask for help.")
                where.setWordWrap(True)
                where.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addWidget(where)
            lay.addWidget(box)

        recent = crash.history(5)
        if recent:
            lines = [
                f"{h.get('time', '?')[5:16]}: {h.get('minutes') or '?'} min, "
                + (", ".join(h.get("causes") or []) or ("crashed" if h.get("crashed") else "closed normally"))
                for h in reversed(recent)
            ]
            past = QLabel("Recent sessions:<br>" + "<br>".join(lines))
            past.setTextFormat(Qt.RichText)
            lay.addWidget(past)

        suggested = {f.fix for f in findings or [] if f.fix}
        on = set(win.settings.get("fixes", []))
        box = QGroupBox("Fixes, applied the next time the game starts")
        form = QVBoxLayout(box)
        self.boxes: dict[str, QCheckBox] = {}
        for fix in crash.FIXES:
            title = fix.title + ("  (suggested)" if fix.key in suggested and fix.key not in on else "")
            cb = QCheckBox(title)
            cb.setChecked(fix.key in on or fix.key in suggested)
            cb.setToolTip(fix.why)
            form.addWidget(cb)
            why = QLabel(fix.why)
            why.setWordWrap(True)
            why.setContentsMargins(24, 0, 0, 6)
            form.addWidget(why)
            self.boxes[fix.key] = cb
        self.diagnostics = QCheckBox("Diagnostics: record extra Wine, Proton and DXVK logs (slower, for bug reports)")
        self.diagnostics.setChecked(bool(win.settings.get("diagnostics")) or (findings is not None and not suggested))
        form.addWidget(self.diagnostics)
        lay.addWidget(box)

        buttons = QDialogButtonBox()
        save = buttons.addButton("Save a crash report now", QDialogButtonBox.ActionRole)
        save.setToolTip("Collect logs and kernel messages from the last hour into a zip")
        save.clicked.connect(lambda: win.collect_crash(None, None, show=True))
        folder = buttons.addButton("Open reports folder", QDialogButtonBox.ActionRole)
        folder.clicked.connect(self.open_reports)
        buttons.addButton(QDialogButtonBox.Save)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def save(self) -> None:
        for key, cb in self.boxes.items():
            crash.set_fix(self.win.settings, key, cb.isChecked())
        self.win.settings.set("diagnostics", self.diagnostics.isChecked())
        self.win.statusBar().showMessage("Saved. Fixes apply the next time you press Play.", 6000)
        self.accept()

    def open_reports(self) -> None:
        crash.reports_dir().mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(crash.reports_dir())))


# --- main window --------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 640)
        self.settings = Settings()
        self.mgr: Manager | None = Manager.from_settings(self.settings)
        self.registry: Registry | None = None
        self.updates: dict[str, github.Release] = {}
        self.pool = QThreadPool.globalInstance()
        self.jobs: set[Job] = set()
        self.busy = 0
        self.watch: official.GameWatch | None = None
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(2000)
        self.watch_timer.timeout.connect(self.poll_game)

        self._build_toolbar()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_installed_tab(), "My Addons")
        self.tabs.addTab(self._build_browse_tab(), "Get Addons")
        self.setCentralWidget(self.tabs)
        self.statusBar()

        self.refresh_installed()
        QTimer.singleShot(0, self.load_registry)
        QTimer.singleShot(0, self.check_app_update)
        if not self.settings.get("setup_done"):
            QTimer.singleShot(0, self.show_setup)

    # layout

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.folder_label = QLabel()
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.folder_label)
        change = QAction("Change folder…", self)
        change.triggered.connect(self.choose_folder)
        bar.addAction(change)
        setup = QAction("Setup…", self)
        setup.triggered.connect(self.show_setup)
        bar.addAction(setup)
        fixes = QAction("Crash fixes…", self)
        fixes.triggered.connect(lambda: FixesDialog(self).exec())
        bar.addAction(fixes)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.clicked.connect(self.check_updates)
        bar.addWidget(self.check_btn)
        self.update_all_btn = QPushButton("Update all")
        self.update_all_btn.clicked.connect(self.update_all)
        bar.addWidget(self.update_all_btn)
        self.play_btn = QPushButton("Play")
        self.play_btn.setToolTip("Update addons, then start the game through the official launcher")
        self.play_btn.clicked.connect(self.play)
        bar.addWidget(self.play_btn)

    def _build_installed_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.installed = _table(["On", "Addon", "Installed", "Latest", ""], stretch=1)
        lay.addWidget(self.installed)
        self.empty_label = QLabel("No addons yet. Find some in Get Addons.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_label)
        return w

    def _build_browse_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search addons")
        self.search.textChanged.connect(self.refresh_browse)
        row.addWidget(self.search)
        self.tag_filter = QComboBox()
        self.tag_filter.addItem("All tags")
        self.tag_filter.currentIndexChanged.connect(self.refresh_browse)
        row.addWidget(self.tag_filter)
        lay.addLayout(row)
        self.browse = _table(["Addon", "Author", "Tags", "Description", ""], stretch=3)
        lay.addWidget(self.browse)
        return w

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
            link = QLabel(f'<a href="{data["html_url"]}">Version {data["tag_name"]} is available</a>')
            link.setOpenExternalLinks(True)
            self.statusBar().addPermanentWidget(link)

        job = Job(fetch)
        self.jobs.add(job)
        job.signals.done.connect(show)
        job.signals.done.connect(lambda _: self.jobs.discard(job))
        self.pool.start(job)

    def refresh_installed(self) -> None:
        if self.mgr is None:
            self.folder_label.setText("  No addon folder found  ")
            self.installed.setRowCount(0)
            self.empty_label.setText("Couldn't find ArcheAge Classic. Use Change folder… to pick ~/Documents/AAClassic.")
            self.empty_label.show()
            return
        self.folder_label.setText(f"  {self.mgr.install.addon_dir}  ")
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
            cl.setContentsMargins(8, 0, 0, 0)
            cl.addWidget(box)
            self.installed.setCellWidget(row, 0, cell)
            meta = self.registry.get(a.name) if self.registry else None
            title = meta.alias if meta else a.name
            self.installed.setItem(row, 1, _item(title, meta.description if meta else ""))
            self.installed.setItem(row, 2, _item(a.version))
            latest = self.updates.get(a.name)
            self.installed.setItem(row, 3, _item(latest.version if latest else ("" if not a.managed else "up to date")))
            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(0, 0, 0, 0)
            if latest:
                up = QPushButton("Update")
                up.clicked.connect(lambda _=False, n=a.name: self.update([n]))
                al.addWidget(up)
            rm = QPushButton("Remove")
            rm.clicked.connect(lambda _=False, n=a.name: self.remove(n))
            al.addWidget(rm)
            self.installed.setCellWidget(row, 4, actions)
        self.update_all_btn.setText(f"Update all ({len(self.updates)})" if self.updates else "Update all")
        self.update_all_btn.setEnabled(bool(self.updates) and not self.busy)
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
            btn = QPushButton("Installed" if a.name in installed else "Install")
            btn.setEnabled(a.name not in installed)
            btn.clicked.connect(lambda _=False, addon=a: self.add(addon))
            self.browse.setCellWidget(row, 4, btn)

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

    def copy_report(self) -> None:
        QApplication.clipboard().setText(report.build(self.settings))
        self.statusBar().showMessage("Report copied. Paste it where you ask for help.", 6000)

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
                                            env={**os.environ, **extra})
                else:
                    proc = official.start(appimage, syscheck.has_lib("libfuse.so.2", 64), extra)
            except (official.LauncherError, OSError) as e:
                QMessageBox.warning(self, APP_NAME, f"Could not start the game: {e}")
                return
            self.watch = official.GameWatch(proc, sampler=crash.Sampler())
            self.watch_timer.start()
            self.statusBar().showMessage("Official launcher started. Log in and press Play there.")

        if self.updates and self.mgr is not None and not paths.game_running():
            self.update(list(self.updates), then=start)
        else:
            start()

    def poll_game(self) -> None:
        if self.watch is None:
            return
        state = self.watch.poll()
        if state is None:
            return
        if state == "game":
            self.statusBar().showMessage("ArcheAge Classic is running.")
            return
        watch = self.watch
        self.watch_timer.stop()
        self.watch = None
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
                self.show_setup()
            elif box.clickedButton() is copy:
                self.copy_report()

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


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("aac-linux-launcher")
    icon = Path(__file__).with_name("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
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
