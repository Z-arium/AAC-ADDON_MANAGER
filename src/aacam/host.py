"""Reach the real system from inside the Flatpak sandbox.

Outside Flatpak every function here is a plain pass-through. Inside it, the official launcher,
Wine and system tools must run on the host (`flatpak-spawn --host`, allowed by the manifest's
--talk-name=org.freedesktop.Flatpak), the host's libraries are read under /run/host
(--filesystem=host-os), and the game's process lives in the host's /proc, which the sandbox
can't see. For that, the few /proc files we read (the same read-only figures as outside
Flatpak, see the ground rule in ROADMAP.md) are copied into a private folder by a host shell.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

FLATPAK = Path("/.flatpak-info").exists() or bool(os.environ.get("FLATPAK_ID"))
HOST_ROOT = Path("/run/host")


def root() -> Path:
    """Where the host's /usr, /etc and /lib are: /run/host in Flatpak, else /."""
    return HOST_ROOT if FLATPAK and (HOST_ROOT / "usr").is_dir() else Path("/")


def argv(command: list[str], extra_env: dict[str, str | None] | None = None, cwd: Path | str | None = None) -> list[str]:
    """The command line that runs `command` on the host, with `extra_env` set (None unsets)."""
    if not FLATPAK:
        return command
    out = ["flatpak-spawn", "--host"]
    if cwd:
        out.append(f"--directory={cwd}")
    for key, value in (extra_env or {}).items():
        out.append(f"--unset-env={key}" if value is None else f"--env={key}={value}")
    return out + command


def popen(command: list[str], env: dict[str, str] | None = None, extra_env: dict[str, str | None] | None = None,
          cwd: Path | str | None = None, **kw) -> subprocess.Popen:
    """Start a host program. Outside Flatpak it gets `env`; inside, the host's own environment
    plus `extra_env`, since the sandbox's variables (paths into /app) mean nothing there."""
    if FLATPAK:
        return subprocess.Popen(argv(command, extra_env, cwd), **kw)
    return subprocess.Popen(command, env=env, cwd=cwd, **kw)


def which(name: str) -> bool:
    if not FLATPAK:
        return shutil.which(name) is not None
    try:
        return subprocess.run(argv(["sh", "-c", f"command -v {shlex.quote(name)}"]), capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(argv(command), capture_output=True, text=True, timeout=timeout)


# --- the host's /proc, for the game's process ------------------------------------

_mirror: Path | None = None

# Copies, for every process whose command line names archeage.exe, the files snapshot() and
# space_32bit_kib() read, and links for exe and cwd. Read-only, like reading /proc directly.
_COPY = r"""
set -u
out=$1
rm -rf "$out.new"; mkdir -p "$out.new"; chmod 700 "$out.new"
cat /proc/meminfo > "$out.new/meminfo" 2>/dev/null
for d in /proc/[0-9]*; do
  p=${d#/proc/}
  [ "$p" = "$$" ] && continue  # this shell, whose own command line names archeage.exe
  tr '\0' '\n' < "$d/cmdline" 2>/dev/null | grep -qi 'archeage.*\.exe' || continue
  mkdir "$out.new/$p"
  for f in cmdline status environ maps; do cat "$d/$f" > "$out.new/$p/$f" 2>/dev/null; done
  for l in exe cwd; do t=$(readlink "$d/$l" 2>/dev/null) && ln -s "$t" "$out.new/$p/$l"; done
done
rm -rf "$out"; mv "$out.new" "$out"
"""


def proc(refresh: bool = False) -> Path:
    """The /proc to read game processes from: /proc, or in Flatpak a copy of the host's."""
    global _mirror
    if not FLATPAK:
        return Path("/proc")
    if _mirror is None:
        base = config.cache_dir()
        base.mkdir(parents=True, exist_ok=True)
        _mirror = Path(tempfile.mkdtemp(prefix="hostproc-", dir=base)) / "proc"
    if refresh or not _mirror.is_dir():
        try:
            subprocess.run(argv(["sh", "-c", _COPY, "sh", str(_mirror)]), capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        _mirror.mkdir(parents=True, exist_ok=True)
    return _mirror
