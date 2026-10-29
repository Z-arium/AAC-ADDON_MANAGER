"""Which Wine build the official launcher runs the game with.

The official launcher picks Wine from AAC_WINE and AAC_WINEPREFIX, and prefers Proton
when AAC_PROTON or Steam's compat variables are set. Under Proton the game's anti-cheat
(Mail.ru's MRAC) closes the client right after server select, so we steer it to a plain
Wine-Staging build and the launcher's own wine-staging-pfx prefix when one is installed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .config import data_dir

LAUNCHER = "launcher"  # setting value: let the official launcher choose
# The launcher prefers Proton when any of these is set.
PROTON_VARS = ("AAC_PROTON", "AAC_STEAM_PATH", "AAC_STEAM_COMPAT_APP_ID", "AAC_STEAM_COMPAT_DATA_PATH",
               "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH")


# A WoW64 build runs the 32-bit game without 32-bit Linux libraries: Wine's Unix side and the graphics
# driver live in 64-bit memory, leaving the game more of its 4 GB. Per the ground rule in ROADMAP.md it
# stays switched off until the ArcheAge Classic team confirms it is allowed.
WOW64_CLEARED = False
WOW64_NAME = "wine-11.0-staging-amd64-wow64"
WOW64_URL = f"https://github.com/Kron4ek/Wine-Builds/releases/download/11.0/{WOW64_NAME}.tar.xz"
WOW64_SHA256 = "e6538a417dd2e7f738ad77addf74bfd9e45c9d970653674f93bde2df29055f2f"  # from the release's sha256sums.txt


class WineError(RuntimeError):
    pass


def own_dir() -> Path:
    return data_dir() / "wine"


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


def allowed(build: Path) -> bool:
    return WOW64_CLEARED or not is_wow64(build)


def default_prefix(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local/share/AAClassic/wine-staging-pfx"


def choose(setting: str | None, home: Path | None = None) -> Path | None:
    """The Wine build to use: the saved one, else a Wine-Staging build when the official
    launcher's Wine-Staging prefix exists, else None (the launcher decides)."""
    if setting == LAUNCHER:
        return None
    if setting:
        build = Path(setting)
        if os.access(build / "bin/wine", os.X_OK) and allowed(build):
            return build
    if not (default_prefix(home) / "system.reg").is_file():
        return None
    found = [b for b in builds(home) if allowed(b)]
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


def download_wow64(dest: Path | None = None, url: str = WOW64_URL, sha256: str = WOW64_SHA256) -> Path:
    """Download and unpack the WoW64 Wine-Staging build, checking its published checksum."""
    dest = dest or own_dir()
    build = dest / WOW64_NAME
    if os.access(build / "bin/wine", os.X_OK):
        return build
    dest.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"aacam/{__version__}"})
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        archive = Path(tmp) / "wine.tar.xz"
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp, open(archive, "wb") as out:
                while chunk := resp.read(1 << 20):
                    digest.update(chunk)
                    out.write(chunk)
        except (urllib.error.URLError, OSError) as e:
            raise WineError(f"could not download {url}: {e}") from e
        if digest.hexdigest() != sha256:
            raise WineError(f"{url} does not match its published checksum; not using it")
        unpacked = Path(tmp) / "out"
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                target = (unpacked / member.name).resolve()
                if not str(target).startswith(str(unpacked.resolve())) or member.isdev():
                    raise WineError(f"unsafe path in {url}: {member.name}")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(unpacked, filter="data")
            else:
                tar.extractall(unpacked)
        top = unpacked / WOW64_NAME
        if not os.access(top / "bin/wine", os.X_OK):
            raise WineError(f"{url} has no {WOW64_NAME}/bin/wine")
        if build.exists():
            shutil.rmtree(build)
        top.rename(build)
    return build
