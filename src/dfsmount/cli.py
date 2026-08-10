"""dfsmount actions, grouped in three kinds:

- one-time setup (no launcher): service-install, service-remove,
  fetch-binaries, install-bin.
- packs (per launcher): pack, repack, install, remove.
- mounts (per launcher): mount, unmount, status. `service` also lives here
  conceptually - it's what drives mount/unmount automatically - but like
  the setup commands it takes no launcher argument.

Invocation: `dfsmount [launcher] <action> [target] [options]`. Launcher can
be omitted if only one is configured. `service`/`service-install`/
`service-remove`/`fetch-binaries`/`install-bin` take no launcher. Omitting
`target` where one applies lists names instead of acting.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from types import FrameType

from . import archive, bin_link, service
from . import binaries as binaries_mod
from . import hooks as hooks_mod
from . import mount as mount_mod
from . import repack as repack_mod
from .config import (
    default_config_path,
    ensure_config_exists,
    load_config,
    resolve_user_path,
)
from .games import list_games, list_mounted, list_packable
from .models import LauncherConfig, ServiceConfig, TargetPaths

# One-time setup: install/remove the background service, fetch the dwarfs
# binaries, and put the `dfsmount` launcher on PATH. Take no launcher.
SETUP_COMMANDS = (
    "service-install",
    "service-remove",
    "fetch-binaries",
    "install-bin",
)
LAUNCHER_FREE_COMMANDS = ("service", *SETUP_COMMANDS)

# Packs: create, refresh, and (un)apply archived game content.
PACK_ACTIONS = ("pack", "repack", "install", "remove")
# Mounts: bring archived content on/offline and inspect its state. The
# background `service` performs the mount/unmount half of this group
# automatically; these actions are for doing it by hand.
MOUNT_ACTIONS = ("mount", "unmount", "status")

ACTIONS = (*PACK_ACTIONS, *MOUNT_ACTIONS)


def _print_names(names: list[str], empty_message: str) -> None:
    if not names:
        print(empty_message)
        return
    for name in names:
        print(name)


def _list_packable(launcher: LauncherConfig) -> None:
    names = list_packable(launcher)
    _print_names(names, f"nothing packable in {launcher.target_mount_dir}")


def _list_available(launcher: LauncherConfig) -> None:
    names = sorted(archive.discover_targets(launcher.archives_dir))
    _print_names(names, f"no archives found in {launcher.archives_dir}")


def _list_mounted(launcher: LauncherConfig) -> None:
    _print_names(
        list_mounted(launcher), f"nothing mounted under {launcher.target_mount_dir}"
    )


def _list_repackable(launcher: LauncherConfig) -> None:
    names = sorted(game.name for game in list_games(launcher) if game.repackable)
    _print_names(names, f"nothing repackable for {launcher.name}")


def _list_status(launcher: LauncherConfig) -> None:
    games = list_games(launcher)
    if not games:
        print(f"no targets found for {launcher.name}")
        return
    for game in games:
        paths = TargetPaths.for_target(launcher, game.name)
        if mount_mod.is_mounted(paths.mount_dir):
            state = "mounted"
        elif game.repackable:
            state = "repackable"
        elif archive.latest_archive(paths) is not None:
            state = "mountable"
        else:
            state = "archiveable"
        print(f"{game.name}: {state}")


def cmd_pack(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.source is None:
        _list_packable(launcher)
        return

    if args.source.startswith("~"):
        source = resolve_user_path(args.source, Path.home())
    elif Path(args.source).is_absolute():
        source = Path(args.source)
    else:
        source = launcher.target_mount_dir / args.source

    target = archive.target_from_source(source)
    paths = TargetPaths.for_target(launcher, target)
    output = archive.create_archive(paths, source)
    archived_dir = archive.archive_source_dir(source)
    print(f"created {output} (target: {target})")
    print(f"moved {source} -> {archived_dir}")


def cmd_service(config_path: Path) -> None:
    service.run(config_path)


def cmd_install_bin() -> None:
    link = bin_link.install_symlink()
    print(f"linked {link} -> {link.resolve()}")
    if not bin_link.bin_dir_on_path():
        print(
            f"warning: {bin_link.BIN_DIR} is not on your PATH.\n"
            '  add this to your shell profile: export PATH="$HOME/.local/bin:$PATH"'
        )


def cmd_mount(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_available(launcher)
        return
    paths = TargetPaths.for_target(launcher, args.target)
    mount_mod.mount(paths)
    print(f"{paths.mount_dir} mounted")


def cmd_unmount(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_mounted(launcher)
        return
    paths = TargetPaths.for_target(launcher, args.target)
    mount_mod.unmount(paths)
    print(f"{paths.mount_dir} unmounted")


def cmd_repack(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_repackable(launcher)
        return
    paths = TargetPaths.for_target(launcher, args.target)
    output = repack_mod.repack(paths)
    print(f"created {output}")


def cmd_status(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_status(launcher)
        return
    paths = TargetPaths.for_target(launcher, args.target)
    state = "mounted" if mount_mod.is_mounted(paths.mount_dir) else "not mounted"
    latest = archive.latest_archive(paths)
    print(f"{paths.mount_dir}: {state}")
    print(f"  latest archive: {latest}")


def _install_targets(launcher: LauncherConfig, target: str) -> list[str]:
    if target == "all":
        return sorted(archive.discover_targets(launcher.archives_dir))
    return [target]


def _run_install_hook(
    launcher: LauncherConfig, target: str, hook_name: str, verb: str
) -> None:
    for name in _install_targets(launcher, target):
        paths = TargetPaths.for_target(launcher, name)
        mount_mod.mount(paths)
        hooks_mod.run_hook(getattr(paths.hooks, hook_name), paths.mount_dir)
        if hook_name == "remove":
            mount_mod.unmount(paths)
        print(f"{verb} {name}")


def cmd_install(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_available(launcher)
        return
    _run_install_hook(launcher, args.target, "install", "installed")


def cmd_remove(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_available(launcher)
        return
    _run_install_hook(launcher, args.target, "remove", "removed")


def cmd_fetch_binaries(args: argparse.Namespace) -> None:
    version_dir = binaries_mod.fetch_release(force=args.force)
    print(f"dwarfs binaries ready: {version_dir}")


def _target_parser(prog: str, help_: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, add_help=True)
    p.add_argument("target", nargs="?", default=None, help=help_)
    return p


def _parse_global(argv: list[str]) -> tuple[Path, list[str]]:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-c", "--config", default=None)
    ns, rest = p.parse_known_args(argv)
    config_path = Path(ns.config) if ns.config else default_config_path()
    return config_path, rest


def _resolve_launcher(
    config: ServiceConfig, rest: list[str]
) -> tuple[LauncherConfig, list[str]]:
    names = [launcher.name for launcher in config.launchers]

    if rest and rest[0] in names:
        launcher = next(item for item in config.launchers if item.name == rest[0])
        return launcher, rest[1:]

    if len(config.launchers) == 1:
        return config.launchers[0], rest

    if not config.launchers:
        sys.exit("dfsmount: no launchers configured in config.yaml")

    sys.exit("dfsmount: specify a launcher:\n" + "\n".join(f"  {n}" for n in names))


_ACTION_HELP = {
    "mount": "game; omit to list available archives",
    "unmount": "game; omit to list mounted targets",
    "repack": "game; omit to list repackable targets",
    "status": "name; omit to list archiveable/repackable/mountable/mounted state",
    "install": "name, or 'all'; omit to list available archives",
    "remove": "name, or 'all'; omit to list available archives",
}
_ACTION_HANDLERS = {
    "mount": cmd_mount,
    "unmount": cmd_unmount,
    "repack": cmd_repack,
    "status": cmd_status,
    "install": cmd_install,
    "remove": cmd_remove,
}


def _dispatch_launcher_command(
    launcher: LauncherConfig, action: str, action_args: list[str]
) -> None:
    if action == "pack":
        p = argparse.ArgumentParser(prog="dfsmount pack", add_help=True)
        p.add_argument(
            "source",
            nargs="?",
            default=None,
            help="directory to archive; omit to list packable game names",
        )
        cmd_pack(launcher, p.parse_args(action_args))
        return

    if action in _ACTION_HANDLERS:
        args = _target_parser(f"dfsmount {action}", _ACTION_HELP[action]).parse_args(
            action_args
        )
        _ACTION_HANDLERS[action](launcher, args)
        return

    sys.exit(f"dfsmount: unknown action {action!r} (choose from: {', '.join(ACTIONS)})")


def main(argv: list[str] | None = None) -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        _main(argv)
    except KeyboardInterrupt:
        print("\ndfsmount: stopped")
        sys.exit(130)


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _main(argv: list[str] | None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    config_path, rest = _parse_global(argv)
    ensure_config_exists(config_path)

    if not rest:
        sys.exit(
            "dfsmount: missing command\n"
            f"  setup:  {', '.join(SETUP_COMMANDS)}\n"
            f"  packs:  {', '.join(PACK_ACTIONS)}\n"
            f"  mounts: {', '.join(MOUNT_ACTIONS)} (or run `service` in the background)"
        )

    if rest[0] == "service":
        argparse.ArgumentParser(prog="dfsmount service", add_help=True).parse_args(
            rest[1:]
        )
        cmd_service(config_path)
        return

    if rest[0] == "install-bin":
        argparse.ArgumentParser(prog="dfsmount install-bin", add_help=True).parse_args(
            rest[1:]
        )
        cmd_install_bin()
        return

    if rest[0] in ("service-install", "service-remove"):
        argparse.ArgumentParser(prog=f"dfsmount {rest[0]}", add_help=True).parse_args(
            rest[1:]
        )
        if rest[0] == "service-install":
            service.install_service()
        else:
            service.remove_service()
        return

    if rest[0] == "fetch-binaries":
        p = argparse.ArgumentParser(prog="dfsmount fetch-binaries", add_help=True)
        p.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="re-download and re-extract even if already fetched",
        )
        cmd_fetch_binaries(p.parse_args(rest[1:]))
        return

    config = load_config(config_path)
    launcher, cmd_args = _resolve_launcher(config, rest)

    if not cmd_args:
        sys.exit(f"dfsmount: missing action (choose from: {', '.join(ACTIONS)})")

    action, action_args = cmd_args[0], cmd_args[1:]
    _dispatch_launcher_command(launcher, action, action_args)


if __name__ == "__main__":
    main()
