"""Why did ArcheAge Classic crash? Evidence, known causes, and switches that avoid them.

The official launcher starts the game, so we cannot change its command line. What we
can change is the environment we start the launcher with, which Wine passes on to the
game. Every fix below is such an environment variable, off by default, each tied to
the crash it avoids. Evidence is collected after the game exits: the game process's
memory and environment (sampled while it runs), the launcher's output, game and
DXVK logs, and the kernel's out-of-memory, segfault and GPU messages.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import official, paths, report
from .config import Settings, state_dir

# Set on every launch so a report can tell whether our variables reached the game.
MARKER = "AACAM_LAUNCH"


@dataclass(frozen=True)
class Fix:
    key: str
    title: str
    why: str
    env: dict[str, str]


FIXES: tuple[Fix, ...] = (
    Fix("large_address", "Let the game use 4 GB of memory",
        "ArcheAge Classic is a 32-bit game. Without this flag Wine may give it only 2 GB of address space, "
        "and it crashes when that runs out, typically after a while in busy areas or on loading screens.",
        {"WINE_LARGE_ADDRESS_AWARE": "1"}),
    Fix("nvidia_gpu", "Run the game on the NVIDIA card",
        "On machines with Intel or AMD graphics plus an NVIDIA card, the game can start on the built-in chip "
        "or switch between them, which crashes DXVK or makes it very slow.",
        {"__NV_PRIME_RENDER_OFFLOAD": "1", "__VK_LAYER_NV_optimus": "NVIDIA_only",
         "__GLX_VENDOR_LIBRARY_NAME": "nvidia"}),
    Fix("no_fsync", "Turn off esync and fsync",
        "Faster thread syncing in Wine and Proton. Rarely it causes freezes or crashes, and esync crashes "
        "when the open file limit is low.",
        {"PROTON_NO_ESYNC": "1", "PROTON_NO_FSYNC": "1", "WINEESYNC": "0", "WINEFSYNC": "0"}),
    Fix("wined3d", "Draw with OpenGL (WineD3D) instead of Vulkan (DXVK)",
        "Slower, but avoids crashes in DXVK or the Vulkan driver. Use it if crashes mention Vulkan, "
        "\"device lost\" or a GPU error.",
        {"PROTON_USE_WINED3D": "1", "WINEDLLOVERRIDES": "d3d9,d3d10core,d3d11,dxgi=b"}),
)
BY_KEY = {f.key: f for f in FIXES}


def log_dir() -> Path:
    return state_dir() / "game-logs"


def reports_dir() -> Path:
    return state_dir() / "crash-reports"


def diagnostics_env(directory: Path) -> dict[str, str]:
    """Extra logging for one session: Wine exceptions, a Proton log and a DXVK log."""
    return {
        "WINEDEBUG": "fixme-all,+seh",
        "PROTON_LOG": "1",
        "PROTON_LOG_DIR": str(directory),
        "DXVK_LOG_LEVEL": "info",
        "DXVK_LOG_PATH": str(directory),
    }


def launch_env(settings: Settings) -> dict[str, str]:
    """Variables to add when starting the official launcher, from the user's choices."""
    env = {MARKER: "1"}
    for key in settings.get("fixes", []):
        if key in BY_KEY:
            env.update(BY_KEY[key].env)
    if settings.get("diagnostics"):
        directory = log_dir()
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)  # one session's logs at a time
        directory.mkdir(parents=True, exist_ok=True)
        env.update(diagnostics_env(directory))
    return env


def set_fix(settings: Settings, key: str, on: bool) -> None:
    if key not in BY_KEY:
        raise KeyError(key)
    keys = [k for k in settings.get("fixes", []) if k != key and k in BY_KEY]
    if on:
        keys.append(key)
    settings.set("fixes", sorted(keys))


# --- while the game runs ------------------------------------------------------

# Variables worth recording from the game's environment: they show which Wine or
# Proton the official launcher uses and whether our switches reached the game.
ENV_RE = re.compile(r"^(WINE\w*|PROTON\w*|DXVK\w*|VKD3D\w*|STEAM_COMPAT\w*|__NV\w*|__GL\w*|__VK\w*|"
                    r"MESA\w*|VK_\w+|LD_PRELOAD|SDL_VIDEODRIVER|" + MARKER + ")$")
STATUS_KEYS = ("VmPeak", "VmSize", "VmHWM", "VmRSS", "VmSwap", "Threads")


