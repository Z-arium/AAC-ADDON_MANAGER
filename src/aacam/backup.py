"""Snapshots of addon settings: every addon's .data folder and addons.txt, in one zip.

Taken by hand, and automatically before updates, so a bad update or a lost addons.txt can be undone.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import installer
from .config import data_dir
from .paths import Install

KEEP_AUTO = 10  # automatic snapshots kept per addon folder; ones made by hand are never pruned


@dataclass(frozen=True)
class Backup:
    path: Path
    when: float
    auto: bool

    @property
    def label(self) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.when))
        return f"{stamp}{' (automatic, before an update)' if self.auto else ''}"


def folder(install: Install) -> Path:
    """One folder of snapshots per addon folder, so two game installs never mix."""
    key = hashlib.sha1(str(install.addon_dir).encode()).hexdigest()[:10]
    return data_dir() / "backups" / key


def create(install: Install, auto: bool = False) -> Backup:
    """Zip addons.txt and each addon's .data folder."""
    dest = folder(install)
    dest.mkdir(parents=True, exist_ok=True)
    when = time.time()
    name = time.strftime("%Y%m%d-%H%M%S", time.localtime(when)) + f"-{int(when * 1000) % 1000:03d}"
    path = dest / f"{name}{'-auto' if auto else ''}.zip"
    n = 0
    while path.exists() or path.with_name(path.name.replace("-auto", "")).exists() \
            or path.with_name(path.stem + "-auto.zip").exists():
        n += 1  # two in the same millisecond: never overwrite one
        path = dest / f"{name}-{n}{'-auto' if auto else ''}.zip"
    tmp = path.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BACKUP.txt", f"addon folder: {install.addon_dir}\n")
        if install.addons_txt.is_file():
            zf.write(install.addons_txt, "addons.txt")
        if install.addon_dir.is_dir():
            for addon in sorted(install.addon_dir.iterdir()):
                data = addon / installer.DATA_DIR
                if not addon.is_dir() or not data.is_dir():
                    continue
                for f in sorted(data.rglob("*")):
                    if f.is_file():
                        zf.write(f, f"{addon.name}/{installer.DATA_DIR}/{f.relative_to(data).as_posix()}")
    tmp.replace(path)
    if auto:
        for old in [b for b in list_backups(install) if b.auto][KEEP_AUTO:]:
            old.path.unlink(missing_ok=True)
    return Backup(path, when, auto)


def list_backups(install: Install) -> list[Backup]:
    """Newest first. Names start with the time they were taken, so they sort by it."""
    out = []
    for p in sorted(folder(install).glob("*.zip"), reverse=True):
        try:
            when = time.mktime(time.strptime(p.stem[:15], "%Y%m%d-%H%M%S"))
        except ValueError:
            when = p.stat().st_mtime
        out.append(Backup(p, when, p.stem.endswith("-auto")))
    return out


def contents(backup: Backup) -> tuple[bool, list[str]]:
    """Whether it holds addons.txt, and the addons whose settings it holds."""
    with zipfile.ZipFile(backup.path) as zf:
        names = zf.namelist()
    addons = sorted({n.split("/", 1)[0] for n in names if f"/{installer.DATA_DIR}/" in n}, key=str.lower)
    return "addons.txt" in names, addons


def restore(install: Install, backup: Backup) -> Backup:
    """Put back addons.txt and the .data folders of addons that are installed. Settings of addons
    that aren't installed wait in the kept-settings folder, where a reinstall picks them up.
    A snapshot of the current state is taken first and returned, so a restore can be undone."""
    safety = create(install)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(backup.path) as zf:
            installer._safe_extract(zf, root, "the backup")
        if (root / "addons.txt").is_file():
            installer.write_enabled(install, installer.read_enabled(Install(root, root)))
        for src in sorted(p for p in root.iterdir() if (p / installer.DATA_DIR).is_dir()):
            target = install.addon_dir / src.name
            if not target.is_dir():
                target = install.addon_dir / ".aacam-kept" / src.name
                shutil.rmtree(target, ignore_errors=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src / installer.DATA_DIR), str(target))
                continue
            shutil.rmtree(target / installer.DATA_DIR, ignore_errors=True)
            shutil.move(str(src / installer.DATA_DIR), str(target / installer.DATA_DIR))
    return safety
