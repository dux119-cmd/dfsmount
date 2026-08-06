"""dfsmount: create, service, mount, unmount, repack, status, install, uninstall."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import archive, service
from . import binaries as binaries_mod
from . import hooks as hooks_mod
from . import mount as mount_mod
from . import repack as repack_mod
from .config import (
    default_config_path,
    find_launcher,
    load_config,
    resolve_run_as,
    resolve_user_path,
)
from .games import list_games


def _require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("dfsmount: this command requires root (fanotify needs it)")


def _target_paths(args: argparse.Namespace):
    config = load_config(Path(args.config))
    launcher = find_launcher(config, args.launcher)
    return config, service.target_paths(launcher, args.target)


def _list_games(launcher) -> None:
    games = list_games(launcher)
    if not games:
        print(f"no games found in {launcher.target_mount_dir}")
        return
    for game in games:
        marker = " (repackable)" if game.repackable else ""
        print(f"{game.name}{marker}")


def cmd_create(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    launcher = find_launcher(config, args.launcher)

    if args.source is None:
        _list_games(launcher)
        return

    source = resolve_user_path(args.source, Path.home())
    target = archive.target_from_source(source)
    output = archive.create_archive(
        source, launcher.archives_dir, target, hooks=launcher.hooks
    )
    print(f"created {output} (target: {target})")


def cmd_service(args: argparse.Namespace) -> None:
    _require_root()
    config = load_config(Path(args.config))
    run_as = resolve_run_as(config, args.user)
    service.run(config, run_as)


def cmd_mount(args: argparse.Namespace) -> None:
    _config, paths = _target_paths(args)
    mount_mod.mount(paths)
    print(f"{paths.mount_dir} mounted")


def cmd_unmount(args: argparse.Namespace) -> None:
    _config, paths = _target_paths(args)
    mount_mod.unmount(paths)
    print(f"{paths.mount_dir} unmounted")


def cmd_repack(args: argparse.Namespace) -> None:
    _config, paths = _target_paths(args)
    output = repack_mod.repack(paths)
    print(f"created {output}")


def cmd_status(args: argparse.Namespace) -> None:
    _config, paths = _target_paths(args)
    state = "mounted" if mount_mod.is_mounted(paths.mount_dir) else "not mounted"
    latest = archive.latest_archive(paths.archives_dir, paths.target)
    print(f"{paths.mount_dir}: {state}")
    print(f"  latest archive: {latest}")


def _install_targets(launcher, target: str) -> list[str]:
    """`target` names a single game, or "all" for every game known to the launcher."""
    if target == "all":
        return [game.name for game in list_games(launcher)]
    return [target]


def _run_install_hook(args: argparse.Namespace, hook_name: str, verb: str) -> None:
    config = load_config(Path(args.config))
    launcher = find_launcher(config, args.launcher)
    for name in _install_targets(launcher, args.target):
        paths = service.target_paths(launcher, name)
        hooks_mod.run_hook(getattr(paths.hooks, hook_name), paths.mount_dir)
        print(f"{verb} {name}")


def cmd_fetch_binaries(args: argparse.Namespace) -> None:
    version_dir = binaries_mod.fetch_release(force=args.force)
    print(f"dwarfs binaries ready: {version_dir}")


def cmd_install(args: argparse.Namespace) -> None:
    _run_install_hook(args, "install", "installed")


def cmd_uninstall(args: argparse.Namespace) -> None:
    _run_install_hook(args, "uninstall", "uninstalled")


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        default=str(default_config_path()),
        help="path to config.yaml",
    )


def _add_launcher_target_args(
    parser: argparse.ArgumentParser, target_help: str | None = None
) -> None:
    parser.add_argument("launcher", help="launcher name as configured in config.yaml")
    parser.add_argument(
        "target",
        help=target_help or "target name (archive basename, e.g. 'assets')",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfsmount")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="pack a directory into a new archive revision")
    _add_config_arg(p)
    p.add_argument("launcher", help="launcher name as configured in config.yaml")
    p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="directory to archive; target name is its final path "
        "component. Omit to list this launcher's game names instead "
        "(flagging which ones can be repacked)",
    )
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("service", help="run the watch/mount/reap loop from config.yaml")
    _add_config_arg(p)
    p.add_argument(
        "-u",
        "--user",
        help="user whose credentials mounts/archives run under "
        "(overrides config's run_as)",
    )
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("mount", help="mount a target's latest archive immediately")
    _add_config_arg(p)
    _add_launcher_target_args(p)
    p.set_defaults(func=cmd_mount)

    p = sub.add_parser("unmount", help="tear down a target's mount")
    _add_config_arg(p)
    _add_launcher_target_args(p)
    p.set_defaults(func=cmd_unmount)

    p = sub.add_parser(
        "repack", help="write a new archive revision from the live mount"
    )
    _add_config_arg(p)
    _add_launcher_target_args(p)
    p.set_defaults(func=cmd_repack)

    p = sub.add_parser(
        "status", help="show mount status and latest revision for a target"
    )
    _add_config_arg(p)
    _add_launcher_target_args(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "fetch-binaries",
        help="download mkdwarfs/dwarfs/dwarfsck/dwarfsextract from "
        "github.com/mhx/dwarfs/releases instead of using the system package manager",
    )
    p.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="re-download and re-extract even if already fetched",
    )
    p.set_defaults(func=cmd_fetch_binaries)

    p = sub.add_parser(
        "install", help="run the install hook for a target, or 'all' targets"
    )
    _add_config_arg(p)
    _add_launcher_target_args(
        p, target_help="target name, or 'all' for every game the launcher knows"
    )
    p.set_defaults(func=cmd_install)

    p = sub.add_parser(
        "uninstall", help="run the uninstall hook for a target, or 'all' targets"
    )
    _add_config_arg(p)
    _add_launcher_target_args(
        p, target_help="target name, or 'all' for every game the launcher knows"
    )
    p.set_defaults(func=cmd_uninstall)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
