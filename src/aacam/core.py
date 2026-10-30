"""Operations shared by the command line and the desktop app."""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import backup, github, installer, paths
from .config import InstalledDb, Settings, data_dir
from .registry import Addon, Registry


class NotFound(LookupError):
    pass


REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


def repo_folder_name(repo: str) -> str:
    """Default addon folder for a repo: Z-arium/aac-dps-meter -> dps_meter."""
    name = repo.split("/")[1]
    name = re.sub(r"^(aac|archeage)[-_]", "", name, flags=re.IGNORECASE)
    return re.sub(r"[-.]", "_", name)


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
        self.check_errors: dict[str, str] = {}  # addon -> why its last update check failed

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

    def archive_dir(self, name: str) -> Path:
        """Zips of the installed and the previous version of one addon, for rolling back."""
        return data_dir() / "archives" / backup.folder(self.install).name / name

    def _keep_archive(self, name: str, archive: Path, version: str) -> str:
        """Store the zip just installed; _prune_archives keeps only it and the one before."""
        keep = self.archive_dir(name)
        keep.mkdir(parents=True, exist_ok=True)
        dest = keep / (re.sub(r"[^A-Za-z0-9._@-]", "_", version) + ".zip")
        if archive.resolve() != dest.resolve():
            shutil.copy2(archive, dest)
        return str(dest)

    def install_one(self, addon: Addon, release: github.Release | None = None, from_repo: bool = False) -> github.Release:
        release = release or github.latest_release(addon.repo, addon.branch)
        with tempfile.TemporaryDirectory() as tmp:
            archive = github.download(release.zip_url, Path(tmp) / f"{addon.name}.zip")
            installer.install_zip(self.install, addon.name, archive)
            kept = self._keep_archive(addon.name, archive, release.version)
        installer.restore_kept_data(self.install, addon.name)
        installer.enable(self.install, addon.name)
        old = self.db.addons.get(addon.name) or {}
        rec = {
            "version": release.version,
            "repo": addon.repo,
            "commit": release.commit,
            "installed_at": int(time.time()),
            "archive": kept,
        }
        if release.published:
            rec["published"] = release.published
        if old.get("archive") and old.get("version") != release.version:
            rec["previous"] = {k: old.get(k) for k in ("version", "commit", "archive", "published")}
        elif old.get("previous"):
            rec["previous"] = old["previous"]
        if from_repo or old.get("source") == "repo":
            rec.update(source="repo", branch=addon.branch)
        elif old.get("catalogue"):
            rec["catalogue"] = old["catalogue"]
        self.db.addons[addon.name] = rec
        self._prune_archives(addon.name)
        self.db.save()
        return release

    def _prune_archives(self, name: str) -> None:
        rec = self.db.addons.get(name) or {}
        wanted = {Path(p).name for p in (rec.get("archive"), (rec.get("previous") or {}).get("archive")) if p}
        for old in self.archive_dir(name).glob("*.zip"):
            if old.name not in wanted:
                old.unlink(missing_ok=True)

    def can_roll_back(self, name: str) -> str | None:
        """The version a roll back would return to, if its zip was kept."""
        prev = (self.db.addons.get(name) or {}).get("previous") or {}
        return prev.get("version") if prev.get("archive") and Path(prev["archive"]).is_file() else None

    def roll_back(self, name: str) -> str:
        """Reinstall the previous version from its kept zip; settings stay. Rolling back again goes forward."""
        rec = self.db.addons.get(name)
        if not rec or not self.can_roll_back(name):
            raise installer.InstallError(f"no earlier version of {name} is kept; only updates made with v0.4 or later can be rolled back")
        prev = rec["previous"]
        installer.install_zip(self.install, name, Path(prev["archive"]))
        current = {k: rec.get(k) for k in ("version", "commit", "archive", "published")}
        rec.update({k: prev.get(k) for k in ("version", "commit", "archive", "published")})
        rec["previous"] = current
        rec["installed_at"] = int(time.time())
        rec["pinned"] = True  # don't offer the version just rolled back from as an update
        self.db.save()
        return rec["version"]

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

    def add_repo(self, repo: str, name: str | None = None, branch: str = "main") -> tuple[Addon, github.Release]:
        """Install an addon straight from a GitHub repo that isn't in the catalogue."""
        if not REPO_RE.fullmatch(repo):
            raise NotFound(f"{repo} (expected owner/repo)")
        name = name or repo_folder_name(repo)
        addon = Addon(name, name, "", repo.split("/")[0], repo, branch)
        return addon, self.install_one(addon, from_repo=True)

    def addon_for(self, reg: Registry, name: str) -> Addon | None:
        """Catalogue entry for an installed addon, or its repo if it was installed with --repo."""
        rec = self.db.addons.get(name) or {}
        if rec.get("source") == "repo":
            return Addon(name, name, "", rec["repo"].split("/")[0], rec["repo"], rec.get("branch") or "main")
        addon = reg.get(rec.get("catalogue") or name)
        if addon is not None and addon.name != name:
            # Install into the folder the player already has, not the catalogue's name.
            addon = Addon(name, addon.alias, addon.description, addon.author, addon.repo, addon.branch,
                          addon.tags, addon.dependencies)
        return addon

    def check_updates(self, reg: Registry, names: list[str] | None = None) -> dict[str, github.Release]:
        """Managed addons whose latest release differs from the installed one."""
        out = {}
        self.check_errors: dict[str, str] = {}
        for name in names or sorted(self.db.addons):
            addon = self.addon_for(reg, name)
            if addon is None or name not in self.db.addons:
                continue
            try:
                release = github.latest_release(addon.repo, addon.branch)
            except (github.RateLimited, github.Offline):
                raise  # every other addon would fail the same way
            except github.GitHubError as e:
                self.check_errors[name] = str(e)  # one broken repo doesn't stop the rest
                continue
            rec = self.db.addons[name]
            if rec.get("pinned") and release.version == (rec.get("previous") or {}).get("version"):
                continue  # rolled back from this version on purpose; offer the next one
            if release.version != rec["version"]:
                out[name] = release
        return out

    def untracked(self, reg: Registry) -> dict[str, Addon]:
        """Addon folders put there by hand or by another manager that match a catalogue entry,
        by folder name or the addon's display name, ignoring case."""
        out = {}
        for name in installer.local_addons(self.install):
            if name in self.db.addons:
                continue
            addon = reg.get(name) or next(
                (a for a in reg.addons.values() if a.alias.lower() == name.lower()), None)
            if addon is not None:
                out[name] = addon
        return out

    def track(self, name: str, addon: Addon) -> None:
        """Start updating a folder we didn't install. Its version is unknown, so the next
        check offers the latest release; .data and addons.txt stay as they are."""
        if not (self.install.addon_dir / name / "main.lua").is_file():
            raise NotFound(name)
        self.db.addons[name] = {
            "version": "unknown",
            "repo": addon.repo,
            "commit": None,
            "installed_at": int(time.time()),
            "source": "tracked",
        }
        if addon.name != name:
            # Folder name differs from the catalogue's (e.g. different case): remember the entry.
            self.db.addons[name]["catalogue"] = addon.name
        self.db.save()

    def changelog(self, reg: Registry, name: str, limit: int = 10) -> list[github.Release]:
        """Releases newer than the installed one, newest first (all recent ones if not installed)."""
        addon = self.addon_for(reg, name)
        if addon is None:
            raise NotFound(name)
        installed = (self.db.addons.get(name) or {}).get("version")
        out = []
        for rel in github.releases(addon.repo, addon.branch, limit):
            if rel.version == installed:
                break
            out.append(rel)
        return out

    def remove(self, name: str, purge: bool = False) -> None:
        installer.remove(self.install, name, purge=purge)
        self.db.addons.pop(name, None)
        self.db.save()
        shutil.rmtree(self.archive_dir(name), ignore_errors=True)
