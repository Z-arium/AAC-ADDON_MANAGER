"""v0.3: clear errors, cached GitHub lookups, a token setting, tracking hand-installed addons, the log."""

import email.message
import io
import json
import time
import urllib.error
from pathlib import Path
from unittest import mock

from aacam import cli, config, core, github, installer, logs, paths, registry, report
from test_aacam import TempHome, make_zip


def headers(**values) -> email.message.Message:
    msg = email.message.Message()
    for k, v in values.items():
        msg[k.replace("_", "-")] = v
    return msg


class FakeResponse(io.BytesIO):
    def __init__(self, body: object, etag: str = '"e1"') -> None:
        super().__init__(json.dumps(body).encode())
        self.status = 200
        self.headers = headers(ETag=etag, X_RateLimit_Remaining="59")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, **hdrs) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", headers(**hdrs), io.BytesIO(b""))


class CacheTests(TempHome):
    URL = f"{github.API}/repos/o/r/releases/latest"

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reuses_fresh_answer_without_asking(self):
        with mock.patch("aacam.net.urlopen", return_value=FakeResponse({"tag_name": "v1"})) as get:
            github.get_json(self.URL)
            github.get_json(self.URL)
        self.assertEqual(get.call_count, 1)

    def test_revalidates_with_etag_and_uses_cache_on_304(self):
        with mock.patch("aacam.net.urlopen", return_value=FakeResponse({"tag_name": "v1"})):
            github.get_json(self.URL)
        sent = {}

        def not_modified(req, timeout):
            sent.update(req.headers)
            raise http_error(304)

        with mock.patch("aacam.net.urlopen", side_effect=not_modified):
            self.assertEqual(github.get_json(self.URL, fresh=0), {"tag_name": "v1"})
        self.assertEqual(sent.get("If-none-match"), '"e1"')

    def test_rate_limit_message_names_the_token_and_reset_time(self):
        reset = int(time.time()) + 600
        err = http_error(403, X_RateLimit_Remaining="0", X_RateLimit_Reset=str(reset))
        with mock.patch("aacam.net.urlopen", side_effect=err):
            with self.assertRaises(github.RateLimited) as ctx:
                github.get_json(self.URL)
        self.assertIn(github.TOKEN_HINT, str(ctx.exception))
        self.assertIn(time.strftime("%H:%M", time.localtime(reset)), str(ctx.exception))

    def test_rate_limited_or_offline_falls_back_to_an_older_answer(self):
        with mock.patch("aacam.net.urlopen", return_value=FakeResponse({"tag_name": "v1"})):
            github.get_json(self.URL)
        for err in (http_error(403, X_RateLimit_Remaining="0"), urllib.error.URLError(OSError("down"))):
            with mock.patch("aacam.net.urlopen", side_effect=err):
                self.assertEqual(github.get_json(self.URL, fresh=0), {"tag_name": "v1"})

    def test_offline_message_is_plain(self):
        import socket
        err = urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
        with mock.patch("aacam.net.urlopen", side_effect=err):
            with self.assertRaises(github.Offline) as ctx:
                github.get_json(self.URL)
        self.assertIn("no internet connection", str(ctx.exception))

    def test_one_broken_repo_does_not_stop_the_update_check(self):
        root = self.home / "Documents/AAClassic"
        inst = paths.install_from_root(root)
        for name in ("Good", "Gone"):
            (inst.addon_dir / name).mkdir(parents=True)
            (inst.addon_dir / name / "main.lua").write_text("x")
        mgr = core.Manager(inst)
        reg = registry.Registry({n: registry.Addon(n, n, "", "o", f"o/{n}", "main") for n in ("Good", "Gone")})
        for name, addon in reg.addons.items():
            mgr.track(name, addon)

        def release(repo, branch):
            if repo == "o/Gone":
                raise github.GitHubError("https://github.com/o/Gone has no releases and no main branch")
            return github.Release("v2", "u")

        with mock.patch.object(github, "latest_release", side_effect=release):
            updates = mgr.check_updates(reg)
        self.assertEqual(list(updates), ["Good"])
        self.assertIn("no releases", mgr.check_errors["Gone"])


