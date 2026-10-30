"""File operations on the game's addon directory."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .config import write_atomic
from .paths import Install

# Per-addon saved data that survives updates, same convention as Classic Addon Manager.
DATA_DIR = ".data"
# Folder the other manager injects; never listed or touched as a user addon.
IGNORED = {"AddonUpdateNotification"}


class InstallError(RuntimeError):
    pass


# --- addons.txt ------------------------------------------------------------

def read_enabled(install: Install) -> list[str]:
    try:
        text = install.addons_txt.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def write_enabled(install: Install, names: list[str]) -> None:
    txt = install.addons_txt
    if txt.exists():
        shutil.copy2(txt, txt.with_name("addons.txt.bak"))
    write_atomic(txt, "\n".join(names).encode())


def enable(install: Install, name: str) -> bool:
    names = read_enabled(install)
    if name in names:
        return False
    write_enabled(install, names + [name])
    return True


def disable(install: Install, name: str) -> bool:
    names = read_enabled(install)
    if name not in names:
        return False
    write_enabled(install, [n for n in names if n != name])
    return True


# --- addon folders ---------------------------------------------------------

def local_addons(install: Install) -> list[str]:
    """Folders in the addon dir that look like addons (contain main.lua)."""
    if not install.addon_dir.is_dir():
        return []
    return sorted(
        (p.name for p in install.addon_dir.iterdir()
         if p.is_dir() and not p.name.startswith(".") and p.name not in IGNORED
         and (p / "main.lua").is_file()),
        key=str.lower,
    )


def _safe_extract(zf: zipfile.ZipFile, dest: Path, name: str) -> None:
    for info in zf.infolist():
        parts = PurePosixPath(info.filename.replace("\\", "/")).parts
        if info.filename.startswith(("/", "\\")) or ".." in parts:
            raise InstallError(f"refused {name}: its zip would write outside the addon folder ({info.filename})")
    zf.extractall(dest)


def _release_root(extracted: Path, name: str) -> Path:
    """The one directory in the archive whose top level holds main.lua."""
    if (extracted / "main.lua").is_file():
        return extracted
    roots = [p for p in extracted.iterdir() if p.is_dir() and (p / "main.lua").is_file()]
    if not roots:
        raise InstallError(f"{name} isn't an ArcheAge addon: its zip has no main.lua at the top or in one folder")
    if len(roots) > 1:
        raise InstallError(f"{name}'s zip holds {len(roots)} addons ({', '.join(sorted(p.name for p in roots))}); "
                           "unpack it and import each folder's zip on its own")
    return roots[0]


def install_zip(install: Install, name: str, archive: Path) -> Path:
    """Unpack archive into addon_dir/name, swapping atomically and keeping .data."""
    dest = install.addon_dir / name
    install.addon_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=install.addon_dir, prefix=f".{name}.staging-") as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(archive) as zf:
                _safe_extract(zf, tmp_path / "x", name)
        except (zipfile.BadZipFile, EOFError) as e:
            raise InstallError(f"the zip for {name} is broken or incomplete ({archive.name}); "
                               "try again, and tell the addon's author if it keeps happening") from e
        except FileNotFoundError as e:
            raise InstallError(f"no such file: {archive}") from e
        stage = _release_root(tmp_path / "x", name)
        if (dest / DATA_DIR).is_dir():
            shutil.rmtree(stage / DATA_DIR, ignore_errors=True)
            shutil.copytree(dest / DATA_DIR, stage / DATA_DIR)
        backup = tmp_path / "old"
        if dest.exists():
            dest.rename(backup)
        try:
            stage.rename(dest)
        except OSError:
            if backup.exists():
                backup.rename(dest)
            raise
    return dest


def remove(install: Install, name: str, purge: bool = False) -> None:
    dest = install.addon_dir / name
    if not dest.is_dir():
        raise InstallError(f"{name} is not installed")
    data = dest / DATA_DIR
    if data.is_dir() and not purge:
        keep = install.addon_dir / ".aacam-kept" / name
        shutil.rmtree(keep, ignore_errors=True)
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data), str(keep))
    shutil.rmtree(dest)
    disable(install, name)


def restore_kept_data(install: Install, name: str) -> bool:
    """Put back .data saved by a previous remove, if any."""
    keep = install.addon_dir / ".aacam-kept" / name
    dest = install.addon_dir / name / DATA_DIR
    if not keep.is_dir() or dest.exists():
        return False
    shutil.move(str(keep), str(dest))
    return True
