import io
import json
import os
import time
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from aacam import cli, crash, official, report
from aacam.config import Settings

from test_aacam import TempHome

GiB = crash.GiB


def fake_proc(root: Path, pid: int, argv: list[str], env: dict[str, str], vmpeak_kib: int) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")
    (d / "status").write_text(f"Name:\tarcheage.exe\nVmPeak:\t{vmpeak_kib} kB\nVmRSS:\t{vmpeak_kib // 2} kB\nThreads:\t42\n")
    (d / "exe").symlink_to("/opt/proton/files/bin/wine-preloader")
    (d / "cwd").symlink_to(root / "game/bin32", target_is_directory=True)


class LaunchEnvTests(TempHome):
    def test_marker_only_by_default(self):
        self.assertEqual(crash.launch_env(Settings()), {crash.MARKER: "1"})

    def test_fixes_and_diagnostics(self):
        s = Settings()
        crash.set_fix(s, "large_address", True)
        crash.set_fix(s, "nvidia_gpu", True)
        crash.set_fix(s, "nvidia_gpu", False)
        s.set("diagnostics", True)
        env = crash.launch_env(s)
        self.assertEqual(env["WINE_LARGE_ADDRESS_AWARE"], "1")
        self.assertNotIn("__NV_PRIME_RENDER_OFFLOAD", env)
        self.assertEqual(env["DXVK_LOG_PATH"], str(crash.log_dir()))
        self.assertTrue(crash.log_dir().is_dir())
        self.assertEqual(Settings().get("fixes"), ["large_address"])

    def test_unknown_fix(self):
        with self.assertRaises(KeyError):
            crash.set_fix(Settings(), "nope", True)

    def test_env_reaches_official_launcher(self):
        env = official.environment(True, base={"PATH": "/bin"}, extra={"WINE_LARGE_ADDRESS_AWARE": "1"})
        self.assertEqual(env["WINE_LARGE_ADDRESS_AWARE"], "1")


class SamplerTests(TempHome):
    def test_snapshot_and_peaks(self):
        proc = self.home / "proc"
        (self.home / "proc/game/bin32").mkdir(parents=True)
        fake_proc(proc, 1234, ["C:\\AAClassic\\bin32\\archeage.exe", "-x"],
                  {"WINEPREFIX": "/pfx", "HOME": "/home/x", crash.MARKER: "1"}, 3 * GiB)
        snap = crash.snapshot(1234, proc)
        self.assertEqual(snap["VmPeak"], 3 * GiB)
        self.assertEqual(snap["Threads"], 42)
        self.assertEqual(snap["env"], {"WINEPREFIX": "/pfx", crash.MARKER: "1"})
        self.assertTrue(snap["exe"].endswith("wine-preloader"))

        s = crash.Sampler()
        with mock.patch.object(crash, "snapshot", side_effect=[snap, dict(snap, VmPeak=GiB)]):
            s.sample(pids=lambda: [1234])
            s.sample(pids=lambda: [1234])
        self.assertEqual(s.peak["VmPeak"], 3 * GiB)
        self.assertEqual(s.game["env"]["WINEPREFIX"], "/pfx")

    def test_watch_feeds_sampler(self):
        sampler = mock.Mock()
        running = [True]
        w = official.GameWatch(None, running=lambda: running[0], sampler=sampler)
        self.assertEqual(w.poll(), "game")
        self.assertIsNone(w.poll())
        self.assertEqual(sampler.sample.call_count, 2)
        sampler.sample.side_effect = RuntimeError
        self.assertIsNone(w.poll())  # a broken sampler never breaks the watch


