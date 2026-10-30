"""Find ArcheAge Classic installs inside Wine, Lutris, Proton, Bottles and Heroic prefixes."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Folder names the game installer uses on drive C: AAClassic, AAClassic2, ...
INSTALL_RE = re.compile(r"^AAClassic\d*$", re.IGNORECASE)

# Where addons live, first existing match wins. The game writes to
# <Windows Documents>/AAClassic/Addon, which Wine usually maps to ~/Documents;
# Classic Addon Manager uses <install>/Documents/Addon on drive C.
DOCS_SUBDIRS = ("Addon", "addons")
INSTALL_SUBDIRS = ("Documents/Addon", "Documents/AAClassic/Addon", "Documents/AAClassic/addons")


@dataclass(frozen=True)
class Install:
    root: Path  # the game's AAClassic folder, under Documents or drive_c
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
    # The official ArcheAge Classic AppImage launcher keeps its prefixes here,
    # e.g. AAClassic/wine-staging-pfx and AAClassic/steam-compatdata/pfx.
    launcher = home / ".local/share/AAClassic"
    roots: list[Path] = [*launcher.glob("*-pfx"), *launcher.glob("*/pfx"), home / ".wine"]
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


def install_from_root(root: Path, in_docs: bool | None = None) -> Install:
    """Pick the addon directory for an AAClassic folder, preferring one that exists.
    in_docs: the folder is in a Documents folder (whatever the desktop's language calls it)."""
    if in_docs is None:
        in_docs = root.parent.name.lower() in ("documents", "my documents")
    subdirs = DOCS_SUBDIRS if in_docs else INSTALL_SUBDIRS
    for sub in subdirs:
        candidate = root / sub
        if candidate.is_dir():
            return Install(root, candidate)
    return Install(root, root / subdirs[0])


def documents_dirs(home: Path | None = None) -> list[Path]:
    """The user's Documents folder plus each Wine user's Documents in every prefix."""
    home = home or Path.home()
    dirs = [home / "Documents"]
    try:
        for line in (home / ".config/user-dirs.dirs").read_text().splitlines():
            if line.startswith("XDG_DOCUMENTS_DIR="):
                dirs.insert(0, Path(line.split("=", 1)[1].strip('"').replace("$HOME", str(home))))
    except OSError:
        pass
    for prefix in prefix_roots(home):
        dirs.extend((prefix / "drive_c/users").glob("*/Documents"))
        dirs.extend((prefix / "drive_c/users").glob("*/My Documents"))
    return dirs


def _version(path: Path) -> int:
    digits = path.name[len("AAClassic"):]
    return int(digits) if digits else 0


def detect(home: Path | None = None) -> list[Install]:
    """Every addon folder found; ones the game already uses come first."""
    candidates: list[Install] = []
    for docs in documents_dirs(home):
        if (docs / "AAClassic").is_dir():
            candidates.append(install_from_root(docs / "AAClassic", in_docs=True))
    candidates.extend(_install_roots(home))

    unique: dict[Path, Install] = {}
    for inst in candidates:
        unique.setdefault(inst.addon_dir.resolve(), inst)
    ranked = list(unique.values())
    # Stable sort: existing addons.txt, then existing addon folder, then discovery order.
    ranked.sort(key=lambda i: (not i.addons_txt.is_file(), not i.addon_dir.is_dir()))
    existing = [i for i in ranked if i.addon_dir.is_dir()]
    return existing or ranked


def _install_roots(home: Path | None) -> list[Install]:
    """Game installs on drive C, highest AAClassic number first."""
    seen: set[Path] = set()
    found: list[Path] = []
    for prefix in prefix_roots(home):
        drive_c = prefix / "drive_c"
        if not drive_c.is_dir():
            continue
        # Heroic installs under drive_c/Games/AAClassic by default.
        for entry in [*drive_c.iterdir(), *(drive_c / "Games").glob("AAClassic*")]:
            if entry.is_dir() and INSTALL_RE.match(entry.name):
                real = entry.resolve()
                if real not in seen:
                    seen.add(real)
                    found.append(entry)
    found.sort(key=_version, reverse=True)
    return [install_from_root(p) for p in found]


def resolve_user_path(path: Path) -> Install:
    """Accept an AAClassic folder, an install's Documents folder, or the addon folder itself."""
    path = path.expanduser().resolve()
    if path.name.lower() in ("addon", "addons"):
        root = path.parent.parent if path.parent.name.lower() == "documents" else path.parent
        return Install(root, path)
    if path.name.lower() == "documents":
        if (path / "AAClassic").is_dir():
            return install_from_root(path / "AAClassic")
        return install_from_root(path.parent)
    return install_from_root(path)


def game_pids() -> list[int]:
    """ArcheAge processes running under Wine, the game itself (argv[0] is archeage.exe) first."""
    found: list[tuple[bool, int]] = []
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            cmdline = (pid / "cmdline").read_bytes().lower()
        except OSError:
            continue
        if b"archeage" in cmdline and b".exe" in cmdline:
            argv0 = cmdline.split(b"\0", 1)[0].replace(b"\\", b"/")
            found.append((not argv0.endswith(b"archeage.exe"), int(pid.name)))
    return [pid for _, pid in sorted(found)]


def game_running() -> bool:
    """True if an ArcheAge process is running under Wine."""
    return bool(game_pids())
