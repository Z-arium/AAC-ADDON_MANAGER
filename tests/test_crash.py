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
        self.assertEqual(crash.launch_env(Settings(), self.home), {crash.MARKER: "1", "WINEDEBUG": "fixme-all"})

    def test_fixes_and_diagnostics(self):
        s = Settings()
        crash.set_fix(s, "no_fsync", True)
        crash.set_fix(s, "nvidia_gpu", True)
        crash.set_fix(s, "nvidia_gpu", False)
        with self.assertRaises(PermissionError):  # not cleared with the ArcheAge Classic team yet
            crash.set_fix(s, "dxvk_memory", True)
        s.set("fixes", s.get("fixes") + ["dxvk_memory"])  # even if saved by hand, it isn't applied
        s.set("diagnostics", True)
        env = crash.launch_env(s)
        self.assertEqual(env["PROTON_NO_FSYNC"], "1")
        self.assertNotIn("DXVK_CONFIG", env)
        self.assertNotIn("DXVK_FILTER_DEVICE_NAME", env)
        self.assertEqual(env["DXVK_LOG_PATH"], str(crash.log_dir()))
        self.assertTrue(crash.log_dir().is_dir())
        self.assertEqual(Settings().get("fixes"), ["no_fsync", "dxvk_memory"])

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
        self.assertIn("texture quality", f["address_space"].detail)
        # What ArcheAge Classic actually logs when its 32-bit address space runs out.
        for line in ("<11:29:36> [392AF52E][Error] *** Memory allocation for 2097172 bytes failed ****",
                     "033c:err:virtual:allocate_virtual_memory out of memory for allocation, base (nil) size 00160000",
                     "err:   D3D9: InitTexture: map failed"):
            self.assertIn("address_space", self.keys({"logs/ArcheAge.log": line}))
        f = self.keys({"journal.txt": "kernel: Out of memory: Killed process 4242 (archeage.exe) total-vm:4000000kB"})
        self.assertIn("oom_killed", f)
        f = self.keys({"journal.txt": "kernel: NVRM: Xid (PCI:0000:01:00): 13, pid=4242, name=archeage.exe"})
        self.assertIn("gpu_fault", f)

    def test_kernel_lines_for_other_programs_do_not_count(self):
        f = self.keys({"journal.txt": "Out of memory: Killed process 99 (firefox)"})
        self.assertNotIn("oom_killed", f)
        self.assertNotIn("address_space", f)

    def test_memory_peak(self):
        s = crash.Sampler(peak={"VmPeak": 3_920_100})  # a session that crashed
        f = self.keys({}, s)["address_space_close"]
        self.assertFalse(f.crash)  # a hint; a clean close near the limit is not a crash
        self.assertNotIn("address_space_close", self.keys({}, crash.Sampler(peak={"VmPeak": 3_660_000})))
        self.assertNotIn("address_space_close", self.keys({}, crash.Sampler(peak={"VmPeak": 9 * GiB})))  # WoW64
        self.assertNotIn("address_space", self.keys({}, crash.Sampler(peak={"VmPeak": 2 * GiB})))

    def test_hybrid_gpu_and_env_blocked(self):
        s = crash.Sampler(game={"pid": 1, "env": {}})
        picked = crash.Sampler(game={"pid": 1, "env": {"DXVK_FILTER_DEVICE_NAME": "RTX 4070", crash.MARKER: "1"}})
        gpus4070 = ["Intel Corporation Raptor Lake-P", "NVIDIA Corporation AD106M [GeForce RTX 4070 Max-Q]"]
        self.assertNotIn("hybrid_gpu", self.keys({}, picked, gpus=gpus4070))
        launcher_set = {"logs/wine-last.log": "DXVK_FILTER_DEVICE_NAME=RTX 4070"}
        self.assertNotIn("hybrid_gpu", self.keys(launcher_set, s, gpus=gpus4070))
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
        (docs / "Game.log").write_text("* LOADING: 'e_sunrise_peninsula_2' loading time: 2.1 seconds\n"
                                       "Unhandled exception: page fault\n")
        (docs / "archeage.crash").write_text("Exception Code: 0xC0000005\n")
        (docs / "x.dmp").write_bytes(b"MDMP")
        aac = self.home / ".local/share/AAClassic"
        (aac / "wine-staging-pfx/drive_c").mkdir(parents=True)
        (aac / "wine-staging-pfx/drive_c/deep.log").write_text("too deep")
        (aac / "launcher.log").write_text("gateway OK token=s3cr3t-value\n")
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
                             "official-launcher.log", "logs/Game.log", "logs/steam-12345.log",
                             "logs/archeage.crash", "logs/launcher.log"} <= names)
            self.assertNotIn("logs/deep.log", names)
            self.assertFalse(any(n.endswith(".dmp") for n in names))  # dumps can hold the login token
            self.assertNotIn("s3cr3t", z.read("logs/launcher.log").decode())
            self.assertNotIn("logs/notes.txt", names)
            self.assertNotIn("logs/old.log", names)
            details = json.loads(z.read("details.json"))
        self.assertEqual(details["played_seconds"], 600)
        self.assertEqual(details["findings"][0]["key"], "unhandled")
        self.assertTrue(details["dumps_not_included"][0].endswith("x.dmp"))
        h = crash.history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["zone"], "e_sunrise_peninsula_2")
        self.assertEqual(h[0]["minutes"], 10.0)
        self.assertTrue(h[0]["crashed"])

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
            self.assertEqual(cli.main(["fixes", "--on", "wined3d", "--diagnostics", "on"]), 0)
        self.assertIn("[on ] wined3d", out.getvalue())
        self.assertIn("[off] dxvk_memory", out.getvalue())
        self.assertTrue(Settings().get("diagnostics"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["fixes", "--on", "bogus"]), 1)
            self.assertEqual(cli.main(["fixes", "--on", "dxvk_memory"]), 1)


