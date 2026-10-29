"""Operations shared by the command line and the desktop app."""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import github, installer, paths
from .config import InstalledDb, Settings
from .registry import Addon, Registry


class NotFound(LookupError):
    pass


@dataclass
class LocalAddon:
    name: str
    enabled: bool
    version: str  # "manual" when not installed through aacam
    managed: bool  # installed from the catalogue, so it can be updated


class Manager:
    """One addon folder plus the catalogue and the record of what aacam installed."""

    def __init__(self, install: paths.Install) -> None:
        self.install = install
        self.db = InstalledDb(install.addon_dir)

    @classmethod
    def from_settings(cls, settings: Settings) -> Manager | None:
        """The saved addon folder, or the best detected one (saved for next time)."""
        saved = settings.get("addon_dir")
        if saved:
            return cls(paths.resolve_user_path(Path(saved)))
        found = paths.detect()
        if not found:
            return None
        settings.set("addon_dir", str(found[0].addon_dir))
        return cls(found[0])

    def local(self) -> list[LocalAddon]:
        enabled = set(installer.read_enabled(self.install))
        out = []
        for name in installer.local_addons(self.install):
            rec = self.db.addons.get(name)
            out.append(LocalAddon(name, name in enabled, rec["version"] if rec else "manual", rec is not None))
        return out

    def missing(self) -> list[str]:
        """Names in addons.txt with no folder."""
        enabled = set(installer.read_enabled(self.install))
        return sorted(enabled - set(installer.local_addons(self.install)) - installer.IGNORED)

    def install_one(self, addon: Addon, release: github.Release | None = None) -> github.Release:
        release = release or github.latest_release(addon.repo, addon.branch)
        with tempfile.TemporaryDirectory() as tmp:
            archive = github.download(release.zip_url, Path(tmp) / f"{addon.name}.zip")
            installer.install_zip(self.install, addon.name, archive)
        installer.restore_kept_data(self.install, addon.name)
        installer.enable(self.install, addon.name)
        self.db.addons[addon.name] = {
            "version": release.version,
            "repo": addon.repo,
            "commit": release.commit,
            "installed_at": int(time.time()),
        }
        self.db.save()
        return release

    def add(self, reg: Registry, name: str) -> list[tuple[Addon, github.Release]]:
        """Install name and any dependencies not already present; returns what was installed."""
        try:
            chain = reg.resolve(name)
        except KeyError as e:
            raise NotFound(e.args[0]) from None
        done = []
        for addon in chain:
            present = addon.name in self.db.addons and (self.install.addon_dir / addon.name).is_dir()
            if present and addon is not chain[-1]:
                continue
            done.append((addon, self.install_one(addon)))
        return done

    def check_updates(self, reg: Registry, names: list[str] | None = None) -> dict[str, github.Release]:
        """Managed addons whose latest release differs from the installed one."""
        out = {}
        for name in names or sorted(self.db.addons):
            addon = reg.get(name)
            if addon is None or name not in self.db.addons:
                continue
            release = github.latest_release(addon.repo, addon.branch)
            if release.version != self.db.addons[name]["version"]:
                out[name] = release
        return out

    def remove(self, name: str, purge: bool = False) -> None:
        installer.remove(self.install, name, purge=purge)
        self.db.addons.pop(name, None)
        self.db.save()
