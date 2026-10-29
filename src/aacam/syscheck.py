"""Check that this Linux system has what the official launcher and the game need, and say how to fix it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"

# Library folders across distros. /usr/lib holds 64-bit libraries on Arch and
# 32-bit ones on Fedora and openSUSE, so each hit is checked by its ELF class.
LIB_DIRS = (
    "usr/lib64", "usr/lib32", "usr/lib", "lib64", "lib32", "lib",
    "usr/lib/x86_64-linux-gnu", "usr/lib/i386-linux-gnu", "lib/x86_64-linux-gnu", "lib/i386-linux-gnu",
)
ICD_DIRS = ("usr/share/vulkan/icd.d", "etc/vulkan/icd.d")


@dataclass(frozen=True)
class Distro:
    id: str
    name: str
    family: str  # arch, steamos, debian, fedora, suse or unknown
    version: str = ""


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    status: str  # OK, WARN or FAIL
    detail: str
    fix: str | None = None  # shell command that fixes it on this distro


def distro(root: Path = Path("/")) -> Distro:
    values: dict[str, str] = {}
    for name in ("etc/os-release", "usr/lib/os-release"):
        try:
            text = (root / name).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip().strip('"')
        break
    ident = values.get("ID", "linux").lower()
    like = {ident, *values.get("ID_LIKE", "").lower().split()}
    if ident == "steamos":
        family = "steamos"
    elif like & {"arch", "archlinux"}:
        family = "arch"
    elif like & {"debian", "ubuntu"}:
        family = "debian"
    elif like & {"fedora", "rhel", "centos"}:
        family = "fedora"
    elif like & {"suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap"}:
        family = "suse"
    else:
        family = "unknown"
    return Distro(ident, values.get("PRETTY_NAME") or values.get("NAME") or ident, family, values.get("VERSION_ID", ""))


def _elf_bits(path: Path) -> int | None:
    try:
        with open(path, "rb") as f:
            head = f.read(5)
    except OSError:
        return None
    if head[:4] != b"\x7fELF":
        return None
    return {1: 32, 2: 64}.get(head[4])


def has_lib(name: str, bits: int, root: Path = Path("/")) -> bool:
    return any(_elf_bits(root / d / name) == bits for d in LIB_DIRS)


def _has_vulkan_driver(root: Path) -> bool:
    return any((root / d).is_dir() and any((root / d).glob("*.json")) for d in ICD_DIRS)


def _nvidia(root: Path) -> bool:
    return (root / "proc/driver/nvidia").exists()


# Package commands per distro family for each check.
FIXES: dict[str, dict[str, str]] = {
    "fuse": {
        "arch": "sudo pacman -S --needed fuse2",
        "debian": "sudo apt install libfuse2t64 || sudo apt install libfuse2",
        "fedora": "sudo dnf install fuse-libs",
        "suse": "sudo zypper install libfuse2",
    },
    "vulkan64": {
        "arch": "sudo pacman -S --needed vulkan-icd-loader",
        "debian": "sudo apt install libvulkan1 mesa-vulkan-drivers",
        "fedora": "sudo dnf install vulkan-loader mesa-vulkan-drivers",
        "suse": "sudo zypper install libvulkan1",
    },
    "vulkan_driver": {
        "arch": "sudo pacman -S --needed vulkan-radeon vulkan-intel  # or nvidia-utils for NVIDIA",
        "debian": "sudo apt install mesa-vulkan-drivers",
        "fedora": "sudo dnf install mesa-vulkan-drivers",
        "suse": "sudo zypper install libvulkan_radeon libvulkan_intel",
    },
    "vulkan32": {
        "arch": "sudo pacman -S --needed lib32-vulkan-icd-loader lib32-mesa  # needs [multilib] enabled in /etc/pacman.conf",
        "debian": "sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install libvulkan1:i386 mesa-vulkan-drivers:i386",
        "fedora": "sudo dnf install vulkan-loader.i686 mesa-vulkan-drivers.i686",
        "suse": "sudo zypper install libvulkan1-32bit libvulkan_radeon-32bit libvulkan_intel-32bit",
    },
    "gl32": {
        "arch": "sudo pacman -S --needed lib32-mesa  # needs [multilib] enabled in /etc/pacman.conf",
        "debian": "sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install libgl1:i386 libgl1-mesa-dri:i386",
        "fedora": "sudo dnf install mesa-libGL.i686 mesa-dri-drivers.i686",
        "suse": "sudo zypper install Mesa-libGL1-32bit Mesa-dri-32bit",
    },
}
NVIDIA_32 = {
    "arch": "sudo pacman -S --needed lib32-nvidia-utils",
    "debian": "sudo apt install libnvidia-gl-<version>:i386  # match your driver version",
    "fedora": "sudo dnf install xorg-x11-drv-nvidia-libs.i686",
}


def _fix(key: str, d: Distro, nvidia: bool = False) -> str | None:
    fix = FIXES[key].get(d.family)
    if fix and nvidia and key in ("vulkan32", "gl32") and d.family in NVIDIA_32:
        fix = f"{fix}\n{NVIDIA_32[d.family]}"
    return fix


def run(root: Path = Path("/")) -> list[Check]:
    d = distro(root)
    nvidia = _nvidia(root)
    steamos = d.family == "steamos"
    checks: list[Check] = []

    def add(key: str, title: str, ok: bool, bad: str, why: str, fail: bool = True) -> None:
        if ok:
            checks.append(Check(key, title, OK, "Found."))
            return
        if steamos:
            why += " SteamOS normally ships this; update SteamOS if it is missing."
        checks.append(Check(key, title, FAIL if fail else WARN, f"{bad} {why}", None if steamos else _fix(key, d, nvidia)))

    add("fuse", "FUSE 2 (starts AppImages)", has_lib("libfuse.so.2", 64, root),
        "libfuse.so.2 is not installed.",
        "The official launcher is an AppImage. Without FUSE 2 we start it in extract mode, which is slower but works.",
        fail=False)
    add("vulkan64", "Vulkan loader", has_lib("libvulkan.so.1", 64, root),
        "libvulkan.so.1 is not installed.", "Wine and DXVK need it to draw the game.")
    add("vulkan_driver", "Vulkan driver", _has_vulkan_driver(root),
        "No Vulkan driver is installed.", "Install the one for your graphics card.")
    add("vulkan32", "32-bit Vulkan", has_lib("libvulkan.so.1", 32, root),
        "The 32-bit Vulkan loader is missing.",
        "ArcheAge Classic is a 32-bit game (bin32/archeage.exe), so DXVK needs 32-bit Vulkan and graphics drivers.")
    add("gl32", "32-bit OpenGL", has_lib("libGL.so.1", 32, root),
        "32-bit OpenGL is missing.", "Wine falls back to it when Vulkan is unavailable.", fail=False)
    return checks


def fix_script(checks: list[Check]) -> str:
    """All fix commands for failed or warned checks, ready to paste in a terminal."""
    return "\n".join(c.fix for c in checks if c.status != OK and c.fix)
