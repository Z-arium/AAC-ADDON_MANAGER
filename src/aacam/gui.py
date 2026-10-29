"""Desktop app: `aacam-gui`. A Qt window over the same engine as the command line."""

from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
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

from . import __version__, github, installer, paths
from .config import Settings
from .core import Manager
from .registry import Addon, Registry

APP_NAME = "AAC Addon Manager"
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
        except (github.GitHubError, installer.InstallError, OSError) as e:
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

        self._build_toolbar()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_installed_tab(), "My Addons")
        self.tabs.addTab(self._build_browse_tab(), "Get Addons")
        self.setCentralWidget(self.tabs)
        self.statusBar()

        self.refresh_installed()
        QTimer.singleShot(0, self.load_registry)
        QTimer.singleShot(0, self.check_app_update)

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
        self.play_btn.setToolTip("Start the game with your saved launch command")
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

    def run(self, label: str, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
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

    def update(self, names: list[str]) -> None:
        if not self._guard() or self.registry is None:
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

        self.run("Updating…", work, finished)

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

    def play(self) -> None:
        command = self.settings.get("launch")
        if not command:
            launcher = Path.home() / ".local/share/ArcheAgeClassic/ArcheAgeClassic.AppImage"
            suggestion = str(launcher) if launcher.exists() else ""
            command, ok = QInputDialog.getText(
                self, APP_NAME, "Command that starts the game (saved for next time):", text=suggestion,
            )
            if not ok or not command.strip():
                return
            self.settings.set("launch", command.strip())
        subprocess.Popen(["sh", "-c", self.settings.get("launch")], start_new_session=True)
        self.statusBar().showMessage("Starting ArcheAge Classic…", 4000)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("aac-addon-manager")
    icon = Path(__file__).with_name("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
