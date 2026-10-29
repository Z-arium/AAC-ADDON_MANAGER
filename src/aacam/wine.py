"""Which Wine build the official launcher runs the game with.

The official launcher picks Wine from AAC_WINE and AAC_WINEPREFIX, and prefers Proton
when AAC_PROTON or Steam's compat variables are set. Under Proton the game's anti-cheat
(Mail.ru's MRAC) closes the client right after server select, so we steer it to a plain
Wine-Staging build and the launcher's own wine-staging-pfx prefix when one is installed.
"""

from __future__ import annotations

import os
from pathlib import Path

LAUNCHER = "launcher"  # setting value: let the official launcher choose
# The launcher prefers Proton when any of these is set.
PROTON_VARS = ("AAC_PROTON", "AAC_STEAM_PATH", "AAC_STEAM_COMPAT_APP_ID", "AAC_STEAM_COMPAT_DATA_PATH",
               "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH")


def runner_dirs(home: Path) -> list[Path]:
    return [
        home / ".local/share/aacam/wine",
        home / ".config/heroic/tools/wine",
        home / ".var/app/com.heroicgameslauncher.hgl/config/heroic/tools/wine",
        home / ".local/share/lutris/runners/wine",
        home / ".var/app/net.lutris.Lutris/data/lutris/runners/wine",
    ]


def builds(home: Path | None = None) -> list[Path]:
    """Wine builds (folders with bin/wine) from Heroic, Lutris and our own folder, newest name first."""
    home = home or Path.home()
    found: list[Path] = []
    for d in runner_dirs(home):
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name, reverse=True)
        except OSError:
            continue
        found += [p for p in entries if os.access(p / "bin/wine", os.X_OK)]
    return found


def is_wow64(build: Path) -> bool:
    """A WoW64 build runs 32-bit games without 32-bit Linux libraries, so the graphics driver
    no longer takes room in the game's 4 GB."""
    return not (build / "lib/wine/i386-unix").is_dir() and not (build / "lib/wine/i386-unix.so").exists()


def label(build: Path) -> str:
    return f"{build.name}{' (WoW64)' if is_wow64(build) else ''}"


def default_prefix(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local/share/AAClassic/wine-staging-pfx"


def choose(setting: str | None, home: Path | None = None) -> Path | None:
    """The Wine build to use: the saved one, else a Wine-Staging build when the official
    launcher's Wine-Staging prefix exists, else None (the launcher decides)."""
    if setting == LAUNCHER:
        return None
    if setting:
        build = Path(setting)
        return build if os.access(build / "bin/wine", os.X_OK) else None
    if not (default_prefix(home) / "system.reg").is_file():
        return None
    found = builds(home)
    staging = [b for b in found if "staging" in b.name.lower()]
    return (staging or found or [None])[0]


def env(build: Path, prefix: Path, path: str | None = None) -> dict[str, str | None]:
    """Variables for the official launcher; None removes a variable."""
    values: dict[str, str | None] = {
        "AAC_WINE": str(build / "bin/wine"),
        "AAC_WINEPREFIX": str(prefix),
        "WINEPREFIX": str(prefix),
        "PATH": f"{build / 'bin'}:{path if path is not None else os.environ.get('PATH', '')}",
    }
    values.update({name: None for name in PROTON_VARS})
    return values
