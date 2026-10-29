"""Command line entry point: `aacam <command>`."""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from pathlib import Path

from . import __version__, github, installer, official, paths, report, syscheck
from .config import Settings
from .core import Manager, NotFound
from .registry import Registry


class CliError(Exception):
    pass


def manager(settings: Settings) -> Manager:
    mgr = Manager.from_settings(settings)
    if mgr is None:
        raise CliError(
            "no ArcheAge Classic addon folder found; run `aacam path ~/Documents/AAClassic`"
        )
    return mgr


def current_install(settings: Settings) -> paths.Install:
    return manager(settings).install


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
    mgr = manager(settings)
    local = mgr.local()
    if not local:
        print("No addons installed.")
    for a in local:
        print(f"{'enabled ' if a.enabled else 'disabled'}  {a.name:<32} {a.version}")
    for name in mgr.missing():
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


def cmd_install(args, settings):
    guard_not_running(args)
    mgr = manager(settings)
    if args.repo:
        if args.names:
            raise CliError("give either addon names or --repo, not both")
        try:
            addon, release = mgr.add_repo(args.repo, args.as_, args.branch)
        except NotFound as e:
            raise CliError(f"not a GitHub repo: {e.args[0]}") from None
        print(f"Installed {addon.name} {release.version} from {addon.repo}")
        return
    if not args.names:
        raise CliError("name an addon to install, or use --repo owner/repo")
    reg = Registry.load()
    for name in args.names:
        try:
            done = mgr.add(reg, name)
        except NotFound as e:
            raise CliError(f"no addon named {e.args[0]} (try `aacam search`)") from None
        for addon, release in done:
            print(f"Installed {addon.name} {release.version}")


def cmd_update(args, settings):
    guard_not_running(args)
    mgr = manager(settings)
    if not mgr.db.addons:
        print("Nothing installed by aacam yet.")
        return
    reg = Registry.load(refresh=True)
    for name in args.names:
        if name not in mgr.db.addons:
            print(f"skip {name}: not installed from the catalogue")
    pending = mgr.check_updates(reg, args.names or None)
    for name in args.names or sorted(mgr.db.addons):
        if name in mgr.db.addons and name not in pending:
            print(f"{name} is up to date ({mgr.db.addons[name]['version']})")
    for name, release in pending.items():
        if args.check:
            print(f"{name}: {mgr.db.addons[name]['version']} -> {release.version}")
            continue
        mgr.install_one(mgr.addon_for(reg, name), release)
        print(f"Updated {name} to {release.version}")
    if not args.check:
        print(f"{len(pending)} updated")


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
    mgr = manager(settings)
    for name in args.names:
        mgr.remove(name, purge=args.purge)
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
    if args.set is not None:
        settings.set("launch", args.set or None)
        print(f"Launch command saved: {args.set}" if args.set else "Using the official launcher again")
        return
    command = settings.get("launch")
    appimage = None if command else official.locate(settings)
    if not command and appimage is None:
        raise CliError(
            "the official ArcheAge Classic launcher was not found; get it with\n"
            "  aacam launcher --download\n"
            "or point at it with `aacam launcher --set /path/to/launcher.AppImage`"
        )
    if args.update_first:
        args.names, args.check, args.force = [], False, False
        cmd_update(args, settings)
    if command:
        subprocess.Popen(["sh", "-c", command], start_new_session=True)
        print(f"Started: {command}")
        return
    fuse_ok = syscheck.has_lib("libfuse.so.2", 64)
    official.start(appimage, fuse_ok)
    print(f"Started the official launcher: {appimage}")
    print(f"Its output goes to {official.log_path()}")


def cmd_launcher(args, settings):
    if args.set:
        path = Path(args.set).expanduser().resolve()
        if not path.is_file():
            raise CliError(f"{path} does not exist")
        settings.set("official_launcher", str(path))
    elif args.download:
        print(f"Downloading the official launcher {args.version} from aa-classic.com…")
        path = official.download(version=args.version)
        settings.set("official_launcher", str(path))
        print(f"Saved to {path}")
        return
    path = official.locate(settings)
    if path is None:
        raise CliError("official launcher not found; run `aacam launcher --download`")
    print(path)


def cmd_doctor(args, settings):
    d = syscheck.distro()
    print(f"{d.name} ({d.family})\n")
    checks = syscheck.run()
    for c in checks:
        print(f"[{report.MARK[c.status]}] {c.title}: {c.detail}")
    launcher = official.locate(settings)
    print(f"[{'ok  ' if launcher else 'FAIL'}] Official launcher: {launcher or 'not found, run `aacam launcher --download`'}")
    fixes = syscheck.fix_script(checks)
    if fixes:
        print("\nTo fix, run:\n")
        print(textwrap.indent(fixes, "  "))
    elif all(c.status == syscheck.OK for c in checks) and launcher:
        print("\nEverything the game needs is here.")


def cmd_report(args, settings):
    print(report.build(settings), end="")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aacam", description="ArcheAge Classic Linux Launcher: addons and game setup")
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
    sp = add("install", cmd_install, "install addons and their dependencies", names="*", force=True)
    sp.add_argument("--repo", metavar="OWNER/REPO", help="install from a GitHub repo that isn't in the catalogue")
    sp.add_argument("--as", dest="as_", metavar="NAME", help="folder name for --repo (default: from the repo name)")
    sp.add_argument("--branch", default="main", help="branch to use for --repo when it has no releases")
    sp = add("update", cmd_update, "update installed addons", names="*", force=True)
    sp.add_argument("--check", action="store_true", help="only list available updates")
    add("enable", cmd_enable, "add addons to addons.txt", names="+", force=True)
    add("disable", cmd_disable, "remove addons from addons.txt", names="+", force=True)
    sp = add("remove", cmd_remove, "uninstall addons", names="+", force=True)
    sp.add_argument("--purge", action="store_true", help="also delete saved settings")
    sp = add("import", cmd_import, "install an addon from a local zip", force=True)
    sp.add_argument("zip")
    sp.add_argument("--name", help="folder name (default: zip file name)")
    add("doctor", cmd_doctor, "check this system for what the game needs, and how to fix it")
    sp = add("launcher", cmd_launcher, "show, set or download the official ArcheAge Classic launcher")
    sp.add_argument("--set", metavar="APPIMAGE", help="use this launcher AppImage")
    sp.add_argument("--download", action="store_true", help="download it from aa-classic.com")
    sp.add_argument("--version", default=official.DEFAULT_VERSION, help="launcher version to download")
    add("report", cmd_report, "print a report of your setup to share when asking for help")
    sp = add("launch", cmd_launch, "start the game through the official launcher")
    sp.add_argument("--set", metavar="COMMAND",
                    help="start the game with this shell command instead (empty to go back to the official launcher)")
    sp.add_argument("--update-first", action="store_true", help="update addons before launching")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args, Settings())
    except (CliError, installer.InstallError, github.GitHubError, official.LauncherError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
