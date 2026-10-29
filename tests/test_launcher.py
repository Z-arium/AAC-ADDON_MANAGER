import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aacam import cli, official, syscheck

from test_aacam import TempHome


def elf(path: Path, bits: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF" + struct.pack("B", 1 if bits == 32 else 2) + b"\0" * 11)


class SysCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def os_release(self, text: str) -> None:
        (self.root / "etc").mkdir(exist_ok=True)
        (self.root / "etc/os-release").write_text(text)

    def test_distro_families(self):
        cases = {
            'ID=cachyos\nID_LIKE=arch\nPRETTY_NAME="CachyOS Linux"': "arch",
            'ID=steamos\nID_LIKE=arch': "steamos",
            'ID=linuxmint\nID_LIKE="ubuntu debian"': "debian",
            'ID=bazzite\nID_LIKE="fedora"': "fedora",
            'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"': "suse",
            'ID=nixos': "unknown",
        }
        for text, family in cases.items():
            self.os_release(text)
            self.assertEqual(syscheck.distro(self.root).family, family, text)
        self.os_release('ID=cachyos\nID_LIKE=arch\nPRETTY_NAME="CachyOS Linux"')
        self.assertEqual(syscheck.distro(self.root).name, "CachyOS Linux")

    def test_lib_bitness_per_layout(self):
        # Fedora keeps 32-bit libraries in /usr/lib and 64-bit in /usr/lib64.
        elf(self.root / "usr/lib/libvulkan.so.1", 32)
        self.assertTrue(syscheck.has_lib("libvulkan.so.1", 32, self.root))
        self.assertFalse(syscheck.has_lib("libvulkan.so.1", 64, self.root))
        elf(self.root / "usr/lib64/libvulkan.so.1", 64)
        self.assertTrue(syscheck.has_lib("libvulkan.so.1", 64, self.root))

    def test_missing_32bit_vulkan_on_arch_with_nvidia(self):
        self.os_release("ID=cachyos\nID_LIKE=arch")
        elf(self.root / "usr/lib/libvulkan.so.1", 64)
        elf(self.root / "usr/lib/libfuse.so.2", 64)
        elf(self.root / "usr/lib32/libGL.so.1", 32)
        (self.root / "usr/share/vulkan/icd.d").mkdir(parents=True)
        (self.root / "usr/share/vulkan/icd.d/nvidia_icd.json").write_text("{}")
        (self.root / "proc/driver/nvidia").mkdir(parents=True)
        checks = {c.key: c for c in syscheck.run(self.root)}
        self.assertEqual([k for k, c in checks.items() if c.status != syscheck.OK], ["vulkan32"])
        self.assertEqual(checks["vulkan32"].status, syscheck.FAIL)
        self.assertIn("lib32-vulkan-icd-loader", checks["vulkan32"].fix)
        self.assertIn("lib32-nvidia-utils", checks["vulkan32"].fix)
        self.assertEqual(syscheck.fix_script(list(checks.values())), checks["vulkan32"].fix)

    def test_steamos_gives_no_package_commands(self):
        self.os_release("ID=steamos\nID_LIKE=arch")
        checks = syscheck.run(self.root)
        self.assertTrue(all(c.fix is None for c in checks))
        self.assertEqual(syscheck.fix_script(checks), "")


class OfficialLauncherTests(TempHome):
    def test_find_prefers_default_folder_and_skips_us(self):
        (self.home / "Downloads").mkdir()
        (self.home / "Downloads/launcher2.2.0.AppImage").write_bytes(b"x")
        (self.home / "Downloads/ArcheAge-Classic-Linux-Launcher-x86_64.AppImage").write_bytes(b"x")
        self.assertEqual(official.find(self.home).name, "launcher2.2.0.AppImage")
        official.default_dir(self.home).mkdir(parents=True)
        (official.default_dir(self.home) / "ArcheAgeClassic.AppImage").write_bytes(b"x")
        self.assertEqual(official.find(self.home), official.default_dir(self.home) / "ArcheAgeClassic.AppImage")

    def test_only_our_appimage_is_not_found(self):
        (self.home / "Applications").mkdir()
        (self.home / "Applications/ArcheAge-Classic-Linux-Launcher-x86_64.AppImage").write_bytes(b"x")
        self.assertIsNone(official.find(self.home))

    def test_environment_without_fuse_and_frozen_paths(self):
        base = {"PATH": "/usr/bin", "APPIMAGE": "/x/us.AppImage", "APPDIR": "/tmp/.mount",
                "LD_LIBRARY_PATH": "/tmp/_MEI", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
        env = official.environment(fuse_ok=False, base=base)
        self.assertEqual(env["APPIMAGE_EXTRACT_AND_RUN"], "1")
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertNotIn("APPIMAGE", env)
        self.assertNotIn("APPIMAGE_EXTRACT_AND_RUN", official.environment(fuse_ok=True, base={}))

    def test_game_watch_states(self):
        now = [0.0]
        running = [False]
        proc = mock.Mock(returncode=None)
        proc.poll.return_value = None
        w = official.GameWatch(proc, clock=lambda: now[0], running=lambda: running[0])
        self.assertIsNone(w.poll())
        running[0] = True
        self.assertEqual(w.poll(), "game")
        now[0], running[0] = 30.0, False
        self.assertEqual(w.poll(), "crashed")

        w = official.GameWatch(proc, clock=lambda: now[0], running=lambda: running[0])
        running[0] = True
        w.poll()
        now[0], running[0] = 4000.0, False
        self.assertEqual(w.poll(), "closed")

        failed = mock.Mock(returncode=1)
        failed.poll.return_value = 1
        w = official.GameWatch(failed, running=lambda: False)
        self.assertEqual(w.poll(), "launcher_failed")

    def test_launch_starts_official_launcher(self):
        launcher = official.default_dir(self.home) / "ArcheAgeClassic.AppImage"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\necho hello\n")
        with mock.patch("subprocess.Popen") as popen:
            self.assertEqual(cli.main(["launch"]), 0)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, [str(launcher)])
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue(official.log_path().is_file())

    def test_launch_without_launcher_explains(self):
        self.assertEqual(cli.main(["launch"]), 1)

    def test_doctor_runs(self):
        self.assertEqual(cli.main(["doctor"]), 0)
        self.assertEqual(cli.main(["report"]), 0)


if __name__ == "__main__":
    unittest.main()