class TokenTests(TempHome):
    def test_token_from_settings_is_sent_to_the_api_only(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            config.Settings().set("github_token", "ghp_saved")
            api = github._request(f"{github.API}/rate_limit")
            other = github._request("https://codeload.github.com/o/r/zip/x")
        self.assertEqual(api.get_header("Authorization"), "Bearer ghp_saved")
        self.assertIsNone(other.get_header("Authorization"))

    def test_cli_saves_and_clears_the_token(self):
        rate = {"limit": 5000, "remaining": 4999, "reset": 0}
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}), \
             mock.patch.object(github, "rate_limit", return_value=rate):
            self.assertEqual(cli.main(["token", "--set", "ghp_abc"]), 0)
            self.assertEqual(config.Settings().get("github_token"), "ghp_abc")
            self.assertEqual(cli.main(["token", "--clear"]), 0)
        self.assertIsNone(config.Settings().get("github_token"))

    def test_settings_file_is_private(self):
        config.Settings().set("github_token", "ghp_x")
        self.assertEqual((config.config_dir() / "config.json").stat().st_mode & 0o077, 0)


class TrackTests(TempHome):
    def setUp(self):
        super().setUp()
        self.inst = paths.install_from_root(self.home / "Documents/AAClassic")
        for name in ("dps_meter", "Mine", "Nice UI"):
            (self.inst.addon_dir / name).mkdir(parents=True)
            (self.inst.addon_dir / name / "main.lua").write_text("x")
            (self.inst.addon_dir / name / ".data").mkdir()
        installer.write_enabled(self.inst, ["dps_meter", "Nice UI"])
        self.reg = registry.Registry({
            "dps_meter": registry.Addon("dps_meter", "DPS Meter", "", "Z", "Z-arium/aac-dps-meter", "main"),
            "nice_ui": registry.Addon("nice_ui", "Nice UI", "", "x", "x/nice-ui", "main"),
        })

    def test_finds_by_folder_name_and_display_name(self):
        loose = core.Manager(self.inst).untracked(self.reg)
        self.assertEqual(sorted(loose), ["Nice UI", "dps_meter"])

    def test_tracked_addon_updates_in_its_own_folder_and_keeps_data(self):
        mgr = core.Manager(self.inst)
        mgr.track("Nice UI", self.reg.addons["nice_ui"])
        (self.inst.addon_dir / "Nice UI/.data/s.lua").write_text("keep")
        with mock.patch.object(github, "latest_release", return_value=github.Release("v3", "u")), \
             mock.patch.object(github, "download", side_effect=lambda url, dest: make_zip(dest, {"r/main.lua": "new"})):
            updates = mgr.check_updates(self.reg)
            self.assertEqual(updates["Nice UI"].version, "v3")
            mgr.install_one(mgr.addon_for(self.reg, "Nice UI"), updates["Nice UI"])
        self.assertEqual((self.inst.addon_dir / "Nice UI/main.lua").read_text(), "new")
        self.assertEqual((self.inst.addon_dir / "Nice UI/.data/s.lua").read_text(), "keep")
        self.assertFalse((self.inst.addon_dir / "nice_ui").exists())
        self.assertEqual(installer.read_enabled(self.inst), ["dps_meter", "Nice UI"])
        self.assertEqual(core.Manager(self.inst).untracked(self.reg).keys(), {"dps_meter"})

    def test_cli_track_all(self):
        config.Settings().set("addon_dir", str(self.inst.addon_dir))
        with mock.patch.object(registry.Registry, "load", return_value=self.reg), \
             mock.patch.object(paths, "game_running", return_value=False):
            self.assertEqual(cli.main(["track"]), 0)
            self.assertEqual(cli.main(["track", "Mine"]), 1)
            self.assertEqual(cli.main(["track", "--all"]), 0)
        self.assertEqual(sorted(core.Manager(self.inst).db.addons), ["Nice UI", "dps_meter"])


