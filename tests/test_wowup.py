"""v0.4: release notes, roll back, settings backups, update options."""

from unittest import mock

from aacam import backup, cli, config, core, github, installer, paths, registry
from test_aacam import TempHome, make_zip


class Base(TempHome):
    def setUp(self):
        super().setUp()
        self.inst = paths.install_from_root(self.home / "Documents/AAClassic")
        self.inst.addon_dir.mkdir(parents=True)
        config.Settings().set("addon_dir", str(self.inst.addon_dir))
        self.reg = registry.Registry({"Foo": registry.Addon("Foo", "Foo", "", "x", "o/foo", "main")})
        self.mgr = core.Manager(self.inst)

    def install(self, version: str, body: str):
        with mock.patch.object(github, "download", side_effect=lambda url, dest: make_zip(dest, {"r/main.lua": body})):
            self.mgr.install_one(self.reg.addons["Foo"], github.Release(version, "u", published="2026-10-0%sT00:00:00Z" % version[-1]))


class RollBackTests(Base):
    def test_roll_back_and_forward_keeping_settings(self):
        self.install("v1", "one")
        data = self.inst.addon_dir / "Foo/.data"
        data.mkdir()
        (data / "s.lua").write_text("mine")
        self.install("v2", "two")
        self.assertEqual(self.mgr.can_roll_back("Foo"), "v1")
        self.assertEqual(self.mgr.roll_back("Foo"), "v1")
        self.assertEqual((self.inst.addon_dir / "Foo/main.lua").read_text(), "one")
        self.assertEqual((data / "s.lua").read_text(), "mine")
        # v2 isn't offered again right away, but a newer v3 is
        with mock.patch.object(github, "latest_release", return_value=github.Release("v2", "u")):
            self.assertEqual(self.mgr.check_updates(self.reg), {})
        with mock.patch.object(github, "latest_release", return_value=github.Release("v3", "u")):
            self.assertIn("Foo", self.mgr.check_updates(self.reg))
        self.assertEqual(self.mgr.roll_back("Foo"), "v2")
        self.assertEqual((self.inst.addon_dir / "Foo/main.lua").read_text(), "two")

    def test_only_two_zips_are_kept(self):
        for v in ("v1", "v2", "v3"):
            self.install(v, v)
        kept = sorted(p.name for p in self.mgr.archive_dir("Foo").glob("*.zip"))
        self.assertEqual(kept, ["v2.zip", "v3.zip"])
        self.mgr.remove("Foo")
        self.assertFalse(self.mgr.archive_dir("Foo").exists())

    def test_nothing_to_roll_back(self):
        self.install("v1", "one")
        with self.assertRaises(installer.InstallError):
            self.mgr.roll_back("Foo")

    def test_cli_rollback(self):
        self.install("v1", "one")
        self.install("v2", "two")
        with mock.patch.object(paths, "game_running", return_value=False):
            self.assertEqual(cli.main(["rollback", "Foo"]), 0)
        self.assertEqual(core.Manager(self.inst).db.addons["Foo"]["version"], "v1")


class BackupTests(Base):
    def test_backup_and_restore(self):
        self.install("v1", "one")
        (self.inst.addon_dir / "Foo/.data").mkdir()
        (self.inst.addon_dir / "Foo/.data/s.lua").write_text("good")
        snap = backup.create(self.inst)
        self.assertEqual(backup.contents(snap), (True, ["Foo"]))
        # an update goes wrong: settings and addons.txt lost
        (self.inst.addon_dir / "Foo/.data/s.lua").write_text("broken")
        installer.write_enabled(self.inst, [])
        safety = backup.restore(self.inst, snap)
        self.assertEqual((self.inst.addon_dir / "Foo/.data/s.lua").read_text(), "good")
        self.assertEqual(installer.read_enabled(self.inst), ["Foo"])
        self.assertNotEqual(safety.path, snap.path)
        self.assertEqual(len(backup.list_backups(self.inst)), 2)

    def test_settings_of_removed_addons_wait_for_a_reinstall(self):
        self.install("v1", "one")
        (self.inst.addon_dir / "Foo/.data").mkdir()
        (self.inst.addon_dir / "Foo/.data/s.lua").write_text("good")
        snap = backup.create(self.inst)
        self.mgr.remove("Foo", purge=True)
        backup.restore(self.inst, snap)
        self.install("v1", "one")
        self.assertEqual((self.inst.addon_dir / "Foo/.data/s.lua").read_text(), "good")

    def test_automatic_backups_are_pruned(self):
        with mock.patch.object(backup, "KEEP_AUTO", 2):
            mine = backup.create(self.inst)
            for _ in range(4):
                backup.create(self.inst, auto=True)
        snaps = backup.list_backups(self.inst)
        self.assertEqual(sum(b.auto for b in snaps), 2)
        self.assertIn(mine.path, [b.path for b in snaps])

    def test_update_makes_a_backup_first(self):
        self.install("v1", "one")
        with mock.patch.object(registry.Registry, "load", return_value=self.reg), \
             mock.patch.object(github, "latest_release", return_value=github.Release("v2", "u")), \
             mock.patch.object(github, "download", side_effect=lambda url, dest: make_zip(dest, {"r/main.lua": "2"})), \
             mock.patch.object(paths, "game_running", return_value=False):
            self.assertEqual(cli.main(["update"]), 0)
            self.assertEqual(cli.main(["backup", "--list"]), 0)
            self.assertEqual(cli.main(["backup", "--restore", "1"]), 0)
        self.assertTrue(any(b.auto for b in backup.list_backups(self.inst)))


class NotesTests(Base):
    def test_changelog_stops_at_the_installed_version(self):
        self.install("v1", "one")
        rels = [github.Release(v, "u", notes=f"notes {v}") for v in ("v3", "v2", "v1", "v0")]
        with mock.patch.object(github, "releases", return_value=rels), \
             mock.patch.object(registry.Registry, "load", return_value=self.reg):
            self.assertEqual([r.version for r in self.mgr.changelog(self.reg, "Foo")], ["v3", "v2"])
            self.assertEqual(cli.main(["changes", "Foo"]), 0)

    def test_release_fields_and_drafts(self):
        api = [
            {"tag_name": "v2", "draft": True},
            {"tag_name": "v1.1", "body": "Fixed it", "published_at": "2026-10-01T10:00:00Z",
             "assets": [{"download_count": 5}, {"download_count": 7}]},
        ]
        with mock.patch.object(github, "get_json", return_value=api):
            rels = github.releases("o/r", "main")
        self.assertEqual([(r.version, r.notes, r.downloads, r.published[:10]) for r in rels],
                         [("v1.1", "Fixed it", 12, "2026-10-01")])

    def test_cached_release_never_asks_github(self):
        self.assertIsNone(github.cached_release("o/r"))
        github._cache_put(f"{github.API}/repos/o/r/releases/latest",
                          {"status": 200, "etag": None, "time": 0, "body": {"tag_name": "v9", "published_at": "2026"}})
        with mock.patch("aacam.net.urlopen", side_effect=AssertionError("no network")):
            self.assertEqual(github.cached_release("o/r").version, "v9")


class OptionTests(Base):
    def test_options(self):
        self.assertEqual(cli.main(["options", "update-on-start=on", "update-before-play=off"]), 0)
        s = config.Settings()
        self.assertEqual((s.get("update_on_start"), s.get("update_before_play")), (True, False))
        self.assertEqual(cli.main(["options", "nope=on"]), 1)
