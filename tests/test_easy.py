"""v0.5: self-update of the AppImage, and running inside Flatpak."""

import hashlib
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from aacam import cli, crash, host, official, paths, selfupdate, syscheck
from test_aacam import TempHome

NEW = b"new appimage contents"


def release(version="v9.0.0", sums=True, bundle=False, digest=None):
    assets = {selfupdate.ASSET: "https://example/app"}
    if sums:
        assets[selfupdate.SUMS] = "https://example/sums"
    if bundle:
        assets[selfupdate.BUNDLE] = "https://example/bundle"
    return selfupdate.Update(version, "notes", "https://example/release", assets), digest


class SelfUpdateTests(TempHome):
    def setUp(self):
        super().setUp()
        self.app = self.home / "Applications/launcher.AppImage"
        self.app.parent.mkdir()
        self.app.write_bytes(b"old")
        self.app.chmod(0o755)
        self.digest = hashlib.sha256(NEW).hexdigest()

    def fake_download(self, sums_text=None, app=NEW):
        def download(url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            if url.endswith("sums"):
                dest.write_text(sums_text if sums_text is not None else f"{self.digest}  {selfupdate.ASSET}\n")
            elif url.endswith("bundle"):
                dest.write_text("{}")
            else:
                dest.write_bytes(app)
            return dest
        return mock.patch.object(selfupdate.github, "download", side_effect=download)

    def test_newer(self):
        self.assertTrue(selfupdate.newer("v0.5.0", "0.4.0"))
        self.assertTrue(selfupdate.newer("v0.10.0", "0.9.1"))
        self.assertFalse(selfupdate.newer("v0.4.0", "0.4.0"))
        self.assertFalse(selfupdate.newer("v0.3.9", "0.4.0"))

    def test_parse_sums(self):
        sums = selfupdate.parse_sums(f"{'a' * 64}  one.AppImage\n{'B' * 64} *two.zsync\nnonsense\n")
        self.assertEqual(sums, {"one.AppImage": "a" * 64, "two.zsync": "b" * 64})

    def test_install_replaces_and_keeps_old(self):
        update, _ = release()
        with self.fake_download():
            selfupdate.install(update, self.app)
        self.assertEqual(self.app.read_bytes(), NEW)
        self.assertTrue(os.access(self.app, os.X_OK))
        self.assertEqual((self.app.parent / "launcher.AppImage.old").read_bytes(), b"old")
        self.assertEqual([p.name for p in self.app.parent.iterdir() if p.name.endswith(".part")], [])

    def test_wrong_checksum_leaves_appimage_alone(self):
        update, _ = release()
        with self.fake_download(app=b"tampered"), self.assertRaisesRegex(selfupdate.UpdateError, "doesn't match"):
            selfupdate.install(update, self.app)
        self.assertEqual(self.app.read_bytes(), b"old")
        self.assertEqual(sorted(p.name for p in self.app.parent.iterdir()), ["launcher.AppImage"])

    def test_no_checksums_no_update(self):
        update, _ = release(sums=False)
        with self.assertRaisesRegex(selfupdate.UpdateError, "SHA256SUMS"):
            selfupdate.install(update, self.app)
        update, _ = release()
        with self.fake_download(sums_text="nothing here\n"), self.assertRaisesRegex(selfupdate.UpdateError, "doesn't list"):
            selfupdate.install(update, self.app)
        self.assertEqual(self.app.read_bytes(), b"old")

    def test_bad_signature_stops_update(self):
        update, _ = release(bundle=True)
        with self.fake_download(), mock.patch.object(selfupdate, "verify_signature", return_value=False), \
                self.assertRaisesRegex(selfupdate.UpdateError, "signature"):
            selfupdate.install(update, self.app)
        self.assertEqual(self.app.read_bytes(), b"old")
        with self.fake_download(), mock.patch.object(selfupdate, "verify_signature", return_value=None):
            selfupdate.install(update, self.app)  # no cosign installed: the checksum still has to match
        self.assertEqual(self.app.read_bytes(), NEW)

    def test_not_an_appimage(self):
        update, _ = release()
        with mock.patch.dict(os.environ, {"APPIMAGE": ""}), self.assertRaisesRegex(selfupdate.UpdateError, "AppImage"):
            selfupdate.install(update)

    def test_kind(self):
        with mock.patch.object(host, "FLATPAK", False):
            with mock.patch.dict(os.environ, {"APPIMAGE": str(self.app)}):
                self.assertEqual(selfupdate.kind(), "appimage")
            with mock.patch.dict(os.environ, {"APPIMAGE": ""}):
                with mock.patch.object(selfupdate.sys, "executable",
                                       str(selfupdate.AUR_DIR / "usr/bin/aac-linux-launcher")):
                    self.assertEqual(selfupdate.kind(), "aur")
                self.assertEqual(selfupdate.kind(), "python")
        with mock.patch.object(host, "FLATPAK", True):
            self.assertEqual(selfupdate.kind(), "flatpak")
            self.assertIn("flatpak update", selfupdate.how_to_update())

    def test_cli_check_and_update(self):
        update, _ = release()
        out = io.StringIO()
        with mock.patch.object(selfupdate, "check", return_value=update), mock.patch.object(host, "FLATPAK", False), \
                mock.patch.dict(os.environ, {"APPIMAGE": str(self.app)}), self.fake_download(), redirect_stdout(out):
            self.assertEqual(cli.main(["self-update", "--check"]), 0)
            self.assertEqual(self.app.read_bytes(), b"old")
            self.assertEqual(cli.main(["self-update"]), 0)
        self.assertEqual(self.app.read_bytes(), NEW)
        self.assertIn("v9.0.0 is out", out.getvalue())
        with mock.patch.object(selfupdate, "check", return_value=None), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["self-update"]), 0)
        self.assertIn("newest version", out.getvalue())


