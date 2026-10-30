"""The official ArcheAge Classic launcher: find it, download it, start it and watch the game.

The official launcher does login, game downloads and patching. We never touch any
of that; we only start it with the right environment and notice when the game runs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, net, paths
from .config import state_dir, write_atomic

# Linux AppImage, from https://aa-classic.com/news/launcher-update-220. The
# launcher updates itself after that, so an old version here is fine.
DOWNLOAD_URL = "https://aa-classic.com/api/download/linux/launcher{version}.AppImage"
DEFAULT_VERSION = "2.2.0"
NEWS_URL = "https://aa-classic.com/news"
FILE_NAME = "ArcheAgeClassic.AppImage"

NAME_RE = re.compile(r"^(archeage[-_ ]?classic|aaclassic|launcher)[-_. ]?[\w.\-]*\.appimage$", re.IGNORECASE)


class LauncherError(RuntimeError):
    pass


def default_dir(home: Path | None = None) -> Path:
    # Where the official launcher puts itself; its Wine prefixes live in ~/.local/share/AAClassic.
    return (home or Path.home()) / ".local/share/ArcheAgeClassic"


def log_path() -> Path:
    return state_dir() / "official-launcher.log"


def _search_dirs(home: Path) -> list[Path]:
    return [
        default_dir(home),
        home / ".local/share/AAClassic",
        home / "Applications",
        home / "Games",
        home / "Games/ArcheAgeClassic",
        home / "Downloads",
        home / "Desktop",
        home,
    ]


def find(home: Path | None = None) -> Path | None:
    """The official launcher AppImage, preferring the default folder, then the newest file."""
    home = home or Path.home()
    for d in _search_dirs(home):
        try:
            hits = [
                p for p in d.iterdir()
                if p.is_file() and NAME_RE.match(p.name) and "linux-launcher" not in p.name.lower()  # not us
            ]
        except OSError:
            continue
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


def download(dest_dir: Path | None = None, version: str = DEFAULT_VERSION) -> Path:
    """Download the official Linux launcher into dest_dir and make it executable."""
    dest = (dest_dir or default_dir()) / FILE_NAME
    url = DOWNLOAD_URL.format(version=version)
    req = urllib.request.Request(url, headers={"User-Agent": f"aacam/{__version__}"})
    try:
        with net.urlopen(req, timeout=300) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        raise LauncherError(
            f"aa-classic.com answered {e.code} for {url}. A newer launcher may have replaced it; "
            f"download it from {NEWS_URL} and choose the file instead."
        ) from e
    except urllib.error.URLError as e:
        raise LauncherError(f"could not reach aa-classic.com: {e.reason}") from e
    if not blob.startswith(b"\x7fELF") or len(blob) < 1 << 20:
        raise LauncherError(f"{url} did not return an AppImage; download it from {NEWS_URL} instead")
    write_atomic(dest, blob)
    dest.chmod(0o755)
    return dest


def environment(fuse_ok: bool, base: dict[str, str] | None = None,
                extra: dict[str, str | None] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    # Our own AppImage runtime sets these for us; they would break the official one.
    for key in ("APPDIR", "APPIMAGE", "ARGV0", "OWD", "PYTHONHOME", "PYTHONPATH",
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    # PyInstaller points LD_LIBRARY_PATH at our bundled libraries and keeps the user's in *_ORIG.
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    if not fuse_ok:
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    # Wine choice, crash fixes and diagnostics; the launcher passes them on to the game. None removes one.
    for key, value in (extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def start(appimage: Path, fuse_ok: bool, extra_env: dict[str, str | None] | None = None) -> subprocess.Popen:
    """Start the official launcher detached, logging its output."""
    if not appimage.is_file():
        raise LauncherError(f"official launcher not found at {appimage}")
    if not os.access(appimage, os.X_OK):
        appimage.chmod(appimage.stat().st_mode | 0o111)
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as out:
        out.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} starting {appimage} (FUSE {'yes' if fuse_ok else 'no'})\n")
        for key, value in sorted((extra_env or {}).items()):
            if value is not None and key != "PATH":
                out.write(f"# {key}={value}\n")
        out.flush()
        return subprocess.Popen(
            [str(appimage)], cwd=appimage.parent, env=environment(fuse_ok, extra=extra_env),
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
        )


def log_tail(lines: int = 200) -> str:
    try:
        return "\n".join(log_path().read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class GameWatch:
    """Follows one Play: launcher started, game running, game closed.

    Call poll() every few seconds; it returns the new state when it changes.
    """

    QUICK_EXIT = 90  # seconds; a game that closes this fast probably crashed

    def __init__(self, proc: subprocess.Popen | None, clock=time.monotonic, running=paths.game_running,
                 sampler=None) -> None:
        self.proc = proc
        self.sampler = sampler  # a crash.Sampler, fed while the game runs
        self.clock = clock
        self.running = running
        self.state = "launcher"
        self.game_started: float | None = None
        self.played = 0.0

    def poll(self) -> str | None:
        if self.state == "launcher":
            if self.running():
                self.state, self.game_started = "game", self.clock()
                self._sample()
                return self.state
            if self.proc is not None and self.proc.poll() is not None:
                self.state = "launcher_failed" if self.proc.returncode else "launcher_closed"
                return self.state
        elif self.state == "game":
            if self.running():
                self._sample()
                return None
            self.played = self.clock() - (self.game_started or self.clock())
            self.state = "crashed" if self.played < self.QUICK_EXIT else "closed"
            return self.state
        return None

    def _sample(self) -> None:
        if self.sampler is not None:
            try:
                self.sampler.sample()
            except Exception:  # evidence is best effort; never break the watch
                pass


def locate(settings) -> Path | None:
    """The saved official launcher if it still exists, else one we can find (and save)."""
    saved = settings.get("official_launcher")
    if saved and Path(saved).is_file():
        return Path(saved)
    found = find()
    if found:
        settings.set("official_launcher", str(found))
    return found
