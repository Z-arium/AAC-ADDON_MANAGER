"""Command line entry point: `aacam <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from . import __version__, crash, github, installer, logs, net, official, paths, report, syscheck, wine
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
    if any(not a.managed for a in local):
        try:
            loose = mgr.untracked(Registry.load())
        except github.GitHubError:
            return
        if loose:
            print(f"\n{len(loose)} addon(s) installed by hand match the catalogue: {', '.join(loose)}")
            print("Track them for updates with: aacam track --all")


def cmd_track(args, settings):
    guard_not_running(args)
    mgr = manager(settings)
    loose = mgr.untracked(Registry.load())
    names = sorted(loose) if args.all else args.names
    if not names:
        if loose:
            print("Installed by hand and found in the catalogue:")
            for name, addon in sorted(loose.items()):
                print(f"  {name:<28} https://github.com/{addon.repo}")
            print("\nTrack them with `aacam track --all` or `aacam track NAME`.")
        else:
            print("Nothing to track: every addon that is in the catalogue is already tracked.")
        return
    for name in names:
        if name in mgr.db.addons:
            print(f"{name} is already tracked")
            continue
        if name not in loose:
            raise CliError(f"{name} isn't an installed addon that matches the catalogue")
        mgr.track(name, loose[name])
        print(f"Tracking {name} ({loose[name].repo}); `aacam update` brings it to the latest release")


def cmd_token(args, settings):
    if args.set is not None:
        value = args.set.strip() or (sys.stdin.readline().strip() if not sys.stdin.isatty() else "")
        if not value:
            raise CliError("give the token: `aacam token --set TOKEN`, or pipe it in")
        settings.set("github_token", value)
        github.clear_cache()
        print("Token saved.")
    elif args.clear:
        settings.set("github_token", None)
        print("Token cleared.")
    source = ("the GITHUB_TOKEN variable" if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
              else "your settings" if settings.get("github_token") else None)
    print(f"Using a token from {source}." if source else "No token: GitHub allows 60 lookups an hour.")
    try:
        rate = github.rate_limit()
    except github.GitHubError as e:
        print(f"Could not check the limit: {e}")
        return
    reset = time.strftime("%H:%M", time.localtime(rate.get("reset", 0)))
    print(f"{rate.get('remaining')} of {rate.get('limit')} GitHub lookups left this hour (resets at {reset}).")


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
        if name in mgr.check_errors:
            print(f"{name}: could not check, {mgr.check_errors[name]}")
        elif name in mgr.db.addons and name not in pending:
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
    extra = crash.launch_env(settings)
    if command:
        subprocess.Popen(["sh", "-c", command], start_new_session=True, env=official.environment(True, extra=extra))
        print(f"Started: {command}")
        return
    fuse_ok = syscheck.has_lib("libfuse.so.2", 64)
    official.start(appimage, fuse_ok, extra)
    print(f"Started the official launcher: {appimage}")
    if extra.get("AAC_WINE"):
        print(f"Wine: {extra['AAC_WINE']} with prefix {extra['WINEPREFIX']}")
    print(f"Its output goes to {official.log_path()}")
    if settings.get("diagnostics"):
        print(f"Diagnostics on: Wine, Proton and DXVK logs go to {crash.log_dir()}")
    print("If the game crashes, run `aacam crash-report` afterwards.")


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
    if args.network:
        problem = net.check()
        print(f"[{'ok  ' if problem is None else 'FAIL'}] HTTPS to GitHub: {problem or 'works'}")
        if problem is not None and args.strict:
            raise CliError(f"HTTPS to GitHub failed: {problem}")
    fixes = syscheck.fix_script(checks)
    if fixes:
        print("\nTo fix, run:\n")
        print(textwrap.indent(fixes, "  "))
    elif all(c.status == syscheck.OK for c in checks) and launcher:
        print("\nEverything the game needs is here.")


def cmd_fixes(args, settings):
    try:
        for key in args.on or []:
            crash.set_fix(settings, key, True)
        for key in args.off or []:
            crash.set_fix(settings, key, False)
    except KeyError as e:
        raise CliError(f"unknown fix {e}; choose from {', '.join(crash.BY_KEY)}") from None
    except PermissionError as e:
        raise CliError(str(e)) from None
    if args.diagnostics:
        settings.set("diagnostics", args.diagnostics == "on")
    on = set(settings.get("fixes", []))
    for fix in crash.FIXES:
        state = "on " if fix.key in on and fix.cleared else "off"
        print(f"[{state}] {fix.key}: {fix.title}{'' if fix.cleared else ' (waiting for the ArcheAge Classic team OK)'}")
        print(textwrap.indent(textwrap.fill(fix.why, 76), "      "))
    print(f"\nDiagnostics (extra Wine, Proton and DXVK logs): {'on' if settings.get('diagnostics') else 'off'}")
    print("Changes apply the next time you start the game.")


def cmd_wine(args, settings):
    if args.test_wow64:
        print(f"Getting {wine.WOW64_NAME} (about 75 MB the first time)…")
        build = wine.download_wow64()
        print(f"Copying your Wine prefix to {wine.wow64_prefix()} (your current one stays as it is)…")
        prefix = wine.prepare_wow64_prefix()
        settings.set("wow64_opt_in", True)
        settings.set("wine", str(build))
        settings.set("wineprefix", str(prefix))
        print("WoW64 Wine is on for this PC only. The ArcheAge Classic team hasn't confirmed it is allowed yet,\n"
              "so you test it at your own risk. Go back with `aacam wine --stop-wow64`.")
    elif args.stop_wow64:
        settings.set("wow64_opt_in", False)
        settings.set("wine", None)
        settings.set("wineprefix", None)
        print("Back to the normal Wine-Staging build.")
    elif args.download_wow64:
        print(f"Downloading {wine.WOW64_NAME} (about 75 MB)…")
        build = wine.download_wow64()
        print(f"Saved to {build}")
        if not wine.WOW64_CLEARED:
            print("It stays switched off until the ArcheAge Classic team confirms WoW64 Wine is allowed.")
    if args.set:
        build = Path(args.set).expanduser().resolve()
        if build.name == "wine" and build.parent.name == "bin":
            build = build.parent.parent
        if not os.access(build / "bin/wine", os.X_OK):
            raise CliError(f"no bin/wine in {build}")
        if not wine.allowed(build, bool(settings.get("wow64_opt_in"))):
            raise CliError("WoW64 Wine builds wait for the ArcheAge Classic team's OK before they can be used")
        settings.set("wine", str(build))
    elif args.launcher:
        settings.set("wine", wine.LAUNCHER)
    elif args.auto:
        settings.set("wine", None)
    if args.prefix:
        settings.set("wineprefix", str(Path(args.prefix).expanduser().resolve()))
    opt_in = bool(settings.get("wow64_opt_in"))
    chosen = wine.choose(settings.get("wine"), opt_in=opt_in)
    print("Wine builds found:")
    for b in wine.builds():
        note = "" if wine.allowed(b, opt_in) else "  (waiting for the ArcheAge Classic team's OK)"
        print(f"  {'*' if b == chosen else ' '} {wine.label(b)}  {b}{note}")
    if chosen is None:
        print("The official launcher chooses Wine or Proton itself.")
    else:
        print(f"Using {chosen} with prefix {settings.get('wineprefix') or wine.default_prefix()}")


def cmd_crash_report(args, settings):
    if args.history:
        for h in crash.history():
            causes = ", ".join(h.get("causes") or []) or ("crashed" if h.get("crashed") else "closed normally")
            print(f"{h.get('time')}  {h.get('minutes') or '?':>6} min  {causes}  {h.get('zone') or ''}")
        return
    since = time.time() - args.minutes * 60
    print("Collecting logs, kernel messages and crash dumps…")
    path, findings = crash.collect(settings, since=since)
    print(crash.summarize(findings), end="")
    print(f"\nSaved {path}\nAttach that file when you ask for help.")


def cmd_watch(args, settings):
    """Print the game's memory use against its 4 GB limit until it closes."""
    sampler = crash.Sampler()
    print("Waiting for ArcheAge Classic… (Ctrl+C to stop)")
    while True:
        sampler.sample()
        if sampler.samples and not paths.game_pids():
            used = sampler.peak.get("Space32") or sampler.peak.get("VmPeak", 0)
            print(f"The game closed. Peak: {crash.meter(used)}")
            return
        used = sampler.used_kib()
        if used is not None:
            rss = sampler.last.get("VmRSS", 0) / 1e6
            warn = "  ** close to the crash line: lower textures or log out somewhere safe **" \
                if sampler.near_limit() else ""
            print(f"{time.strftime('%H:%M:%S')}  {crash.meter(used)}  RAM {rss:.2f} GB{warn}", flush=True)
        time.sleep(args.every)


