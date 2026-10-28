"""Find ArcheAge Classic installs inside Wine, Lutris, Proton, Bottles and Heroic prefixes."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Folder names the game installer uses on drive C: AAClassic, AAClassic2, ...
INSTALL_RE = re.compile(r"^AAClassic\d*$", re.IGNORECASE)

# Where addons live inside the install, newest layout first. The Classic Addon
# Manager uses Documents/Addon, the wiki mentions Documents/AAClassic/addons.
ADDON_SUBDIRS = ("Documents/Addon", "Documents/AAClassic/addons")


@dataclass(frozen=True)
class Install:
    root: Path  # .../drive_c/AAClassic
    addon_dir: Path  # where addon folders and addons.txt live

    @property
    def addons_txt(self) -> Path:
        return self.addon_dir / "addons.txt"


def prefix_roots(home: Path | None = None) -> list[Path]:
    """Directories that hold drive_c folders, directly or one level down."""
    home = home or Path.home()
    steam_roots = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    roots: list[Path] = [home / ".wine"]
    extra = os.environ.get("WINEPREFIX")
    if extra:
        roots.insert(0, Path(extra))
    for steam in steam_roots:
        roots.extend((steam / "steamapps/compatdata").glob("*/pfx"))
    roots.extend((home / "Games").glob("*"))
    roots.extend((home / "Games/Heroic/Prefixes").glob("*"))
    roots.extend((home / ".local/share/bottles/bottles").glob("*"))
    roots.extend((home / ".var/app/com.usebottles.bottles/data/bottles/bottles").glob("*"))
    roots.extend((home / ".local/share/lutris/runners/wine").glob("*"))
    return roots


def install_from_root(root: Path) -> Install:
    """Pick the addon directory for an install root, preferring one that exists."""
    for sub in ADDON_SUBDIRS:
        candidate = root / sub
        if candidate.is_dir():
            return Install(root, candidate)
    return Install(root, root / ADDON_SUBDIRS[0])


def _version(path: Path) -> int:
    digits = path.name[len("AAClassic"):]
    return int(digits) if digits else 0


def detect(home: Path | None = None) -> list[Install]:
    """Every install found, highest AAClassic number first."""
    seen: set[Path] = set()
    found: list[Path] = []
    for prefix in prefix_roots(home):
        drive_c = prefix / "drive_c"
        if not drive_c.is_dir():
            continue
        for entry in drive_c.iterdir():
            if entry.is_dir() and INSTALL_RE.match(entry.name):
                real = entry.resolve()
                if real not in seen:
                    seen.add(real)
                    found.append(entry)
    found.sort(key=_version, reverse=True)
    return [install_from_root(p) for p in found]


def resolve_user_path(path: Path) -> Install:
    """Accept the install root, its Documents folder, or the addon folder itself."""
    path = path.expanduser().resolve()
    if path.name.lower() in ("addon", "addons"):
        root = path.parent.parent if path.parent.name.lower() == "documents" else path.parents[2]
        return Install(root, path)
    if path.name.lower() == "documents":
        return install_from_root(path.parent)
    return install_from_root(path)


def game_running() -> bool:
    """True if an ArcheAge process is running under Wine."""
    proc = Path("/proc")
    for pid in proc.iterdir():
        if not pid.name.isdigit():
            continue
        try:
            cmdline = (pid / "cmdline").read_bytes().lower()
        except OSError:
            continue
        if b"archeage" in cmdline and b".exe" in cmdline:
            return True
    return False