class AnalyzeTests(TempHome):
    def keys(self, texts, sampler=None, gpus=(), played=None, settings=None):
        found = crash.analyze(texts, sampler, settings or Settings(), list(gpus), played)
        return {f.key: f for f in found}

    def test_signatures(self):
        f = self.keys({"official-launcher.log": "wine: Unhandled page fault on read access to 00000000 at address 1234"})
        self.assertIn("Unhandled page fault", f["unhandled"].detail)
        f = self.keys({"logs/archeage_d3d11.log": "err: DxvkDevice: VK_ERROR_DEVICE_LOST"})
        self.assertEqual(f["device_lost"].fix, "wined3d")
        f = self.keys({"official-launcher.log": "E_OUTOFMEMORY"})
        self.assertEqual(f["address_space"].fix, "large_address")
        f = self.keys({"journal.txt": "kernel: Out of memory: Killed process 4242 (archeage.exe) total-vm:4000000kB"})
        self.assertIn("oom_killed", f)
        f = self.keys({"journal.txt": "kernel: NVRM: Xid (PCI:0000:01:00): 13, pid=4242, name=archeage.exe"})
        self.assertIn("gpu_fault", f)

    def test_kernel_lines_for_other_programs_do_not_count(self):
        f = self.keys({"journal.txt": "Out of memory: Killed process 99 (firefox)"})
        self.assertNotIn("oom_killed", f)
        self.assertNotIn("address_space", f)

    def test_memory_peak(self):
        s = crash.Sampler(peak={"VmPeak": int(3.8 * GiB)})
        self.assertEqual(self.keys({}, s)["address_space"].fix, "large_address")
        s = crash.Sampler(peak={"VmPeak": 2 * GiB})
        self.assertIn("address_space_2g", self.keys({}, s))

    def test_hybrid_gpu_and_env_blocked(self):
        s = crash.Sampler(game={"pid": 1, "env": {}})
        f = self.keys({}, s, gpus=["Intel Corporation Raptor Lake-S UHD Graphics", "NVIDIA Corporation AD104 [GeForce RTX 4070]"])
        self.assertEqual(f["hybrid_gpu"].fix, "nvidia_gpu")
        self.assertFalse(f["hybrid_gpu"].crash)
        self.assertIn("env_blocked", f)
        self.assertFalse(crash.crashed(list(f.values())))

    def test_quick_exit_only_without_other_cause(self):
        self.assertIn("quick_exit", self.keys({}, played=20))
        f = self.keys({"official-launcher.log": "Unhandled exception: page fault"}, played=20)
        self.assertNotIn("quick_exit", f)
        self.assertNotIn("quick_exit", self.keys({}, played=3600))


class CollectTests(TempHome):
    def test_zip_has_evidence(self):
        started = time.time() - 60
        docs = self.home / "Documents/AAClassic"
        docs.mkdir(parents=True)
        (docs / "Game.log").write_text("loading\nUnhandled exception: page fault\n")
        (docs / "Addon").mkdir()
        (docs / "Addon/old.log").write_text("ignored")
        (self.home / "steam-12345.log").write_text("proton log")
        (self.home / "notes.txt").write_text("not ours")
        official.log_path().parent.mkdir(parents=True, exist_ok=True)
        official.log_path().write_text("# starting\n")
        sampler = crash.Sampler(started=started, game={"pid": 1, "env": {crash.MARKER: "1"}}, peak={"VmPeak": GiB})
        with mock.patch.object(crash, "journal", return_value="Kernel:\n(nothing relevant)\n"), \
             mock.patch.object(report, "_gpus", return_value=["NVIDIA RTX 4070"]), \
             mock.patch.object(report, "build", return_value="report"):
            path, findings = crash.collect(Settings(), sampler, played=600)
        self.assertTrue(crash.crashed(findings))
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            self.assertTrue({"summary.txt", "details.json", "report.txt", "journal.txt",
                             "official-launcher.log", "logs/Game.log", "logs/steam-12345.log"} <= names)
            self.assertNotIn("logs/notes.txt", names)
            self.assertNotIn("logs/old.log", names)
            details = json.loads(z.read("details.json"))
        self.assertEqual(details["played_seconds"], 600)
        self.assertEqual(details["findings"][0]["key"], "unhandled")

    def test_prune_keeps_ten(self):
        d = crash.reports_dir()
        d.mkdir(parents=True)
        for i in range(12):
            (d / f"crash-2026010{i:02d}.zip").write_text("x")
        crash._prune(d, keep=10)
        self.assertEqual(len(list(d.glob("*.zip"))), 10)


class CliTests(TempHome):
    def test_fixes_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["fixes", "--on", "large_address", "--diagnostics", "on"]), 0)
        self.assertIn("[on ] large_address", out.getvalue())
        self.assertTrue(Settings().get("diagnostics"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["fixes", "--on", "bogus"]), 1)
