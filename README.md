# ArcheAge Classic Linux Launcher

A free app that gets ArcheAge Classic running on Linux and keeps your addons up to date. The official launcher still handles login and patching; this one handles the tweaks and hurdles around it. It checks your system for the drivers the game needs and tells you the exact command to fix what's missing on your distro, finds or downloads the official launcher, starts the game through it, and installs and updates addons. See the [roadmap](ROADMAP.md) for what's next.

**Safe to use:** the launcher never changes game files, never touches the running game or anti-cheat, and never sees your login. It installs addons only through the game's own addon folder and `addons.txt`, and starts the official launcher, which does login and patching itself. See the ground rule in the [roadmap](ROADMAP.md).

## Desktop app

Download `ArcheAge-Classic-Linux-Launcher-x86_64.AppImage` from the [Releases page](https://github.com/Z-arium/AAC-ADDON_MANAGER/releases), then:

```bash
chmod +x ArcheAge-Classic-Linux-Launcher-x86_64.AppImage
./ArcheAge-Classic-Linux-Launcher-x86_64.AppImage
```

The first time it opens, **Setup** walks you through three things:

1. **Your system**: FUSE 2, Vulkan, a Vulkan driver, and the 32-bit Vulkan and OpenGL libraries the 32-bit game needs, with copyable fix commands for Arch/CachyOS, SteamOS, Debian/Ubuntu/Mint, Fedora and openSUSE.
2. **The official launcher**: found where it usually lives, or downloaded from aa-classic.com.
3. **Your addon folder**: found automatically once the game has run, or chosen by you.

- **Play** updates your addons, then starts the official launcher, where you log in and press Play. While the game runs the app keeps an eye on its memory use, and shows a desktop notification when the 32-bit game gets close to its 4 GB limit, before it crashes. When it closes, the app checks the logs and the kernel for signs of a crash (out of memory, unhandled exceptions, GPU errors, a very short session). If it finds one, it explains it, saves the logs to a crash report zip, and suggests the fix that matches.
- **Wine build**: the official launcher runs the game with Wine or Proton. Under Proton the game's anti-cheat closes it right after server select, so when the official launcher's Wine-Staging prefix exists, Play points it at a Wine-Staging build from Heroic or Lutris (`AAC_WINE`, `AAC_WINEPREFIX`). You can pick another build or let the official launcher choose. A WoW64 build (`aacam wine --download-wow64`) would leave the game more of its 4 GB, but it stays switched off until the ArcheAge Classic team confirms it is allowed.
- **Crash fixes** are switches applied the next time the game starts, using only variables the official launcher itself knows: run the game on the NVIDIA card on machines with two graphics chips, turn off esync/fsync under Proton, or draw with WineD3D instead of DXVK. A DXVK setting that leaves the game more of its 4 GB is built but stays off until the ArcheAge Classic team confirms it is allowed (see the ground rule in [ROADMAP.md](ROADMAP.md)). Until then, lowering texture quality in the game's options is the fix for out-of-memory crashes. **Diagnostics** records extra Wine, Proton and DXVK logs for one session, for bug reports. All of these are environment variables passed to the official launcher, which passes them to the game. No game files are touched and nothing reads or changes the game's memory, so there is nothing for anti-cheat to object to. Crash reports hide the login token the official launcher writes to its logs, and leave the game's crash dumps out because they can contain it.
- **My Addons**: everything in your addon folder, a checkbox to turn each one on or off in game, its version, and Update or Remove buttons.
- **Get Addons**: the public catalogue, with search and tag filters. Install pulls in any addons it depends on.
- **Update all** checks every addon you installed on startup and updates them in one click.
- The app tells you in the status bar when a newer version of itself is out.

Running from source instead: `./install.sh` installs both the app (menu entry "ArcheAge Classic Linux Launcher") and the `aacam` command.

## Command line

`aacam` does the same from a terminal. It reads the same public addon catalogue as Classic Addon Manager ([classic-addon-manager/addons](https://github.com/classic-addon-manager/addons)) and downloads addons straight from their GitHub releases.

### Install

```bash
git clone https://github.com/Z-arium/AAC-ADDON_MANAGER
cd AAC-ADDON_MANAGER
./install.sh
```

`install.sh` needs only Python 3. It puts aacam in its own virtualenv under `~/.local/share/aacam/venv` and links it to `~/.local/bin/aacam`; run it again after `git pull` to upgrade. If you prefer pipx: `pipx install .`

### Use

```bash
aacam detect                  # find AAClassic inside your Wine/Proton/Lutris prefixes
aacam path ~/Documents/AAClassic   # or set it yourself
aacam search raid             # browse the catalogue
aacam info nuzi-raid
aacam install nuzi-raid       # installs dependencies too, and enables it
aacam list                    # installed addons, enabled or not, and versions
aacam update                  # update everything aacam installed (--check to only look)
aacam disable nuzi-raid       # keeps the files, removes it from addons.txt
aacam remove nuzi-raid        # keeps saved settings; --purge deletes them
aacam install --repo Z-arium/aac-dps-meter   # straight from a GitHub repo; updates like the rest
aacam import ~/Downloads/MyAddon.zip   # an addon that isn't in the catalogue

aacam doctor                  # check drivers and libraries, print the fix commands for your distro
aacam launcher --download     # download the official launcher (or --set /path/to/it.AppImage)
aacam launch --update-first   # update addons, then start the official launcher
aacam report                  # system details and the launcher's log, to paste when asking for help
aacam fixes                   # list crash fixes; --on nvidia_gpu, --off wined3d, --diagnostics on
aacam wine                    # show or choose the Wine build (--set DIR, --auto, --launcher, --download-wow64)
aacam crash-report            # after a crash: collect logs and kernel messages into a zip and name known causes
aacam crash-report --history  # recent sessions: how long they lasted, crash cause, zone
aacam launch --set 'lutris lutris:rungame/archeage-classic'   # start the game your own way instead
```

Commands that change files refuse to run while ArcheAge is open, because the game can overwrite `addons.txt` on exit. Pass `--force` to override.

## How it works

- Addons live in `Documents/AAClassic/Addon/<name>/`, each with a `main.lua`. The game loads the folders listed in `Documents/AAClassic/Addon/addons.txt`, one per line. Wine usually maps the Windows Documents folder to `~/Documents`, so with the official launcher that is `~/Documents/AAClassic/Addon`.
- Detection checks `~/Documents/AAClassic` (and your XDG Documents folder), then each Wine user's Documents and `drive_c/AAClassic*/Documents/Addon` in `~/.wine`, `$WINEPREFIX`, the official launcher's `~/.local/share/AAClassic/*` prefixes, `~/Games/*` (Lutris), Heroic, Steam `compatdata/*/pfx` (native and Flatpak) and Bottles. A folder that already has `addons.txt` wins.
- Installs unpack into a staging folder and swap in atomically. An addon's `.data` folder is carried over on update and set aside on remove, then put back if you reinstall.
- `addons.txt` is backed up to `addons.txt.bak` before every change.
- Settings: `~/.config/aacam/config.json`. Installed versions: `~/.local/share/aacam/installed.json`. Catalogue cache (1 hour): `~/.cache/aacam/`.
- GitHub allows 60 unauthenticated API calls an hour. Set `GITHUB_TOKEN` if you update many addons at once.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Releasing

Push a tag like `v0.2.0` (bump `version` in `pyproject.toml` and `src/aacam/__init__.py` first). The release workflow runs the tests, builds the AppImage with PyInstaller and appimagetool, and attaches it to a GitHub release. Pull requests build the AppImage too and keep it as a workflow artifact.
