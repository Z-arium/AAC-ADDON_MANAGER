# aacam: ArcheAge Classic addon manager for Linux

A command-line tool that installs, updates, enables and removes ArcheAge Classic addons when the game runs under Wine, Proton (Steam), Lutris, Bottles or Heroic. It reads the same public addon catalogue as Classic Addon Manager ([classic-addon-manager/addons](https://github.com/classic-addon-manager/addons)) and downloads addons straight from their GitHub releases.

## Install

```bash
git clone https://github.com/Z-arium/AAC-ADDON_MANAGER
cd AAC-ADDON_MANAGER
./install.sh
```

`install.sh` needs only Python 3. It puts aacam in its own virtualenv under `~/.local/share/aacam/venv` and links it to `~/.local/bin/aacam`; run it again after `git pull` to upgrade. If you prefer pipx: `pipx install .`

## Use

```bash
aacam detect                  # find AAClassic inside your Wine/Proton/Lutris prefixes
aacam path ~/Games/archeage-classic/drive_c/AAClassic   # or set it yourself
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

- Addons live in `<install>/Documents/Addon/<name>/`, each with a `main.lua`. The game loads the folders listed in `Documents/Addon/addons.txt`, one per line. If your install uses `Documents/AAClassic/addons` instead, aacam picks that up.
- Detection looks for `drive_c/AAClassic*` in `~/.wine`, `$WINEPREFIX`, `~/Games/*` (Lutris), Steam `compatdata/*/pfx` (native and Flatpak), Bottles and Heroic prefixes. The highest-numbered install wins.
- Installs unpack into a staging folder and swap in atomically. An addon's `.data` folder is carried over on update and set aside on remove, then put back if you reinstall.
- `addons.txt` is backed up to `addons.txt.bak` before every change.
- Settings: `~/.config/aacam/config.json`. Installed versions: `~/.local/share/aacam/installed.json`. Catalogue cache (1 hour): `~/.cache/aacam/`.
- GitHub allows 60 unauthenticated API calls an hour. Set `GITHUB_TOKEN` if you update many addons at once.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