def cmd_report(args, settings):
    print(report.build(settings, log_lines=args.log), end="")


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
    sp = add("doctor", cmd_doctor, "check this system for what the game needs, and how to fix it")
    sp.add_argument("--network", action="store_true", help="also check that HTTPS to GitHub works")
    sp.add_argument("--strict", action="store_true", help="exit with an error if the network check fails")
    sp = add("launcher", cmd_launcher, "show, set or download the official ArcheAge Classic launcher")
    sp.add_argument("--set", metavar="APPIMAGE", help="use this launcher AppImage")
    sp.add_argument("--download", action="store_true", help="download it from aa-classic.com")
    sp.add_argument("--version", default=official.DEFAULT_VERSION, help="launcher version to download")
    sp = add("report", cmd_report, "print a report of your setup and recent log to share when asking for help")
    sp.add_argument("--log", type=int, default=80, metavar="N", help="lines of the launcher's log to include (default 80)")
    sp = add("track", cmd_track, "start updating addons that were installed by hand or by another manager",
             names="*", force=True)
    sp.add_argument("--all", action="store_true", help="track every one that matches the catalogue")
    sp = add("token", cmd_token, "save a GitHub token so updating many addons doesn't hit the 60 an hour limit")
    sp.add_argument("--set", metavar="TOKEN", nargs="?", const="", help="save this token (or read it from stdin)")
    sp.add_argument("--clear", action="store_true", help="forget the saved token")
    sp = add("fixes", cmd_fixes, "show or switch the crash fixes applied when the game starts")
    sp.add_argument("--on", action="append", metavar="FIX", help="turn a fix on (repeatable)")
    sp.add_argument("--off", action="append", metavar="FIX", help="turn a fix off (repeatable)")
    sp.add_argument("--diagnostics", choices=("on", "off"), help="extra Wine, Proton and DXVK logs on the next start")
    sp = add("wine", cmd_wine, "show or choose the Wine build the official launcher runs the game with")
    sp.add_argument("--set", metavar="DIR", help="use this Wine build (a folder with bin/wine)")
    sp.add_argument("--auto", action="store_true", help="pick a Wine-Staging build automatically (default)")
    sp.add_argument("--launcher", action="store_true", help="let the official launcher choose")
    sp.add_argument("--test-wow64", action="store_true",
                    help="download WoW64 Wine and use it on this PC, before the ArcheAge Classic team OKs it (own risk)")
    sp.add_argument("--stop-wow64", action="store_true", help="go back to the normal Wine-Staging build")
    sp.add_argument("--download-wow64", action="store_true",
                    help="download a WoW64 Wine-Staging build (used once the ArcheAge Classic team OKs it)")
    sp.add_argument("--prefix", metavar="DIR", help="Wine prefix (default ~/.local/share/AAClassic/wine-staging-pfx)")
    sp = add("crash-report", cmd_crash_report, "collect evidence after a crash into a zip and name known causes")
    sp.add_argument("--minutes", type=int, default=60, help="how far back to look (default 60)")
    sp.add_argument("--history", action="store_true", help="list recent game sessions: length, crash cause, zone")
    sp = add("watch", cmd_watch, "show the game's memory use against its 4 GB limit while it runs")
    sp.add_argument("--every", type=float, default=10, help="seconds between readings (default 10)")
    sp = add("launch", cmd_launch, "start the game through the official launcher")
    sp.add_argument("--set", metavar="COMMAND",
                    help="start the game with this shell command instead (empty to go back to the official launcher)")
    sp.add_argument("--update-first", action="store_true", help="update addons before launching")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logs.setup(f"aacam {args.command}")
    log = logging.getLogger("aacam.cli")
    try:
        args.fn(args, Settings())
    except (CliError, installer.InstallError, github.GitHubError, official.LauncherError, wine.WineError) as e:
        log.error("%s: %s", args.command, e)
        print(f"error: {e}", file=sys.stderr)
        return 1
    except PermissionError as e:
        log.error("%s: %s", args.command, e)
        print(f"error: no permission to change {e.filename or 'a file'}; check who owns that folder", file=sys.stderr)
        return 1
    except OSError as e:
        log.exception("%s failed", args.command)
        print(f"error: {e.strerror or e}{f' ({e.filename})' if e.filename else ''}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        log.exception("%s crashed", args.command)
        print(f"error: unexpected failure, details are in {logs.path()}\n"
              "Please report it with `aacam report` at https://github.com/Z-arium/AAC-ADDON_MANAGER/issues",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
