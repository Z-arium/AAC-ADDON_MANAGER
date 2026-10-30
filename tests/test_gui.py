"""The desktop app's core flows, offscreen: install, track, update, disable, remove, token, rate limit."""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication, QMessageBox

    from aacam import gui
except ImportError as e:  # PySide6 or its system libraries missing: CLI-only test run
    gui = None
    SKIP = str(e)

from aacam import config, github, installer, paths, registry
from test_aacam import TempHome, make_zip


@unittest.skipIf(gui is None, "PySide6 not available")
class GuiFlowTests(TempHome):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.addon_dir = self.home / "Documents/AAClassic/Addon"
        (self.addon_dir / "Hand").mkdir(parents=True)
        (self.addon_dir / "Hand/main.lua").write_text("old")
        config.Settings().set("setup_done", True)
        self.reg = registry.Registry({
            "Lib": registry.Addon("Lib", "Lib", "a library", "x", "o/lib", "main"),
            "Hand": registry.Addon("Hand", "Hand Made", "", "x", "o/hand", "main"),
        })
        self.versions = {"o/lib": "v1", "o/hand": "v1"}
        patches = [
            mock.patch.object(gui.QTimer, "singleShot"),  # no catalogue, update or system check on start
            mock.patch.object(registry.Registry, "load", return_value=self.reg),
            mock.patch.object(github, "latest_release",
                              side_effect=lambda repo, branch: github.Release(self.versions[repo], repo)),
            mock.patch.object(github, "download",
                              side_effect=lambda url, dest: make_zip(dest, {"r/main.lua": url})),
            mock.patch.object(paths, "game_running", return_value=False),
            mock.patch.object(gui.QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(gui.UpdateDialog, "exec", return_value=gui.QDialog.Accepted),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win = gui.MainWindow()
        self.addCleanup(self.win.deleteLater)

    def wait(self):
        """Let background jobs finish and their results reach the window."""
        for _ in range(50):
            QThreadPool.globalInstance().waitForDone(2000)
            self.app.processEvents()
            if not self.win.busy:
                break
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_install_track_update_disable_remove(self):
        self.win.load_registry()
        self.wait()
        self.assertEqual(list(self.win.untracked), ["Hand"])
        self.assertFalse(self.win.track_row.isHidden())

        self.win.add(self.reg.addons["Lib"])
        self.wait()
        self.assertTrue((self.addon_dir / "Lib/main.lua").is_file())
        self.assertIn("Lib", installer.read_enabled(self.win.mgr.install))

        self.win.track(["Hand"])
        self.wait()
        self.assertEqual(self.win.untracked, {})
        self.assertEqual(set(self.win.updates), {"Hand"})  # version unknown, so the latest is offered

        self.versions["o/lib"] = "v2"
        self.win.check_updates()
        self.wait()
        self.assertEqual(set(self.win.updates), {"Hand", "Lib"})
        self.win.update_all()
        self.wait()
        self.assertEqual(self.win.updates, {})
        self.assertEqual(self.win.mgr.db.addons["Lib"]["version"], "v2")
        self.assertEqual((self.addon_dir / "Hand/main.lua").read_text(), "o/hand")

        self.win.toggle("Lib", False)
        self.assertNotIn("Lib", installer.read_enabled(self.win.mgr.install))
        self.win.remove("Lib")
        self.assertFalse((self.addon_dir / "Lib").exists())

    def test_rate_limit_offers_to_add_a_token(self):
        shown = []

        def fake_exec(box):
            shown.append(box.text())
            token = next(b for b in box.buttons() if b.text() == "Add a token")
            box.clickedButton = lambda: token
            return 0

        with mock.patch.object(gui.QMessageBox, "exec", fake_exec):
            self.win.run("x", lambda: (_ for _ in ()).throw(github.RateLimited(None, False)), lambda _r: None)
            self.wait()
        self.assertIn(github.TOKEN_HINT, shown[0])
        self.assertEqual(self.win.tabs.currentIndex(), self.win.SETUP)

    def test_token_is_saved_from_setup(self):
        panel = self.win.setup_panel
        panel.token_edit.setText(" ghp_from_gui ")
        with mock.patch.object(github, "rate_limit", return_value={"limit": 5000, "remaining": 5000, "reset": 0}):
            panel.save_token()
            self.wait()
        self.assertEqual(config.Settings().get("github_token"), "ghp_from_gui")
        self.assertEqual(panel.token_edit.text(), "")
        self.assertIn("5000 of 5000", panel.token_label.text())
        panel.clear_token()
        self.assertIsNone(config.Settings().get("github_token"))

    def test_details_backups_rollback_and_filters(self):
        self.win.load_registry()
        self.wait()
        self.win.add(self.reg.addons["Lib"])
        self.wait()
        self.versions["o/lib"] = "v2"
        self.win.check_updates()
        self.wait()
        self.win.update(["Lib"])
        self.wait()
        notes = [github.Release("v2", "u", notes="**Faster**", published="2026-10-30T00:00:00Z")]
        with mock.patch.object(github, "releases", return_value=notes):
            dialog = gui.AddonDialog(self.win, "Lib")
            self.wait()
        self.assertIn("Faster", dialog.notes.toPlainText())
        labels = [b.text() for b in dialog.findChildren(gui.QPushButton)]
        self.assertIn("Roll back to v1", labels)
        self.win.roll_back("Lib")
        self.assertEqual(self.win.mgr.db.addons["Lib"]["version"], "v1")

        backups = gui.BackupsDialog(self.win)
        self.assertGreaterEqual(backups.list.count(), 1)  # the automatic one before the update
        backups.make()
        self.assertGreaterEqual(backups.list.count(), 2)

        self.win.addon_filter.setCurrentIndex(1)  # enabled only
        names = {self.win.installed.item(r, 1).data(gui.Qt.UserRole) for r in range(self.win.installed.rowCount())}
        self.assertEqual(names, {"Lib"})
        self.win.addon_filter.setCurrentIndex(0)
        self.win.browse_sort.setCurrentIndex(1)
        self.assertEqual(self.win.browse.rowCount(), 2)

    def test_copy_diagnostics(self):
        self.win.copy_report()
        self.assertIn("ArcheAge Classic Linux Launcher", QApplication.clipboard().text())
