import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from aacam import cli, core, github, installer, paths, registry


def make_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


class TempHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "cfg"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "XDG_STATE_HOME": str(self.home / "state"),
        }
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        os.environ.pop("WINEPREFIX", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class DetectTests(TempHome):
    def test_finds_lutris_and_proton_highest_first(self):
        (self.home / "Games/archeage-classic/drive_c/AAClassic/Documents/Addon").mkdir(parents=True)
        (self.home / ".local/share/Steam/steamapps/compatdata/123/pfx/drive_c/AAClassic2/Documents/Addon").mkdir(parents=True)
        found = paths.detect(self.home)
        self.assertEqual([i.root.name for i in found], ["AAClassic2", "AAClassic"])
        self.assertEqual(found[1].addon_dir.name, "Addon")

    def test_prefers_documents_addon_folder_used_by_game(self):
        # Layout on a real CachyOS install with the official AppImage launcher.
        (self.home / "Documents/AAClassic/Addon").mkdir(parents=True)
        (self.home / ".local/share/AAClassic/wine-staging-pfx/drive_c/AAClassic").mkdir(parents=True)
        found = paths.detect(self.home)
        self.assertEqual([i.addon_dir for i in found], [self.home / "Documents/AAClassic/Addon"])
        self.assertEqual(
            paths.resolve_user_path(self.home / "Documents").addon_dir,
            (self.home / "Documents/AAClassic/Addon").resolve(),
        )

    def test_wine_user_documents_in_prefix(self):
        docs = self.home / ".wine/drive_c/users/steamuser/Documents/AAClassic/Addon"
        docs.mkdir(parents=True)
        (docs / "addons.txt").write_text("")
        (self.home / ".wine/drive_c/AAClassic/Documents/Addon").mkdir(parents=True)
        self.assertEqual(paths.detect(self.home)[0].addon_dir, docs)

    def test_finds_official_launcher_and_heroic_prefixes(self):
        (self.home / ".local/share/AAClassic/wine-staging-pfx/drive_c/AAClassic").mkdir(parents=True)
        (self.home / ".local/share/AAClassic/steam-compatdata/pfx/drive_c/AAClassic").mkdir(parents=True)
        (self.home / "Games/Heroic/Prefixes/Archeage Classic/drive_c/Games/AAClassic").mkdir(parents=True)
        roots = sorted(str(i.root.relative_to(self.home)) for i in paths.detect(self.home))
        self.assertEqual(roots, [
            ".local/share/AAClassic/steam-compatdata/pfx/drive_c/AAClassic",
            ".local/share/AAClassic/wine-staging-pfx/drive_c/AAClassic",
            "Games/Heroic/Prefixes/Archeage Classic/drive_c/Games/AAClassic",
        ])

    def test_resolve_user_path_variants(self):
        root = self.home / ".wine/drive_c/AAClassic"
        (root / "Documents/Addon").mkdir(parents=True)
        for given in (root, root / "Documents", root / "Documents/Addon"):
            self.assertEqual(paths.resolve_user_path(given).addon_dir, (root / "Documents/Addon").resolve())

    def test_wiki_layout_used_when_present(self):
        root = self.home / ".wine/drive_c/AAClassic"
        (root / "Documents/AAClassic/addons").mkdir(parents=True)
        self.assertEqual(paths.install_from_root(root).addon_dir.name, "addons")


class InstallerTests(TempHome):
    def setUp(self):
        super().setUp()
        self.inst = paths.Install(self.home / "AAClassic", self.home / "AAClassic/Documents/Addon")

    def test_install_update_keeps_data_and_remove(self):
        z1 = make_zip(self.home / "v1.zip", {"owner-repo-abc/main.lua": "v1", "owner-repo-abc/ui.lua": "x"})
        installer.install_zip(self.inst, "Foo", z1)
        installer.enable(self.inst, "Foo")
        (self.inst.addon_dir / "Foo/.data").mkdir()
        (self.inst.addon_dir / "Foo/.data/settings.lua").write_text("keep")

        z2 = make_zip(self.home / "v2.zip", {"owner-repo-def/main.lua": "v2"})
        installer.install_zip(self.inst, "Foo", z2)
        foo = self.inst.addon_dir / "Foo"
        self.assertEqual((foo / "main.lua").read_text(), "v2")
        self.assertFalse((foo / "ui.lua").exists())
        self.assertEqual((foo / ".data/settings.lua").read_text(), "keep")
        self.assertEqual(installer.local_addons(self.inst), ["Foo"])
        self.assertEqual(sorted(p.name for p in self.inst.addon_dir.iterdir()), ["Foo", "addons.txt"])

        installer.remove(self.inst, "Foo")
        self.assertFalse(foo.exists())
        self.assertEqual(installer.read_enabled(self.inst), [])
        installer.install_zip(self.inst, "Foo", z2)
        self.assertTrue(installer.restore_kept_data(self.inst, "Foo"))
        self.assertEqual((foo / ".data/settings.lua").read_text(), "keep")

    def test_rejects_traversal_and_missing_main(self):
        bad = make_zip(self.home / "bad.zip", {"../evil/main.lua": "x"})
        with self.assertRaises(installer.InstallError):
            installer.install_zip(self.inst, "Bad", bad)
        empty = make_zip(self.home / "empty.zip", {"r/readme.md": "x"})
        with self.assertRaises(installer.InstallError):
            installer.install_zip(self.inst, "Empty", empty)
        self.assertEqual(installer.local_addons(self.inst), [])

    def test_addons_txt_crlf_and_bom(self):
        self.inst.addon_dir.mkdir(parents=True)
        self.inst.addons_txt.write_bytes(b"\xef\xbb\xbfA\r\nB\r\n\r\n")
        self.assertEqual(installer.read_enabled(self.inst), ["A", "B"])
        installer.disable(self.inst, "A")
        self.assertEqual(self.inst.addons_txt.read_text(), "B")
        self.assertTrue((self.inst.addon_dir / "addons.txt.bak").exists())


class RegistryTests(unittest.TestCase):
    def tarball(self, files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, body in files.items():
                data = body.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_parse_and_resolve_dependencies(self):
        blob = self.tarball({
            "addons-master/a/App.yaml": "name: App\nalias: App\ndescription: d\nauthor: x\nrepo: o/app\nbranch: main\ntags: [UI]\ndependencies: [Lib]\n",
            "addons-master/l/Lib.yaml": "name: Lib\nalias: Lib\ndescription: library\nauthor: x\nrepo: o/lib\nbranch: main\ntags: [Library]\n",
            "addons-master/b/Broken.yaml": "name: [unclosed\n",
            "addons-master/.github/workflow.yml": "on: push\n",
        })
        reg = registry.Registry(registry.parse_tarball(blob))
        self.assertEqual(sorted(reg.addons), ["App", "Lib"])
        self.assertEqual([a.name for a in reg.resolve("app")], ["Lib", "App"])
        self.assertEqual([a.name for a in reg.search("library")], ["Lib"])


class GitHubTests(unittest.TestCase):
    def test_release_zip_names_the_tag(self):
        with mock.patch.object(github, "get_json", return_value={"tag_name": "main", "zipball_url": "x"}):
            rel = github.latest_release("o/r", "main")
        self.assertEqual(rel.version, "main")
        self.assertEqual(rel.zip_url, "https://codeload.github.com/o/r/zip/refs/tags/main")


class CliTests(TempHome):
    def test_install_flow_with_fake_github(self):
        root = self.home / "Games/aac/drive_c/AAClassic"
        (root / "Documents/Addon").mkdir(parents=True)
        reg = registry.Registry({
            "Lib": registry.Addon("Lib", "Lib", "", "x", "o/lib", "main"),
            "App": registry.Addon("App", "App", "", "x", "o/app", "main", dependencies=["Lib"]),
        })

        def fake_download(url, dest):
            return make_zip(dest, {"r/main.lua": url})

        with mock.patch.object(registry.Registry, "load", return_value=reg), \
             mock.patch.object(github, "latest_release", side_effect=lambda repo, br: github.Release("1.0.0", repo)), \
             mock.patch.object(github, "download", side_effect=fake_download), \
             mock.patch.object(paths, "game_running", return_value=False):
            self.assertEqual(cli.main(["install", "App"]), 0)
            self.assertEqual(cli.main(["update", "--check"]), 0)
            self.assertEqual(cli.main(["disable", "Lib"]), 0)
            self.assertEqual(cli.main(["remove", "App"]), 0)

        addon_dir = root / "Documents/Addon"
        self.assertEqual(installer.read_enabled(paths.Install(root, addon_dir)), [])
        self.assertTrue((addon_dir / "Lib/main.lua").is_file())
        self.assertFalse((addon_dir / "App").exists())

    def test_install_from_repo_and_update(self):
        root = self.home / "Games/aac/drive_c/AAClassic"
        (root / "Documents/Addon").mkdir(parents=True)
        versions = iter(["v0.1.0", "v0.2.0"])
        seen = []

        def fake_release(repo, branch):
            seen.append((repo, branch))
            return github.Release(next(versions), repo)

        def fake_download(url, dest):
            return make_zip(dest, {"r/main.lua": url})

        empty = registry.Registry({})
        with mock.patch.object(registry.Registry, "load", return_value=empty), \
             mock.patch.object(github, "latest_release", side_effect=fake_release), \
             mock.patch.object(github, "download", side_effect=fake_download), \
             mock.patch.object(paths, "game_running", return_value=False):
            self.assertEqual(cli.main(["install", "--repo", "Z-arium/aac-dps-meter"]), 0)
            self.assertEqual(cli.main(["update"]), 0)
            self.assertEqual(cli.main(["install", "--repo", "not a repo"]), 1)
            self.assertEqual(cli.main(["install"]), 1)

        addon_dir = root / "Documents/Addon"
        self.assertTrue((addon_dir / "dps_meter/main.lua").is_file())
        self.assertIn("dps_meter", installer.read_enabled(paths.Install(root, addon_dir)))
        self.assertEqual(seen, [("Z-arium/aac-dps-meter", "main")] * 2)
        db = core.Manager(paths.Install(root, addon_dir)).db.addons["dps_meter"]
        self.assertEqual((db["version"], db["source"]), ("v0.2.0", "repo"))

    def test_repo_folder_name(self):
        self.assertEqual(core.repo_folder_name("Z-arium/aac-dps-meter"), "dps_meter")
        self.assertEqual(core.repo_folder_name("o/ArcheAge_Tools"), "Tools")
        self.assertEqual(core.repo_folder_name("o/nuzi-ui"), "nuzi_ui")

    def test_refuses_while_game_running(self):
        (self.home / ".wine/drive_c/AAClassic/Documents/Addon").mkdir(parents=True)
        with mock.patch.object(paths, "game_running", return_value=True):
            self.assertEqual(cli.main(["disable", "X"]), 1)


if __name__ == "__main__":
    unittest.main()
