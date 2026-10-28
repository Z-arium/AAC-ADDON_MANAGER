"""The public addon catalogue from github.com/classic-addon-manager/addons."""

from __future__ import annotations

import io
import json
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import config, github

REGISTRY_REPO = "classic-addon-manager/addons"
REGISTRY_BRANCH = "master"
MAX_AGE = 3600  # seconds before the cached catalogue is refreshed


@dataclass
class Addon:
    name: str
    alias: str
    description: str
    author: str
    repo: str
    branch: str
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, data: dict) -> Addon:
        return cls(
            name=str(data["name"]),
            alias=str(data.get("alias") or data["name"]),
            description=str(data.get("description") or "").strip(),
            author=str(data.get("author") or ""),
            repo=str(data["repo"]),
            branch=str(data.get("branch") or "main"),
            tags=[str(t) for t in data.get("tags") or []],
            dependencies=[str(d) for d in data.get("dependencies") or []],
        )


def parse_tarball(blob: bytes) -> dict[str, Addon]:
    addons: dict[str, Addon] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith((".yaml", ".yml")):
                continue
            if "/.github/" in member.name:
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            try:
                data = yaml.safe_load(f.read())
                addon = Addon.from_yaml(data)
            except (yaml.YAMLError, KeyError, TypeError):
                continue  # skip a malformed declaration rather than the whole catalogue
            addons[addon.name] = addon
    return addons


class Registry:
    def __init__(self, addons: dict[str, Addon]) -> None:
        self.addons = addons
        self._lower = {k.lower(): v for k, v in addons.items()}

    @classmethod
    def load(cls, refresh: bool = False) -> Registry:
        cache = config.cache_dir() / "registry.json"
        if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < MAX_AGE:
            raw = json.loads(cache.read_text())
            return cls({k: Addon(**v) for k, v in raw.items()})
        url = f"https://codeload.github.com/{REGISTRY_REPO}/tar.gz/refs/heads/{REGISTRY_BRANCH}"
        try:
            tmp = github.download(url, config.cache_dir() / "registry.tar.gz")
            addons = parse_tarball(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
        except github.GitHubError:
            if cache.exists():  # offline: use the stale copy
                raw = json.loads(cache.read_text())
                return cls({k: Addon(**v) for k, v in raw.items()})
            raise
        config.write_atomic(cache, json.dumps({k: asdict(v) for k, v in addons.items()}).encode())
        return cls(addons)

    def get(self, name: str) -> Addon | None:
        return self.addons.get(name) or self._lower.get(name.lower())

    def search(self, text: str) -> list[Addon]:
        text = text.lower()
        hits = [
            a for a in self.addons.values()
            if text in a.name.lower() or text in a.alias.lower() or text in a.description.lower()
            or any(text == t.lower() for t in a.tags)
        ]
        return sorted(hits, key=lambda a: a.name.lower())

    def resolve(self, name: str) -> list[Addon]:
        """The addon and its dependencies, dependencies first."""
        order: list[Addon] = []
        visiting: set[str] = set()

        def visit(n: str) -> None:
            addon = self.get(n)
            if addon is None:
                raise KeyError(n)
            if addon in order or addon.name in visiting:
                return
            visiting.add(addon.name)
            for dep in addon.dependencies:
                visit(dep)
            order.append(addon)

        visit(name)
        return order
