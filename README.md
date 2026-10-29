# AAC Addon Manager: ArcheAge Classic addons on Linux

A free desktop app and command-line tool that installs, updates, enables and removes ArcheAge Classic addons when the game runs under the official launcher, Wine, Proton (Steam), Lutris, Bottles or Heroic.

## Desktop app

Download `AAC-Addon-Manager-x86_64.AppImage` from the [Releases page](https://github.com/Z-arium/AAC-ADDON_MANAGER/releases), then:

```bash
chmod +x AAC-Addon-Manager-x86_64.AppImage
./AAC-Addon-Manager-x86_64.AppImage
```

- **My Addons**: everything in your addon folder, a checkbox to turn each one on or off in game, its version, and Update or Remove buttons.
- **Get Addons**: the public catalogue, with search and tag filters. Install pulls in any addons it depends on.
- **Update all** checks every addon you installed on startup and updates them in one click.
- **Play** starts the game. The first time, it asks for the launch command (it suggests the official launcher if it finds it).
- The app tells you in the status bar when a newer version of itself is out.

Running from source instead: `./install.sh` installs both the app (menu entry "AAC Addon Manager") and the `aacam` command.

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
aacam import ~/Downloads/MyAddon.zip   # an addon that isn't in the catalogue

aacam launch --set 'lutris lutris:rungame/archeage-classic'   # save how you start the game
aacam launch --update-first   # update addons, then start the game
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
