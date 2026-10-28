"""Command line entry point: `aacam <command>`."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

from . import __version__, github, installer, paths
from .config import InstalledDb, Settings
from .registry import Registry


class CliError(Exception):
    pass


def current_install(settings: Settings) -> paths.Install:
    saved = settings.get("addon_dir")
    if saved:
        return paths.resolve_user_path(Path(saved))
    found = paths.detect()
    if not found:
        raise CliError(
            "no ArcheAge Classic install found; run `aacam path /path/to/drive_c/AAClassic`"
        )
    settings.set("addon_dir", str(found[0].addon_dir))
    return found[0]


def guard_not_running(args: argparse.Namespace) -> None:
    if not getattr(args, "force", False) and paths.game_running():
        raise CliError("ArcheAge is running; close it first (or pass --force)")


# --- commands --------------------------------------------------------------

def cmd_detect(args, settings):
    found = paths.detect()
    if not found:
        print("No installs found. Set one with: aacam path /path/to/drive_c/AAClassic")
        return
    for i, inst in enumerate(found):
        mark = "*" if str(inst.addon_dir) == settings.get("addon_dir") else " "
        print(f"{mark} [{i}] {inst.addon_dir}")
    if not settings.get("addon_dir"):
        settings.set("addon_dir", str(found[0].addon_dir))
        print(f"Using {found[0].addon_dir}")


def cmd_path(args, settings):
    if args.path:
        inst = paths.resolve_user_path(Path(args.path))
        settings.set("addon_dir", str(inst.addon_dir))
    inst = current_install(settings)
    print(inst.addon_dir)
    if not inst.addon_dir.is_dir():
        print("(folder does not exist yet; it will be created on first install)")


def cmd_list(args, settings):
    inst = current_install(settings)
    enabled = set(installer.read_enabled(inst))
    db = InstalledDb(inst.addon_dir)
    local = installer.local_addons(inst)
    if not local:
        print("No addons installed.")
        return
    for name in local:
        state = "enabled " if name in enabled else "disabled"
        version = db.addons.get(name, {}).get("version", "manual")
        print(f"{state}  {name:<32} {version}")
    for name in sorted(enabled - set(local) - installer.IGNORED):
        print(f"missing   {name:<32} (in addons.txt but no folder)")


def cmd_search(args, settings):
    reg = Registry.load(refresh=args.refresh)
    hits = reg.search(args.text) if args.text else sorted(reg.addons.values(), key=lambda a: a.name.lower())
    for a in hits:
        print(f"{a.name:<28} {a.description[:70]}")
    print(f"\n{len(hits)} addon(s)")


def cmd_info(args, settings):
    a = Registry.load().get(args.name)
    if a is None:
        raise CliError(f"no addon named {args.name}")
    print(f"{a.alias} ({a.name}) by {a.author}")
    print(f"https://github.com/{a.repo}  [{', '.join(a.tags)}]")
    if a.dependencies:
        print(f"Needs: {', '.join(a.dependencies)}")
    print()
    print(textwrap.fill(a.description, 78))


def _install_one(inst, db, addon, release=None):
    release = release or github.latest_release(addon.repo, addon.branch)
    with tempfile.TemporaryDirectory() as tmp:
        archive = github.download(release.zip_url, Path(tmp) / f"{addon.name}.zip")
        installer.install_zip(inst, addon.name, archive)
    installer.restore_kept_data(inst, addon.name)
    installer.enable(inst, addon.name)
    db.addons[addon.name] = {
        "version": release.version,
        "repo": addon.repo,
        "commit": release.commit,
        "installed_at": int(time.time()),
    }
    db.save()
    return release


def cmd_install(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    reg = Registry.load()
    db = InstalledDb(inst.addon_dir)
    for name in args.names:
        try:
            chain = reg.resolve(name)
        except KeyError as e:
            raise CliError(f"no addon named {e.args[0]} (try `aacam search`)") from None
        for addon in chain:
            if addon.name in db.addons and (inst.addon_dir / addon.name).is_dir() and addon.name != chain[-1].name:
                continue  # dependency already present
            release = _install_one(inst, db, addon)
            print(f"Installed {addon.name} {release.version}")


def cmd_update(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    reg = Registry.load(refresh=True)
    db = InstalledDb(inst.addon_dir)
    names = args.names or sorted(db.addons)
    if not names:
        print("Nothing installed by aacam yet.")
        return
    updated = 0
    for name in names:
        addon = reg.get(name)
        if addon is None or name not in db.addons:
            print(f"skip {name}: not installed from the registry")
            continue
        release = github.latest_release(addon.repo, addon.branch)
        if release.version == db.addons[name]["version"]:
            print(f"{name} is up to date ({release.version})")
            continue
        if args.check:
            print(f"{name}: {db.addons[name]['version']} -> {release.version}")
            continue
        _install_one(inst, db, addon, release)
        print(f"Updated {name} to {release.version}")
        updated += 1
    if not args.check:
        print(f"{updated} updated")


def cmd_enable(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    for name in args.names:
        if not (inst.addon_dir / name / "main.lua").is_file():
            raise CliError(f"{name} is not installed")
        print(f"Enabled {name}" if installer.enable(inst, name) else f"{name} already enabled")


def cmd_disable(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    for name in args.names:
        print(f"Disabled {name}" if installer.disable(inst, name) else f"{name} was not enabled")


def cmd_remove(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    db = InstalledDb(inst.addon_dir)
    for name in args.names:
        installer.remove(inst, name, purge=args.purge)
        db.addons.pop(name, None)
        db.save()
        print(f"Removed {name}" + ("" if args.purge else " (settings kept)"))


def cmd_import(args, settings):
    guard_not_running(args)
    inst = current_install(settings)
    archive = Path(args.zip).expanduser()
    name = args.name or archive.stem
    installer.install_zip(inst, name, archive)
    installer.enable(inst, name)
    print(f"Imported {name}")


def cmd_launch(args, settings):
    if args.set:
        settings.set("launch", args.set)
        print(f"Launch command saved: {args.set}")
        return
    command = settings.get("launch")
    if not command:
        raise CliError(
            "no launch command set; for example\n"
            "  aacam launch --set 'lutris lutris:rungame/archeage-classic'\n"
            "  aacam launch --set 'WINEPREFIX=~/.wine wine \"C:/AAClassic/Launcher.exe\"'"
        )
    if args.update_first:
        args.names, args.check, args.force = [], False, False
        cmd_update(args, settings)
    subprocess.Popen(["sh", "-c", command], start_new_session=True)
    print(f"Started: {command}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aacam", description="ArcheAge Classic addon manager for Linux")
    p.add_argument("--version", action="version", version=f"aacam {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_, names=False, force=False):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if names:
            sp.add_argument("names", nargs="+" if names == "+" else "*", metavar="addon")
        if force:
            sp.add_argument("--force", action="store_true", help="run even if the game is open")
        return sp

    add("detect", cmd_detect, "find game installs in Wine/Proton/Lutris prefixes")
    add("path", cmd_path, "show or set the addon folder").add_argument("path", nargs="?")
    add("list", cmd_list, "show installed addons")
    sp = add("search", cmd_search, "search the addon catalogue")
    sp.add_argument("text", nargs="?", default="")
    sp.add_argument("--refresh", action="store_true")
    add("info", cmd_info, "show details for one addon").add_argument("name")
    add("install", cmd_install, "install addons and their dependencies", names="+", force=True)
    sp = add("update", cmd_update, "update installed addons", names="*", force=True)
    sp.add_argument("--check", action="store_true", help="only list available updates")
    add("enable", cmd_enable, "add addons to addons.txt", names="+", force=True)
    add("disable", cmd_disable, "remove addons from addons.txt", names="+", force=True)
    sp = add("remove", cmd_remove, "uninstall addons", names="+", force=True)
    sp.add_argument("--purge", action="store_true", help="also delete saved settings")
    sp = add("import", cmd_import, "install an addon from a local zip", force=True)
    sp.add_argument("zip")
    sp.add_argument("--name", help="folder name (default: zip file name)")
    sp = add("launch", cmd_launch, "start the game")
    sp.add_argument("--set", metavar="COMMAND", help="save the shell command that starts the game")
    sp.add_argument("--update-first", action="store_true", help="update addons before launching")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args, Settings())
    except (CliError, installer.InstallError, github.GitHubError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
