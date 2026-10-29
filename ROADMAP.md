# Roadmap to 1.0

AAC Addon Manager is a free, WowUp-style addon manager for ArcheAge Classic. It will always be free: no paid tier, no ads, no paywalled features.

The order is deliberate. First make installing and updating addons on your own machine boringly reliable, then make it pleasant, then make it easy to get, and only then open the door to authors uploading addons and to a store.

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

## v0.5: Profiles and multiple installs

Goal: handle more than one game folder and more than one set of addons.

- Several game installs or prefixes, with a switcher.
- Addon profiles (for example "raiding" and "minimal") that switch which addons are enabled in one click.
- Export and import an addon list so friends or a guild can share a setup.

## v0.6: Uploading addons

Goal: authors can publish without editing YAML by hand.

- An "Author" tab and `aacam publish` that validate an addon (has `main.lua`, valid metadata, a release zip that unpacks cleanly) before publishing.
- Publish to the existing catalogue by opening a pull request against `classic-addon-manager/addons`, so we add to the shared ecosystem instead of splitting it.
- A template for new addons (`aacam new`).
- Basic safety checks on downloaded addons: warn on files that are not Lua, assets or text.

## v0.7: Addon store

Goal: discovery beyond a search box.

- A store front in the app: featured, new, recently updated and popular addons, with screenshots from the addon repos.
- A small public index (static JSON built by GitHub Actions) with download counts and update dates, so the app does not need a server we pay for.
- Ratings or likes only if they can be run for free and resist abuse; otherwise skip them.
- Report-an-addon flow for broken or malicious addons.

## 1.0: Stable

Goal: something we would recommend to every ArcheAge Classic player on Linux.

- No known data-loss bugs: addon settings and `addons.txt` are never lost by an install, update, remove or crash.
- Config and data formats are documented and versioned, with migrations from every 0.x release.
- User guide and FAQ in the repo, with screenshots.
- Translations framework, with at least English shipped and room for community languages.
- A release checklist that is followed for every release, including testing on the official launcher, Proton and Lutris.

## Out of scope

- Anything paid, ads, or accounts required to download addons.
- Hosting addon files ourselves. Addons stay on their authors' GitHub repos.
- Game modifications beyond what the game's addon API allows.
