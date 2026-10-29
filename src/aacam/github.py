"""Minimal GitHub client: latest release, branch head, file downloads."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

API = "https://api.github.com"
USER_AGENT = f"aacam/{__version__}"


class GitHubError(RuntimeError):
    pass


def _request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def get_json(url: str) -> object:
    try:
        with urllib.request.urlopen(_request(url), timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubError("GitHub rate limit reached; set GITHUB_TOKEN to raise it") from e
        raise GitHubError(f"GitHub returned {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"could not reach GitHub: {e.reason}") from e


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(_request(url, accept="*/*"), timeout=120) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        raise GitHubError(f"download failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"could not reach GitHub: {e.reason}") from e
    return dest


@dataclass(frozen=True)
class Release:
    version: str  # release tag, or "<branch>@<short sha>" when the addon has no releases
    zip_url: str
    commit: str | None = None


def latest_release(repo: str, branch: str) -> Release:
    """Latest published release, falling back to the branch head for addons without releases."""
    try:
        data = get_json(f"{API}/repos/{repo}/releases/latest")
        assert isinstance(data, dict)
        tag = data["tag_name"]
        # Name the tag explicitly: the API's zipball_url answers 300 when a tag
        # shares its name with a branch (one addon tags its releases "main").
        return Release(tag, f"https://codeload.github.com/{repo}/zip/refs/tags/{urllib.parse.quote(tag)}")
    except GitHubError as e:
        if "returned 404" not in str(e):
            raise
    data = get_json(f"{API}/repos/{repo}/commits/{branch}")
    assert isinstance(data, dict)
    sha = data["sha"]
    return Release(f"{branch}@{sha[:7]}", f"https://codeload.github.com/{repo}/zip/{sha}", sha)