class ErrorMessageTests(TempHome):
    def setUp(self):
        super().setUp()
        self.inst = paths.Install(self.home, self.home / "Addon")

    def test_broken_zip_names_the_addon(self):
        bad = self.home / "x.zip"
        bad.write_bytes(b"PK\x03\x04 not really")
        with self.assertRaisesRegex(installer.InstallError, "zip for Foo is broken"):
            installer.install_zip(self.inst, "Foo", bad)

    def test_zip_without_main_lua_says_it_is_not_an_addon(self):
        z = make_zip(self.home / "y.zip", {"r/readme.md": "x"})
        with self.assertRaisesRegex(installer.InstallError, "isn't an ArcheAge addon"):
            installer.install_zip(self.inst, "Foo", z)

    def test_cli_turns_unexpected_errors_into_one_line_and_logs_them(self):
        with mock.patch.object(cli, "cmd_list", side_effect=RuntimeError("boom")), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(cli.main(["list"]), 1)
        self.assertIn("unexpected failure", err.getvalue())
        self.assertIn("RuntimeError: boom", logs.path().read_text())


class LogTests(TempHome):
    def tearDown(self):
        import logging
        root = logging.getLogger("aacam")
        for h in [h for h in root.handlers if getattr(h, "_aacam", False)]:
            root.removeHandler(h)
            h.close()
        super().tearDown()

    def test_log_hides_tokens_and_report_includes_it(self):
        import logging
        logs.setup("test")
        logging.getLogger("aacam.test").warning("token ghp_%s and Bearer abc", "A" * 36)
        text = logs.path().read_text()
        self.assertNotIn("ghp_AAAA", text)
        self.assertNotIn("Bearer abc", text)
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            config.Settings().set("github_token", "ghp_" + "B" * 36)
            out = report.build(config.Settings())
        self.assertIn("aacam.log", out)
        self.assertIn("GitHub token: set", out)
        self.assertNotIn("B" * 36, out)


class MoreDetectTests(TempHome):
    """Prefix layouts beyond the ones in test_aacam: Bottles, Flatpak Steam, $WINEPREFIX, XDG Documents."""

    def make(self, prefix: Path, user: str = "steamuser") -> Path:
        addon = prefix / f"drive_c/users/{user}/Documents/AAClassic/Addon"
        addon.mkdir(parents=True)
        return addon

    def test_bottles_native_and_flatpak(self):
        a = self.make(self.home / ".local/share/bottles/bottles/AAC")
        b = self.make(self.home / ".var/app/com.usebottles.bottles/data/bottles/bottles/AAC2")
        found = {i.addon_dir for i in paths.detect(self.home)}
        self.assertTrue({a, b} <= found)

    def test_flatpak_steam_proton(self):
        a = self.make(self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/123/pfx")
        self.assertIn(a, {i.addon_dir for i in paths.detect(self.home)})

    def test_wineprefix_variable_comes_first(self):
        a = self.make(self.home / "custom-pfx", user="jacob")
        self.make(self.home / ".wine", user="jacob")
        with mock.patch.dict("os.environ", {"WINEPREFIX": str(self.home / "custom-pfx")}):
            self.assertEqual(paths.detect(self.home)[0].addon_dir, a)

    def test_xdg_documents_folder(self):
        docs = self.home / "Dokumente"
        (docs / "AAClassic/Addon").mkdir(parents=True)
        (self.home / ".config").mkdir()
        (self.home / ".config/user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n')
        self.assertEqual(paths.detect(self.home)[0].addon_dir, docs / "AAClassic/Addon")

    def test_nothing_found(self):
        self.assertEqual(paths.detect(self.home), [])
