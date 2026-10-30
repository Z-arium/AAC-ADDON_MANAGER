"""Minimal GitHub client: latest release, branch head, file downloads.

API answers are cached with their ETags, so a repeat lookup is a conditional request: GitHub
answers 304 Not Modified, which doesn't count against the 60 calls an hour allowed without a token.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__, config, net

API = "https://api.github.com"
USER_AGENT = f"aacam/{__version__}"
FRESH = 300  # seconds an API answer is reused without asking GitHub again
TOKEN_HINT = "add a GitHub token in Setup (or `aacam token --set`)"

log = logging.getLogger(__name__)
_lock = threading.Lock()


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset: float | None, authed: bool) -> None:
        self.reset = reset
        when = f"try again after {time.strftime('%H:%M', time.localtime(reset))}" if reset else "try again in an hour"
        if authed:
            super().__init__(f"GitHub's rate limit for your token is used up; {when}.")
        else:
            super().__init__(f"GitHub's limit of 60 lookups an hour without a token is used up; {TOKEN_HINT}, or {when}.")


class Offline(GitHubError):
    pass


def token() -> str | None:
    """GITHUB_TOKEN or GH_TOKEN from the environment, else the one saved in the settings."""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or config.Settings().get("github_token") or None


def _request(url: str, accept: str = "application/vnd.github+json", etag: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    tok = token()
    if tok and url.startswith(API):
        headers["Authorization"] = f"Bearer {tok}"
    if etag:
        headers["If-None-Match"] = etag
    return urllib.request.Request(url, headers=headers)


def network_problem(reason: object) -> str:
    """Plain words for a failed connection."""
    text = str(reason)
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "Temporary failure in name" in text:
        return "could not reach GitHub: no internet connection, or DNS isn't working"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "could not reach GitHub: the connection timed out; check your internet connection and try again"
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return ("could not reach GitHub: the secure connection could not be verified; install your distro's "
                "ca-certificates package, or run `aacam doctor --network`")
    if isinstance(reason, ConnectionRefusedError) or "Connection refused" in text:
        return "could not reach GitHub: the connection was refused (a firewall or proxy may be blocking it)"
    return f"could not reach GitHub: {text}"


def _http_error(e: urllib.error.HTTPError, url: str) -> GitHubError:
    remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
    if e.code in (403, 429) and (remaining == "0" or e.code == 429):
        reset = e.headers.get("X-RateLimit-Reset")
        return RateLimited(float(reset) if reset and reset.isdigit() else None, bool(token()))
    if e.code == 401:
        return GitHubError("GitHub rejected the saved token (401); check it or clear it in Setup")
    if e.code >= 500:
        return GitHubError(f"GitHub is having trouble ({e.code}); try again in a few minutes")
    return GitHubError(f"GitHub returned {e.code} for {url}")


# --- cached API answers ---------------------------------------------------------

def _cache_path() -> Path:
    return config.cache_dir() / "github-api.json"


def _cache_load() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _cache_put(url: str, entry: dict) -> None:
    with _lock:
        cache = _cache_load()
        cache[url] = entry
        try:
            config.write_atomic(_cache_path(), json.dumps(cache).encode())
        except OSError:
            pass  # a cache that can't be written only costs API calls


def clear_cache() -> None:
    _cache_path().unlink(missing_ok=True)


def get_json(url: str, fresh: float = FRESH) -> object:
    """GET an API URL. Recent answers are reused; older ones are revalidated with their ETag."""
    with _lock:
        cached = _cache_load().get(url)
    now = time.time()
    if cached and now - cached.get("time", 0) < fresh:
        if cached.get("status") == 404:
            raise GitHubError(f"GitHub returned 404 for {url}")
        return cached["body"]
    etag = cached.get("etag") if cached and cached.get("status") == 200 else None
    try:
        with net.urlopen(_request(url, etag=etag), timeout=30) as resp:
            body = json.load(resp)
            log.info("GET %s -> %s (left %s)", url, resp.status, resp.headers.get("X-RateLimit-Remaining"))
            _cache_put(url, {"status": 200, "etag": resp.headers.get("ETag"), "body": body, "time": now})
            return body
    except urllib.error.HTTPError as e:
        log.info("GET %s -> %s (left %s)", url, e.code, e.headers.get("X-RateLimit-Remaining") if e.headers else "?")
        if e.code == 304 and cached:
            cached["time"] = now
            _cache_put(url, cached)
            return cached["body"]
        if e.code == 404:
            _cache_put(url, {"status": 404, "time": now})
        err = _http_error(e, url)
        if isinstance(err, RateLimited) and cached and cached.get("status") == 200:
            log.warning("rate limited, using the cached answer for %s", url)
            return cached["body"]
        raise err from e
    except urllib.error.URLError as e:
        log.warning("GET %s failed: %s", url, e.reason)
        if cached and cached.get("status") == 200:
            return cached["body"]  # offline: an older answer beats none
        raise Offline(network_problem(e.reason)) from e
    except (TimeoutError, OSError) as e:
        log.warning("GET %s failed: %s", url, e)
        if cached and cached.get("status") == 200:
            return cached["body"]
        raise Offline(network_problem(e)) from e
    except ValueError as e:
        raise GitHubError(f"GitHub sent an answer that isn't valid JSON for {url}") from e


def rate_limit() -> dict:
    """Calls left this hour: {"limit", "remaining", "reset"}; costs nothing against the limit."""
    try:
        with net.urlopen(_request(f"{API}/rate_limit"), timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise _http_error(e, f"{API}/rate_limit") from e
    except (urllib.error.URLError, OSError) as e:
        raise Offline(network_problem(getattr(e, "reason", e))) from e
    return data.get("resources", {}).get("core") or data.get("rate", {})


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with net.urlopen(_request(url, accept="*/*"), timeout=120) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        log.warning("download %s -> %s", url, e.code)
        if e.code == 404:
            raise GitHubError(f"the download is gone (404): {url}; the addon's author may have removed that release") from e
        err = _http_error(e, url)
        if isinstance(err, RateLimited):
            raise err from e
        raise GitHubError(f"download failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        log.warning("download %s failed: %s", url, e.reason)
        raise Offline(network_problem(e.reason)) from e
    except (TimeoutError, ConnectionError) as e:
        log.warning("download %s failed: %s", url, e)
        raise Offline(network_problem(e)) from e
    return dest


@dataclass(frozen=True)
class Release:
    version: str  # release tag, or "<branch>@<short sha>" when the addon has no releases
    zip_url: str
    commit: str | None = None
    notes: str = ""  # release notes, or the commit message for addons without releases
    published: str = ""  # ISO 8601 date
    downloads: int | None = None  # downloads of the release's attached files, when it has any


def _from_release(repo: str, data: dict) -> Release:
    tag = data["tag_name"]
    assets = data.get("assets") or []
    return Release(
        tag,
        # Name the tag explicitly: the API's zipball_url answers 300 when a tag
        # shares its name with a branch (one addon tags its releases "main").
        f"https://codeload.github.com/{repo}/zip/refs/tags/{urllib.parse.quote(tag)}",
        notes=(data.get("body") or "").strip(),
        published=data.get("published_at") or "",
        downloads=sum(a.get("download_count", 0) for a in assets) if assets else None,
    )


def latest_release(repo: str, branch: str) -> Release:
    """Latest published release, falling back to the branch head for addons without releases."""
    try:
        data = get_json(f"{API}/repos/{repo}/releases/latest")
        assert isinstance(data, dict)
        return _from_release(repo, data)
    except GitHubError as e:
        if "returned 404" not in str(e):
            raise
    try:
        data = get_json(f"{API}/repos/{repo}/commits/{branch}")
    except GitHubError as e:
        if "returned 404" in str(e) or "returned 422" in str(e):
            raise GitHubError(f"https://github.com/{repo} has no releases and no {branch} branch; "
                              "it may have been renamed or deleted") from e
        raise
    assert isinstance(data, dict)
    sha = data["sha"]
    commit = data.get("commit") or {}
    return Release(f"{branch}@{sha[:7]}", f"https://codeload.github.com/{repo}/zip/{sha}", sha,
                   notes=(commit.get("message") or "").strip(),
                   published=(commit.get("committer") or {}).get("date") or "")


def releases(repo: str, branch: str, limit: int = 10) -> list[Release]:
    """Newest first: published releases, or recent commits for addons without releases. One API call."""
    data = get_json(f"{API}/repos/{repo}/releases?per_page={limit}")
    assert isinstance(data, list)
    out = [_from_release(repo, d) for d in data if not d.get("draft") and not d.get("prerelease")]
    if out or data:
        return out
    commits = get_json(f"{API}/repos/{repo}/commits?sha={urllib.parse.quote(branch)}&per_page={limit}")
    assert isinstance(commits, list)
    return [Release(f"{branch}@{c['sha'][:7]}", f"https://codeload.github.com/{repo}/zip/{c['sha']}", c["sha"],
                    notes=(c.get("commit", {}).get("message") or "").strip(),
                    published=(c.get("commit", {}).get("committer") or {}).get("date") or "")
            for c in commits]


def cached_release(repo: str) -> Release | None:
    """The latest release from the cache only, without asking GitHub (for lists of many addons)."""
    with _lock:
        entry = _cache_load().get(f"{API}/repos/{repo}/releases/latest")
    if not entry or entry.get("status") != 200:
        return None
    try:
        return _from_release(repo, entry["body"])
    except (KeyError, TypeError):
        return None