def _kib(value: str) -> int:
    return int(value.split()[0]) if value.split() and value.split()[0].isdigit() else 0


def snapshot(pid: int, proc: Path = Path("/proc")) -> dict | None:
    """Memory, executable, folder and Wine variables of one game process."""
    d = proc / str(pid)
    try:
        status = dict(line.split(":", 1) for line in (d / "status").read_text().splitlines() if ":" in line)
        cmdline = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
    except (OSError, ValueError):
        return None
    snap: dict = {"pid": pid, "cmdline": cmdline}
    for key in STATUS_KEYS:
        if key in status:
            snap[key] = _kib(status[key]) if key != "Threads" else int(status[key].strip() or 0)
    for name, link in (("exe", "exe"), ("cwd", "cwd")):
        try:
            snap[name] = str((d / link).readlink())
        except OSError:
            pass
    try:
        env = (d / "environ").read_bytes().split(b"\0")
        snap["env"] = {k: v for k, _, v in (e.decode(errors="replace").partition("=") for e in env)
                       if ENV_RE.match(k)}
    except OSError:
        pass
    return snap


def mem_available_kib(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return _kib(line.split(":", 1)[1])
    except OSError:
        pass
    return None


@dataclass
class Sampler:
    """Remembers the game process and the peaks of its memory use while it runs."""

    started: float = field(default_factory=time.time)
    game: dict | None = None
    peak: dict = field(default_factory=dict)
    min_mem_available: int | None = None
    samples: int = 0

    def sample(self, pids=paths.game_pids) -> None:
        found = pids()
        if not found:
            return
        snap = snapshot(found[0])
        if snap is None:
            return
        self.samples += 1
        self.game = {k: v for k, v in snap.items() if k not in STATUS_KEYS} | {"pid": snap["pid"]}
        for key in ("VmPeak", "VmSize", "VmHWM", "VmRSS", "VmSwap", "Threads"):
            if key in snap:
                self.peak[key] = max(self.peak.get(key, 0), snap[key])
        avail = mem_available_kib()
        if avail is not None:
            self.min_mem_available = avail if self.min_mem_available is None else min(self.min_mem_available, avail)


# --- after the game exits -----------------------------------------------------

@dataclass(frozen=True)
class Finding:
    key: str
    title: str
    detail: str
    crash: bool = True  # evidence of a crash, not just a hint
    fix: str | None = None  # a key in FIXES that should help


def _run(cmd: list[str], timeout: int = 20) -> str:
    if not shutil.which(cmd[0]):
        return f"({cmd[0]} not installed)\n"
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return f"({' '.join(cmd)} failed: {e})\n"


KERNEL_RE = re.compile(r"out of memory|oom-kill|killed process|segfault|general protection|archeage|wine|"
                       r"NVRM|Xid|i915|\bxe\b|amdgpu.*(fault|hang|timeout|reset)|gpu hang|ring .* timeout",
                       re.IGNORECASE)


def journal(since: float) -> str:
    """Kernel messages that explain crashes, plus wine/archeage core dumps, since a time."""
    stamp = f"@{int(since)}"
    kernel = _run(["journalctl", "-k", "--no-pager", "-o", "short-iso", "--since", stamp])
    lines = [line for line in kernel.splitlines() if KERNEL_RE.search(line)]
    dumps = _run(["coredumpctl", "list", "--no-pager", "--since", stamp])
    dumps = "\n".join(line for line in dumps.splitlines()
                      if re.search(r"wine|archeage|preloader|\.exe|TIME", line, re.IGNORECASE))
    return "Kernel:\n" + ("\n".join(lines[-200:]) or "(nothing relevant)") + "\n\nCore dumps:\n" + (dumps or "(none)") + "\n"


LOG_SUFFIXES = {".log", ".txt", ".dmp", ".mdmp", ".err"}
TEXT_LIMIT = 256 * 1024
DUMP_LIMIT = 64 * 1024 * 1024


def game_files(since: float, game: dict | None, home: Path | None = None) -> list[Path]:
    """Logs and crash dumps the game, Wine, Proton or DXVK wrote since the game started."""
    home = home or Path.home()
    dirs: list[Path] = [log_dir(), home]  # Proton writes steam-<appid>.log to $HOME by default
    for docs in paths.documents_dirs(home):
        dirs.append(docs / "AAClassic")
    if game and game.get("cwd"):
        cwd = Path(game["cwd"])
        dirs += [cwd, cwd.parent]  # bin32 and the game folder
    found: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        depth = 1 if d == home else 2
        for p in _walk(d, depth):
            try:
                st = p.stat()
            except OSError:
                continue
            if p.suffix.lower() not in LOG_SUFFIXES or st.st_mtime < since - 5 or p.resolve() in seen:
                continue
            if d == home and not p.name.startswith("steam-"):
                continue
            seen.add(p.resolve())
            found.append(p)
    return found


def _walk(d: Path, depth: int):
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for p in entries:
        if p.is_file():
            yield p
        elif depth > 1 and p.is_dir() and p.name.lower() not in ("addon", "addons"):
            yield from _walk(p, depth - 1)


def _tail_text(path: Path, limit: int = TEXT_LIMIT) -> str:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - limit))
            return f.read().decode(errors="replace")
    except OSError:
        return ""


