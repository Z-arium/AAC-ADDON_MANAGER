"""User settings and the record of installed addons, stored as JSON under XDG dirs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "aacam"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(base) / "aacam"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(base) / "aacam"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "aacam"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _save(path: Path, value: dict[str, Any]) -> None:
    write_atomic(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


class Settings:
    """`addon_dir` is where addons go; `official_launcher` is the official AppImage;
    `launch` is a custom command that starts the game instead of it."""

    def __init__(self) -> None:
        self.path = config_dir() / "config.json"
        self.values = _load(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value
        _save(self.path, self.values)


class InstalledDb:
    """Addons this tool installed into one addon dir: name -> {version, repo, commit, installed_at}."""

    def __init__(self, addon_dir: Path) -> None:
        self.path = data_dir() / "installed.json"
        self.key = str(addon_dir)
        self._all = _load(self.path)
        self.addons: dict[str, dict[str, Any]] = self._all.setdefault(self.key, {})

    def save(self) -> None:
        _save(self.path, self._all)
