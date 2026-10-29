# Roadmap to 1.0

AAC Addon Manager is a free, WowUp-style addon manager for ArcheAge Classic. It will always be free: no paid tier, no ads, no paywalled features.

The order is deliberate. First make installing and updating addons on your own machine boringly reliable, then make it pleasant, then make it easy to get, then package it so the official launcher can ship it. 1.0 is the version we hand to the ArcheAge Classic developers for Linux players. Uploading addons and a store come after that.

Each version below has a GitHub milestone with its issues. Dates are not promised; a version ships when its milestone is empty.

## Where we are: v0.1.0

- `aacam` command line: detect the addon folder under Wine, Proton, Lutris, Heroic, Bottles and the official launcher; search, install (with dependencies), update, enable, disable, remove, import a zip, launch the game.
- Desktop app (PySide6) with **My Addons**, **Get Addons**, **Update all** and **Play**, shipped as an AppImage built by GitHub Actions on every `v*` tag.
- Catalogue: the public [classic-addon-manager/addons](https://github.com/classic-addon-manager/addons) registry. Downloads come straight from each addon's GitHub releases.
- Verified in game: an installed addon loads, and disabling it through `addons.txt` removes it.

## v0.2: Reliable on every machine

Goal: nobody hits an error they cannot understand.

- Clear, actionable error messages for GitHub rate limits, network failures and bad zips, in both the app and the CLI.
- Optional GitHub token in the app's settings (today it is only the `GITHUB_TOKEN` variable), so updating many addons does not hit the 60 calls an hour limit.
- Cache release lookups and use conditional requests (ETags) so a check for updates costs few API calls.
- First-run setup: find the addon folder, confirm it, and offer to set the launch command, instead of an empty window.
- Detect addons that were installed by hand or by another manager and offer to track them for updates.
- A log file and a "Copy diagnostics" button for bug reports, plus an issue template.
- Tests for the GUI's core flows and for path detection across the prefix layouts we support.

## v0.3: Pleasant to use

Goal: it feels like WowUp, not a table.

- Addon details page: description, author, tags, dependencies, changelog from the GitHub release notes, link to the repo.
- Update badges and a count on the My Addons tab; changelog preview before you update.
- Roll back to the previous version of an addon (keep the last installed zip).
- Back up and restore addon settings (the `.data` folders) and `addons.txt`.
- Auto-update addons on start, and an option to update before Play.
- Sort and filter My Addons (enabled, has update, name); show download counts and last-updated dates in Get Addons.
- Dark and light theme that follows the desktop; a proper app icon set.

## v0.4: Easy to get and keep current

Goal: installing the manager is one step on any common Linux setup, and it updates itself.

- Self-update for the AppImage (AppImageUpdate zsync info, or download-and-replace from GitHub releases).
- AUR package (`aac-addon-manager-bin`) for Arch and CachyOS users.
- Flatpak on Flathub, with filesystem access to Wine and Steam prefixes.
- A Windows build, since the catalogue and the code are not Linux-specific; Windows users get one tool for the same addons.
- Signed release checksums.

## v0.5: Launcher integration kit

Goal: the ArcheAge Classic developers can drop the addon manager into the official launcher without taking on our UI or our Python setup.

- Split the engine from the desktop app: a core with no Qt dependency, so the launcher only ships what it needs.
- A headless interface with machine-readable output (`aacam --json list`, `install`, `update`, `enable`, `disable`, `remove`, `check`), with stable exit codes and progress events on stdout, so a launcher written in any language can drive it.
- A single self-contained binary of that core for Linux x86_64 (no system Python needed), small enough to bundle in the launcher AppImage.
- Let the launcher tell us the game's Wine prefix and Documents folder directly (`--prefix` / `--addon-dir` / environment variables), so detection is never a guess when we run inside it.
- Configurable catalogue source and branding, so the devs can point it at their own addon list later and show it under their name.
- A license that lets the devs bundle and modify it.
- `INTEGRATION.md`: how to call it, the JSON schema, where it stores files, and how to update the bundled copy.
- Reference integration: a demo that drives the headless core the way a launcher would, tested against the official launcher's prefixes.

## 1.0: In the official launcher

Goal: Linux players manage addons from the official ArcheAge Classic launcher, with our engine underneath, and we hand that version to the developers.

- The integration kit, versioned with a frozen 1.0 JSON interface and documented upgrade path.
- No known data-loss bugs: addon settings and `addons.txt` are never lost by an install, update, remove or crash.
- Config and data formats are documented and versioned, with migrations from every 0.x release.
- A release checklist followed for every release, including testing inside the official launcher, Proton and Lutris.
- A user guide and FAQ with screenshots, for players and for launcher support staff.
- A hand-off package for the developers: the binary, `INTEGRATION.md`, the demo, test results and a contact for fixes.
- The standalone AppImage keeps shipping for players who don't use the official launcher.

### What we need from the developers

- Whether they want this at all, and who to talk to.
- What the launcher is built with, and whether they prefer to call a bundled binary, embed a library, or link out to our app.
- How the launcher knows the game's prefix and Documents folder, so it can pass them to us.
- Whether addons should come from the public community catalogue or a list they moderate.
- Which license terms they need to ship it.
- Whether the game's addon API or `addons.txt` format will change, and any rules they want enforced on addons.

## After 1.0

These stay planned but no longer block 1.0.

- **Profiles and multiple installs:** several game installs with a switcher, addon profiles, export and import of addon lists.
- **Uploading addons:** validate an addon, publish it by opening a PR to the shared catalogue, `aacam new` template, warnings on unexpected files.
- **Addon store:** featured, new and popular addons from a static index built by GitHub Actions, and a report-an-addon flow.

## Out of scope

- Anything paid, ads, or accounts required to download addons.
- Hosting addon files ourselves. Addons stay on their authors' GitHub repos.
- Game modifications beyond what the game's addon API allows.