GiB = 1024 * 1024  # in KiB

SIGNS: tuple[tuple[str, re.Pattern, str, str, str | None], ...] = (
    # key, pattern, title, detail, fix
    ("oom_killed", re.compile(r"Killed process \d+ \([^)]*(archeage|wine|preloader|\.exe)[^)]*\)",
                              re.IGNORECASE),
     "Linux ran out of memory and killed the game",
     "The kernel's out-of-memory killer stopped it. Close other programs, add swap or zram, or lower the game's "
     "graphics settings.", None),
    ("address_space", re.compile(r"E_OUTOFMEMORY|VK_ERROR_OUT_OF_(HOST|DEVICE)_MEMORY|out of (virtual )?memory|"
                                 r"Failed to allocate|virtual_alloc.*failed|NtAllocateVirtualMemory.*failed",
                                 re.IGNORECASE),
     "The game ran out of memory",
     "A 32-bit game can address at most 4 GB, and only 2 GB without the large address flag.", "large_address"),
    ("device_lost", re.compile(r"VK_ERROR_DEVICE_LOST|Device lost|DXGI_ERROR_DEVICE_(REMOVED|HUNG|RESET)",
                               re.IGNORECASE),
     "The graphics driver crashed (device lost)",
     "DXVK lost the GPU. Update the graphics driver, or draw with WineD3D instead.", "wined3d"),
    ("gpu_fault", re.compile(r"NVRM: Xid|GPU HANG|amdgpu.*(page fault|ring .* timeout|GPU reset)|"
                             r"i915.*(GPU HANG|reset)|xe .*(reset|timedout)", re.IGNORECASE),
     "The kernel logged a GPU error",
     "The GPU or its driver faulted while the game ran. Update the driver; if it repeats, try WineD3D.", "wined3d"),
    ("no_vulkan", re.compile(r"vkCreateInstance failed|Failed to create Vulkan instance|No adapters found|"
                             r"Failed to load vulkan|DXVK: No adapters", re.IGNORECASE),
     "Vulkan did not start for the game",
     "The 32-bit Vulkan driver is probably missing. Setup shows the command that installs it.", None),
    ("too_many_files", re.compile(r"Too many open files|eventfd: Too many", re.IGNORECASE),
     "Wine hit the open file limit (esync)",
     "Turn off esync and fsync, or raise the open file limit.", "no_fsync"),
    ("unhandled", re.compile(r"Unhandled (page fault|exception|stack overflow|illegal instruction)[^\n]*|"
                             r"archeage\.exe\[\d+\]: segfault[^\n]*", re.IGNORECASE),
     "The game crashed (unhandled exception)",
     "", None),
)


KERNEL_SIGNS = {"oom_killed", "gpu_fault"}


def _gpus_text(text: str) -> tuple[bool, bool]:
    lower = text.lower()
    nvidia = "nvidia" in lower
    other = "intel" in lower or "amd" in lower or "advanced micro devices" in lower or "radeon" in lower
    return nvidia, other