class RedactTests(TempHome):
    def test_hides_tokens_but_not_paths(self):
        self.assertEqual(crash.redact("token=abc123"), "token=[hidden]")
        self.assertEqual(crash.redact('"sessionToken": "xyz"'), '"sessionToken": "[hidden]"')
        self.assertEqual(crash.redact("archeage.exe -t " + "a1" * 20 + " -lang en"), "archeage.exe -t [hidden] -lang en")
        path = "/home/jacob/.local/share/AAClassic/wine-staging-pfx/drive_c/users/steamuser"
        self.assertEqual(crash.redact(path), path)


def fake_wine(home: Path, name: str, wow64: bool = False) -> Path:
    build = home / ".config/heroic/tools/wine" / name
    (build / "bin").mkdir(parents=True)
    (build / "bin/wine").write_text("#!/bin/sh\n")
    (build / "bin/wine").chmod(0o755)
    (build / "lib/wine/x86_64-unix").mkdir(parents=True)
    if not wow64:
        (build / "lib/wine/i386-unix").mkdir(parents=True)
    return build


class WineTests(TempHome):
    def test_auto_picks_staging_only_with_the_launchers_staging_prefix(self):
        from aacam import wine
        staging = fake_wine(self.home, "wine-11.0-staging-amd64")
        fake_wine(self.home, "wine-ge-8-26")
        self.assertIsNone(wine.choose(None, self.home))
        prefix = wine.default_prefix(self.home)
        prefix.mkdir(parents=True)
        (prefix / "system.reg").write_text("")
        self.assertEqual(wine.choose(None, self.home), staging)
        self.assertIsNone(wine.choose(wine.LAUNCHER, self.home))
        self.assertFalse(wine.is_wow64(staging))
        self.assertTrue(wine.is_wow64(fake_wine(self.home, "wine-11.0-staging-amd64-wow64", wow64=True)))

    def test_launch_env_steers_the_official_launcher_away_from_proton(self):
        from aacam import wine
        build = fake_wine(self.home, "wine-11.0-staging-amd64")
        s = Settings()
        s.set("wine", str(build))
        env = crash.launch_env(s, self.home)
        self.assertEqual(env["AAC_WINE"], str(build / "bin/wine"))
        self.assertEqual(env["WINEPREFIX"], str(wine.default_prefix(self.home)))
        self.assertIsNone(env["AAC_PROTON"])
        merged = official.environment(True, base={"PATH": "/usr/bin", "AAC_PROTON": "/x/proton"}, extra=env)
        self.assertNotIn("AAC_PROTON", merged)
        self.assertTrue(merged["PATH"].startswith(str(build / "bin")))
        self.assertEqual(merged["WINEDEBUG"], "fixme-all")

    def test_proton_short_session_is_flagged(self):
        game = {"pid": 1, "exe": "/home/x/Proton 11.0/files/bin/wine-preloader", "env": {crash.MARKER: "1"}}
        found = crash.analyze({}, crash.Sampler(game=game), Settings(), [], played=60)
        self.assertIn("proton_anticheat", {f.key for f in found})


class MemoryWarningTests(TempHome):
    def test_near_limit(self):
        s = crash.Sampler()
        self.assertFalse(s.near_limit())
        s.last = {"VmSize": 3_630_000}  # a long, stable session
        self.assertFalse(s.near_limit())
        s.last = {"VmSize": 3_870_052}  # two minutes before a crash
        self.assertTrue(s.near_limit())
        s.last = {"VmSize": 12 * GiB}  # a 64-bit WoW64 process: meaningless
        self.assertFalse(s.near_limit())


class Wow64Tests(TempHome):
    def test_wow64_waits_for_the_teams_ok(self):
        from aacam import wine
        w = fake_wine(self.home, "wine-11.0-staging-amd64-wow64", wow64=True)
        classic = fake_wine(self.home, "wine-11.0-staging-amd64")
        prefix = wine.default_prefix(self.home)
        prefix.mkdir(parents=True)
        (prefix / "system.reg").write_text("")
        self.assertEqual(wine.choose(str(w), self.home), classic)  # a saved WoW64 choice falls back
        with mock.patch.object(wine, "WOW64_CLEARED", True):
            self.assertEqual(wine.choose(str(w), self.home), w)

    def test_download_checks_checksum_and_unpacks(self):
        import hashlib, tarfile
        from aacam import wine
        src = self.home / "src" / wine.WOW64_NAME / "bin"
        src.mkdir(parents=True)
        (src / "wine").write_text("#!/bin/sh\n")
        (src / "wine").chmod(0o755)
        archive = self.home / "w.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            tar.add(self.home / "src" / wine.WOW64_NAME, arcname=wine.WOW64_NAME)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        with self.assertRaises(wine.WineError):
            wine.download_wow64(self.home / "bad", url=archive.as_uri(), sha256="0" * 64)
        build = wine.download_wow64(self.home / "dest", url=archive.as_uri(), sha256=digest)
        self.assertTrue(os.access(build / "bin/wine", os.X_OK))
