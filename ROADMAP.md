# Roadmap to 1.0

ArcheAge Classic Linux Launcher gets ArcheAge Classic running on Linux and keeps your addons up to date. The official launcher still handles login and patching; ours handles the tweaks and hurdles around it, plus WowUp-style addon management. It will always be free: no paid tier, no ads, no paywalled features.

The vision: anyone on any Linux distro downloads the launcher and ArcheAge Classic works out of the box, with addons supported. So getting the game running comes first, then making everything reliable, then addon polish, then making the launcher easy to get and keep updated. 1.0 is that launcher. Uploading addons and a store come after that.

## Ground rule: nothing that could get a player banned

This applies to every version and every feature, and it wins over anything else in this roadmap.

The launcher works only from the outside, the same way a player does by hand:

- **Game files are never changed.** The game install, its executables, data files and anything the official launcher downloads, patches or verifies stay exactly as the official launcher leaves them.
- **Nothing touches the running game.** No injecting code or DLLs, no reading or writing game memory, no hooking, no overlays, no packet or network tampering, no automation of input.
- **Nothing goes near anti-cheat.** We don't disable, bypass, hide from or probe it.
- **Login and patching stay with the official launcher.** We start it and get out of the way; we never handle credentials.
- **Addons only through the game's own addon system:** Lua addons in `Documents/AAClassic/Addon` and the `addons.txt` list the game reads. That is the mechanism the game provides for addons.
- **Linux setup only through the standard layers.** Drivers, FUSE, and Wine/Proton settings that change how Windows runs on Linux, not how the game behaves. Before we ship any Wine/Proton tweak beyond what the official launcher already does, we confirm with the ArcheAge Classic team (Discord) that it is allowed.
- **Watching the game is read-only.** We only check whether the game's process exists, to know when it started and closed.

A feature that can't meet these rules doesn't ship.

Each version below has a GitHub milestone with its issues. Dates are not promised; a version ships when its milestone is empty.

## Where we are: v0.1.0

