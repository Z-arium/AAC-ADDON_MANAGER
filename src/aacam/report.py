"""A plain-text report of this system and the game setup, to paste into a bug report or Discord."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

from . import __version__, github, logs, official, paths, syscheck
from .config import Settings
from .core import Manager

MARK = {syscheck.OK: "ok  ", syscheck.WARN: "warn", syscheck.FAIL: "FAIL"}


def _gpus() -> list[str]:
    if not shutil.which("lspci"):
        return ["(lspci not installed)"]
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ["(lspci failed)"]
    return [line.split(": ", 1)[-1] for line in out.splitlines() if " VGA " in line or "3D controller" in line]


def build(settings: Settings | None = None, log_lines: int = 80) -> str:
    settings = settings or Settings()
    d = syscheck.distro()
    lines = [
        f"ArcheAge Classic Linux Launcher {__version__}",
        f"Distro: {d.name} ({d.family})",
        f"Kernel: {platform.release()}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '?')} on {os.environ.get('XDG_SESSION_TYPE', '?')}",
        *[f"GPU: {g}" for g in _gpus()],
        "",
        "System check:",
    ]
    for c in syscheck.run():
        lines.append(f"  [{MARK[c.status]}] {c.title}: {c.detail}")
    launcher = official.locate(settings)
    lines += [
        "",
        f"Official launcher: {launcher or 'not found'}",
        f"Custom launch command: {settings.get('launch') or 'none'}",
        f"Game running: {'yes' if paths.game_running() else 'no'}",
    ]
    mgr = Manager.from_settings(settings)
    if mgr is None:
        lines.append("Addon folder: not set")
    else:
        lines.append(f"Addon folder: {mgr.install.addon_dir}")
        enabled = [a.name for a in mgr.local() if a.enabled]
        lines.append(f"Enabled addons ({len(enabled)}): {', '.join(enabled) or 'none'}")
        versions = [f"{n} {r.get('version')}" for n, r in sorted(mgr.db.addons.items())]
        lines.append(f"Tracked addons ({len(versions)}): {', '.join(versions) or 'none'}")
    lines.append(f"GitHub token: {'set' if github.token() else 'none'}")
    ours = logs.tail(log_lines) if log_lines else ""
    if ours:
        lines += ["", f"Last lines of {logs.path()}:", ours]
    tail = official.log_tail(150)
    if tail:
        lines += ["", f"Last lines of {official.log_path()}:", tail]
    return "\n".join(lines) + "\n"
