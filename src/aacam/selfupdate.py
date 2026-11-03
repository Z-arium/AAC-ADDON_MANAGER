"""Keep the launcher itself up to date.

The AppImage replaces itself with the newest release from GitHub, after checking the file
against the release's SHA256SUMS (and, when `cosign` is installed, the checksums' Sigstore
signature from our release workflow). The AppImage also carries zsync update information, so
AppImageUpdate and Gear Lever can update it too. Installs from the AUR, Flatpak or install.sh
are updated by their own tool, so for those we only say how.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__, github, host

REPO = "Z-arium/AAC-ADDON_MANAGER"
ASSET = "ArcheAge-Classic-Linux-Launcher-x86_64.AppImage"
SUMS = "SHA256SUMS"
BUNDLE = "SHA256SUMS.sigstore.json"
# The only signer we accept: our release workflow, run by GitHub Actions for this repository.
SIGNER = r"(?i)^https://github\.com/Z-arium/AAC-ADDON_MANAGER/\.github/workflows/release\.yml@"
ISSUER = "https://token.actions.githubusercontent.com"
AUR_PACKAGE = "archeage-classic-linux-launcher-bin"
AUR_DIR = Path("/opt/archeage-classic-linux-launcher")
FLATPAK_ID = "io.github.z_arium.ArcheAgeClassicLauncher"

log = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Update:
    version: str
    notes: str
    page: str  # the release on GitHub
    assets: dict[str, str]  # file name -> download URL


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version.split("-")[0]))


def newer(tag: str, current: str = __version__) -> bool:
    try:
        return _parts(tag) > _parts(current)
    except ValueError:
        return False


def kind() -> str:
    """How this launcher was installed: appimage, aur, flatpak or python (install.sh / pip)."""
    if host.FLATPAK:
        return "flatpak"
    if os.environ.get("APPIMAGE") and Path(os.environ["APPIMAGE"]).is_file():
        return "appimage"
    exe = Path(sys.executable).resolve()
    if AUR_DIR in exe.parents:
        return "aur"
    return "python"


def appimage() -> Path | None:
    path = os.environ.get("APPIMAGE")
    return Path(path) if path and Path(path).is_file() else None


def how_to_update(how: str | None = None) -> str:
    how = how or kind()
    if how == "aur":
        return f"Update it with your AUR helper, e.g. `yay -Syu {AUR_PACKAGE}` or `paru -Syu`."
    if how == "flatpak":
        return f"Update it with `flatpak update {FLATPAK_ID}` or your software centre."
    if how == "python":
        return "Run install.sh again from a fresh copy of the repository, or download the AppImage."
    return "Press Update to replace this AppImage with the new one."


def latest() -> Update:
    """The newest release of the launcher on GitHub."""
    data = github.get_json(f"{github.API}/repos/{REPO}/releases/latest")
    if not isinstance(data, dict) or "tag_name" not in data:
        raise UpdateError("GitHub's answer about the launcher's releases wasn't understood")
    return Update(data["tag_name"], (data.get("body") or "").strip(), data.get("html_url") or "",
                  {a["name"]: a["browser_download_url"] for a in data.get("assets") or [] if "name" in a})


def check() -> Update | None:
    """The newest release when it is newer than this launcher."""
    update = latest()
    return update if newer(update.version) else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output: file name -> hash."""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


def verify_signature(sums: Path, bundle: Path) -> bool | None:
    """True or False when cosign is installed and checked the signature; None when it isn't installed."""
    cosign = shutil.which("cosign")
    if not cosign:
        return None
    result = subprocess.run(
        [cosign, "verify-blob", "--bundle", str(bundle), "--certificate-identity-regexp", SIGNER,
         "--certificate-oidc-issuer", ISSUER, str(sums)], capture_output=True, text=True, timeout=120)
    if result.returncode:
        log.warning("cosign rejected %s: %s", sums, result.stderr.strip())
    return result.returncode == 0


def install(update: Update, target: Path | None = None) -> Path:
    """Download the release's AppImage, check it, and put it in place of `target` (this AppImage).

    The previous AppImage is kept next to it as `<name>.old`. The running copy keeps working
    until the launcher restarts.
    """
    target = target or appimage()
    if target is None:
        raise UpdateError(f"this launcher isn't running from an AppImage. {how_to_update()}")
    if not os.access(target.parent, os.W_OK) or not os.access(target, os.W_OK):
        raise UpdateError(f"can't write to {target}; download the new AppImage from {update.page}")
    if ASSET not in update.assets:
        raise UpdateError(f"{update.version} has no AppImage attached; see {update.page}")
    if SUMS not in update.assets:
        raise UpdateError(f"{update.version} has no {SUMS} to check the download against; see {update.page}")

    with tempfile.TemporaryDirectory(prefix="aacam-update-") as tmp:
        sums = github.download(update.assets[SUMS], Path(tmp) / SUMS)
        expected = parse_sums(sums.read_text(errors="replace")).get(ASSET)
        if not expected:
            raise UpdateError(f"{SUMS} of {update.version} doesn't list {ASSET}")
        if BUNDLE in update.assets:
            bundle = github.download(update.assets[BUNDLE], Path(tmp) / BUNDLE)
            if verify_signature(sums, bundle) is False:
                raise UpdateError(f"the signature of {update.version}'s checksums is not from our release "
                                  "workflow; not updating. Please report this.")
        # Download next to the target so the final rename stays on one filesystem.
        fd, part = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
        os.close(fd)
        new = Path(part)
        try:
            github.download(update.assets[ASSET], new)
            got = sha256(new)
            if got != expected:
                raise UpdateError(f"the downloaded AppImage doesn't match {SUMS} (got {got[:12]}…, "
                                  f"expected {expected[:12]}…); not updating")
            new.chmod(0o755)
            old = target.with_name(target.name + ".old")
            shutil.copy2(target, old)
            os.replace(new, target)
        except BaseException:
            new.unlink(missing_ok=True)
            raise
    log.info("updated %s to %s", target, update.version)
    return target


def restart(target: Path) -> None:
    """Start the new AppImage, with the environment the old one got from the desktop."""
    from . import official  # late: official imports paths, which imports host; keep this module light

    subprocess.Popen([str(target)], env=official.environment(True), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