def analyze(texts: dict[str, str], sampler: Sampler | None, settings: Settings, gpus: list[str],
            played: float | None = None) -> list[Finding]:
    findings: list[Finding] = []
    logs = "\n".join(text for name, text in texts.items() if name != "journal.txt")
    kernel = texts.get("journal.txt", "")
    for key, pattern, title, detail, fix in SIGNS:
        # The kernel log covers every program, so only its game-specific lines count.
        m = pattern.search(kernel if key in KERNEL_SIGNS else logs)
        if m is None and key == "unhandled":
            m = pattern.search(kernel)
        if m is None:
            continue
        if key == "unhandled":
            detail = f"Wine reported: {m.group(0).strip()[:200]}"
        findings.append(Finding(key, title, detail, True, fix))

    peak = sampler.peak.get("VmPeak", 0) if sampler else 0
    keys = {f.key for f in findings}
    if peak >= 3.5 * GiB and "address_space" not in keys:
        findings.append(Finding("address_space", "The game was close to the 4 GB limit",
                                f"It used {peak / GiB:.1f} GB of address space. A 32-bit game crashes at 4 GB.",
                                True, "large_address"))
    elif 1.8 * GiB <= peak < 2.2 * GiB and "large_address" not in settings.get("fixes", []):
        findings.append(Finding("address_space_2g", "The game stopped near 2 GB of memory",
                                f"It used {peak / GiB:.1f} GB, right at the limit for 32-bit games without the "
                                "large address flag.", True, "large_address"))

    game = sampler.game if sampler else None
    if game is not None and MARKER not in game.get("env", {}):
        findings.append(Finding("env_blocked", "Our fixes don't reach the game",
                                "The official launcher starts the game without the variables we pass it, so "
                                "switches in this launcher have no effect. Please report this.", False))

    nvidia, other = _gpus_text("\n".join(gpus))
    if nvidia and other and "nvidia_gpu" not in settings.get("fixes", []):
        findings.append(Finding("hybrid_gpu", "This machine has two graphics chips",
                                "The game may start on the built-in one. Running it on the NVIDIA card avoids "
                                "crashes and slowness from that.", False, "nvidia_gpu"))

    if played is not None and played < official.GameWatch.QUICK_EXIT and not any(f.crash for f in findings):
        findings.append(Finding("quick_exit", "The game closed soon after it started",
                                f"It ran for {played:.0f} seconds without leaving a reason in the logs. "
                                "Turn on diagnostics and play again to record one.", True))
    return findings


def crashed(findings: list[Finding]) -> bool:
    return any(f.crash for f in findings)


def collect(settings: Settings, sampler: Sampler | None = None, since: float | None = None,
            played: float | None = None) -> tuple[Path, list[Finding]]:
    """Gather everything about the last session into a zip and say what it shows."""
    since = since if since is not None else (sampler.started if sampler else time.time() - 3600)
    game = sampler.game if sampler else None
    texts: dict[str, str] = {"journal.txt": journal(since)}
    launcher_log = official.log_path()
    if launcher_log.is_file():
        texts["official-launcher.log"] = _tail_text(launcher_log)
    files = game_files(since, game)
    for p in files:
        if p.suffix.lower() not in (".dmp", ".mdmp"):
            texts[f"logs/{p.name}"] = _tail_text(p)
    gpus = report._gpus()
    findings = analyze(texts, sampler, settings, gpus, played)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = reports_dir() / f"crash-{stamp}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "findings": [asdict(f) for f in findings],
        "played_seconds": played,
        "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since)),
        "fixes_on": settings.get("fixes", []),
        "diagnostics": bool(settings.get("diagnostics")),
        "game_process": game,
        "peak_kib": sampler.peak if sampler else {},
        "min_mem_available_kib": sampler.min_mem_available if sampler else None,
        "files": [str(p) for p in files],
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.txt", summarize(findings))
        z.writestr("details.json", json.dumps(summary, indent=2))
        z.writestr("report.txt", report.build(settings))
        for name, text in texts.items():
            z.writestr(name, text)
        for p in files:
            if p.suffix.lower() in (".dmp", ".mdmp"):
                try:
                    if p.stat().st_size <= DUMP_LIMIT:
                        z.write(p, f"dumps/{p.name}")
                except OSError:
                    pass
    _prune(reports_dir(), keep=10)
    return out, findings


def _prune(directory: Path, keep: int) -> None:
    old = sorted(directory.glob("crash-*.zip"), reverse=True)[keep:]
    for p in old:
        p.unlink(missing_ok=True)


def summarize(findings: list[Finding]) -> str:
    if not findings:
        return "No known crash cause found.\n"
    lines = []
    for f in findings:
        lines.append(f"- {f.title}. {f.detail}".rstrip())
        if f.fix:
            lines.append(f"  Fix: turn on \"{BY_KEY[f.fix].title}\" (aacam fixes --on {f.fix})")
    return "\n".join(lines) + "\n"