class FlatpakTests(TempHome):
    def test_outside_flatpak_nothing_changes(self):
        with mock.patch.object(host, "FLATPAK", False):
            self.assertEqual(host.argv(["wine"], {"A": "1"}), ["wine"])
            self.assertEqual(host.proc(), Path("/proc"))
            self.assertEqual(host.root(), Path("/"))

    def test_commands_run_on_the_host(self):
        with mock.patch.object(host, "FLATPAK", True):
            self.assertEqual(host.argv(["sh", "-c", "x"], {"AAC_WINE": "/w", "PROTON_NO_ESYNC": None}, "/game"),
                             ["flatpak-spawn", "--host", "--directory=/game", "--env=AAC_WINE=/w",
                              "--unset-env=PROTON_NO_ESYNC", "sh", "-c", "x"])

    def test_official_launcher_gets_only_our_variables_on_the_host(self):
        app = self.home / "launcher.AppImage"
        app.write_text("#!/bin/sh\n")
        app.chmod(0o755)
        with mock.patch.object(host, "FLATPAK", True), mock.patch.object(host.subprocess, "Popen") as popen:
            official.start(app, fuse_ok=False, extra_env={"AAC_WINE": "/w", "PATH": "/app/bin", "X": None})
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:3], ["flatpak-spawn", "--host", f"--directory={self.home}"])
        self.assertIn("--env=AAC_WINE=/w", cmd)
        self.assertIn("--env=APPIMAGE_EXTRACT_AND_RUN=1", cmd)
        self.assertIn("--unset-env=X", cmd)
        self.assertFalse(any(a.startswith("--env=PATH") for a in cmd))
        self.assertNotIn("env", popen.call_args.kwargs)

    def test_host_proc_copy_is_read_like_proc(self):
        """The shell that copies the host's /proc, run here against this machine's /proc."""
        fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "bin32/archeage.exe"])
        try:
            with mock.patch.object(host, "FLATPAK", True), \
                    mock.patch.object(host, "argv", side_effect=lambda c, *a, **k: c), \
                    mock.patch.object(host, "_mirror", None):
                self.assertEqual(paths.game_pids(), [fake.pid])
                mirror = host.proc()
                self.assertTrue(str(mirror).startswith(str(self.home / "cache")))
                snap = crash.snapshot(fake.pid)
                self.assertEqual(snap["cmdline"][-1], "bin32/archeage.exe")
                self.assertEqual(snap["exe"], os.path.realpath(sys.executable))
                self.assertGreater(snap["VmSize"], 0)
                self.assertGreater(snap.get("Space32", 0), 0)
                self.assertIsNotNone(crash.mem_available_kib())
                self.assertEqual(oct(mirror.stat().st_mode & 0o777), "0o700")
        finally:
            fake.kill()
            fake.wait()

    def test_system_check_reads_host_root(self):
        root = self.home / "run-host"
        (root / "etc").mkdir(parents=True)
        (root / "usr").mkdir()
        (root / "etc/os-release").write_text('ID=cachyos\nID_LIKE=arch\nNAME="CachyOS Linux"\n')
        with mock.patch.object(host, "FLATPAK", True), mock.patch.object(host, "HOST_ROOT", root):
            self.assertEqual(syscheck.distro().family, "arch")