- `aacam` command line: detect the addon folder under Wine, Proton, Lutris, Heroic, Bottles and the official launcher; search, install (with dependencies), update, enable, disable, remove, import a zip, launch the game.
- Desktop app (PySide6) with **My Addons**, **Get Addons**, **Update all** and **Play**, shipped as an AppImage built by GitHub Actions on every `v*` tag.
- Catalogue: the public [classic-addon-manager/addons](https://github.com/classic-addon-manager/addons) registry. Downloads come straight from each addon's GitHub releases.
- Verified in game: an installed addon loads, and disabling it through `addons.txt` removes it.

## v0.2: Out of the box on any distro

Goal: anyone on any Linux distro downloads one file, opens it, and ends up in ArcheAge Classic without reading a guide. This is the vision and comes first.

The official launcher works and is the reference: it handles account login, game downloads and patching, and already runs the game under Wine. We don't reimplement any of that. We wrap it and fix what is hard on Linux.

- Find the official launcher, or download and install it if it is missing.
- One Play button: update addons, start the official launcher for login and patching, and notice when the game is running and when it exits.
In v0.2.0:

- Find the official launcher, or download it from aa-classic.com if it is missing.
- One Play button: update addons, start the official launcher for login and patching (in extract mode when FUSE 2 is missing), and notice when the game is running and when it exits.
- Check the system for what the game needs (FUSE 2, Vulkan, a Vulkan driver, 32-bit Vulkan and OpenGL) and give the exact fix command for Arch/CachyOS, SteamOS, Debian/Ubuntu/Mint, Fedora and openSUSE.
- First-run setup that walks through the system check, the official launcher and the addon folder.
- Crash help: when the game closes soon after starting, offer Setup and a copyable report with system details and the official launcher's log.
- One AppImage, built on Ubuntu 22.04's glibc with Qt and Python bundled, that CI starts on Ubuntu 22.04 and 24.04, Debian 12, Fedora, Arch, openSUSE Tumbleweed and Linux Mint before every release.

Next, in v0.2.1, once we know which crashes Linux players actually hit:

- Choose the Wine or Proton build the game runs with, and repair the game's prefix.
- Known Linux fixes as switches (Wine/Proton settings and environment variables only), each documented with the crash it fixes, recognised in crash reports, and cleared with the ArcheAge Classic team first per the ground rule above.
- A Steam Deck check on real hardware.
- Never touch the game's own files, the login, or anything the official launcher signs or patches.

## v0.3: Reliable on every machine

Goal: nobody hits an error they cannot understand.

- Clear, actionable error messages for GitHub rate limits, network failures and bad zips, in both the app and the CLI.
- Optional GitHub token in the app's settings (today it is only the `GITHUB_TOKEN` variable), so updating many addons does not hit the 60 calls an hour limit.
- Cache release lookups and use conditional requests (ETags) so a check for updates costs few API calls.
- Detect addons that were installed by hand or by another manager and offer to track them for updates.
- A log file and a "Copy diagnostics" button for bug reports, plus an issue template.
- Tests for the GUI's core flows and for path detection across the prefix layouts we support.

## v0.4: Addons that feel like WowUp

Goal: addon management feels like WowUp, not a table.

- Addon details page: description, author, tags, dependencies, changelog from the GitHub release notes, link to the repo.
- Update badges and a count on the My Addons tab; changelog preview before you update.
- Roll back to the previous version of an addon (keep the last installed zip).
- Back up and restore addon settings (the `.data` folders) and `addons.txt`.
- Auto-update addons on start, and an option to update before Play.
- Sort and filter My Addons (enabled, has update, name); show download counts and last-updated dates in Get Addons.
- Dark and light theme that follows the desktop; a proper app icon set.

## v0.5: Easy to get and keep current

Goal: installing the manager is one step on any common Linux setup, and it updates itself.

- Self-update for the AppImage (AppImageUpdate zsync info, or download-and-replace from GitHub releases).
- AUR package (`archeage-classic-linux-launcher-bin`) for Arch and CachyOS users.
- Flatpak on Flathub, with filesystem access to Wine and Steam prefixes.
- Signed release checksums.

## v0.6: A launcher you'd keep open

Goal: everything a player checks before playing, in one window.

- Game news and patch notes, and server status where there is a public source.
- Settings for the launcher's behaviour: close or minimise to the tray when the game starts, update addons on start.
- Desktop integration: menu entry, `.desktop` file, and a Steam non-Steam-game shortcut.

## 1.0: The Linux launcher for ArcheAge Classic

Goal: the app we would recommend to every ArcheAge Classic player on Linux, with addons built in.

- Reliable from install to in game on the official launcher's Wine, Proton (Steam) and Lutris setups, tested before every release.
- No known data-loss bugs: addon settings and `addons.txt` are never lost by an install, update, remove or crash.
- Config and data formats are documented and versioned, with migrations from every 0.x release.
- A user guide and FAQ with screenshots, including the crash fixes.
- A release checklist followed for every release.

## After 1.0

These stay planned but no longer block 1.0.

- **Kit for the official launcher:** the addon engine split from our window, a headless JSON interface, a small bundled binary and integration docs, so the game's developers can build addons into their own launcher if they want to. Questions for them: who to talk to, what their launcher is built with, how it knows the game's prefix, which catalogue they want, and which license terms they need.
- **Profiles and multiple installs:** several game installs with a switcher, addon profiles, export and import of addon lists.
- **Uploading addons:** validate an addon, publish it by opening a PR to the shared catalogue, `aacam new` template, warnings on unexpected files.
- **Addon store:** featured, new and popular addons from a static index built by GitHub Actions, and a report-an-addon flow.

## Out of scope

- Anything paid, ads, or accounts required to download addons.
- Hosting addon files ourselves. Addons stay on their authors' GitHub repos.
- Anything the ground rule above forbids: changing game files, touching the running game or its memory, overlays, automation, or anything near anti-cheat.
- Replacing the official launcher's login, game downloads or patching.
