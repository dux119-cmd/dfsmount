"""dfsmount: create, service, mount, unmount, repack, status, install, uninstall.

Invocation shape: `dfsmount [launcher] <action> [target] [options]`. The
launcher name can be omitted if only one is configured. `service` and
`fetch-binaries` take no launcher (service runs for every configured
launcher; fetch-binaries isn't launcher-scoped at all). Leaving off `target`
where one applies lists the short names available for that action instead
of acting on one.

`service` is a plain user-space process - it generates systemd user mount
units for each launcher's archived games and starts/stops them as the
launcher runs; see user_service.py.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from types import FrameType

from . import archive, user_service
from . import binaries as binaries_mod
from . import hooks as hooks_mod
from . import mount as mount_mod
from . import repack as repack_mod
from .config import (
    LauncherConfig,
    ServiceConfig,
    default_config_path,
    ensure_config_exists,
    load_config,
    resolve_user_path,
)
from .games import list_games, list_mounted

LAUNCHER_FREE_COMMANDS = ("service", "fetch-binaries")
ACTIONS = (
    "create",
    "mount",
    "unmount",
    "repack",
    "status",
    "install",
    "uninstall",
)


def _print_names(names: list[str], empty_message: str) -> None:
    if not names:
        print(empty_message)
        return
    for name in names:
        print(name)


def _list_games(launcher: LauncherConfig) -> None:
    games = list_games(launcher)
    if not games:
        print(f"no games found in {launcher.target_mount_dir}")
        return
    for game in games:
        marker = " (repackable)" if game.repackable else ""
        print(f"{game.name}{marker}")


def _list_available(launcher: LauncherConfig) -> None:
    """Archive target names available to mount for `launcher`."""
    names = sorted(archive.discover_targets(launcher.archives_dir))
    _print_names(names, f"no archives found in {launcher.archives_dir}")


def _list_mounted(launcher: LauncherConfig) -> None:
    """Target names currently mounted for `launcher`."""
    _print_names(
        list_mounted(launcher), f"nothing mounted under {launcher.target_mount_dir}"
    )


def _list_repackable(launcher: LauncherConfig) -> None:
    """Target names with an archive and unbaked overlay content -
    repack candidates, whether or not they're currently mounted."""
    names = sorted(game.name for game in list_games(launcher) if game.repackable)
    _print_names(names, f"nothing repackable for {launcher.name}")


def _list_status(launcher: LauncherConfig) -> None:
    names = sorted(
        archive.discover_targets(launcher.archives_dir) | set(list_mounted(launcher))
    )
    if not names:
        print(f"no targets found for {launcher.name}")
        return
    for name in names:
        paths = user_service.target_paths(launcher, name)
        state = "mounted" if mount_mod.is_mounted(paths.mount_dir) else "not mounted"
        print(f"{name}: {state}")


def cmd_create(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.source is None:
        _list_games(launcher)
        return

    source = None
    if args.source.startswith("~"):
        source = resolve_user_path(args.source, Path.home())
    elif Path(args.source).is_absolute():
        source = args.source
    else:
        source = launcher.target_mount_dir / args.source
        assert source.is_absolute()

    target = archive.target_from_source(source)
    output = archive.create_archive(
        source, launcher.archives_dir, target, hooks=launcher.hooks
    )
    archived_dir = archive.archive_source_dir(source)
    print(f"created {output} (target: {target})")
    print(f"moved {source} -> {archived_dir}")


def cmd_service(config: ServiceConfig) -> None:
    user_service.run(config)


def cmd_mount(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_available(launcher)
        return
    paths = user_service.target_paths(launcher, args.target)
    mount_mod.mount(paths)
    print(f"{paths.mount_dir} mounted")


def cmd_unmount(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_mounted(launcher)
        return
    paths = user_service.target_paths(launcher, args.target)
    mount_mod.unmount(paths)
    print(f"{paths.mount_dir} unmounted")


def cmd_repack(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_repackable(launcher)
        return
    paths = user_service.target_paths(launcher, args.target)
    output = repack_mod.repack(paths)
    print(f"created {output}")


def cmd_status(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_status(launcher)
        return
    paths = user_service.target_paths(launcher, args.target)
    state = "mounted" if mount_mod.is_mounted(paths.mount_dir) else "not mounted"
    latest = archive.latest_archive(paths.archives_dir, paths.target)
    print(f"{paths.mount_dir}: {state}")
    print(f"  latest archive: {latest}")


def _install_targets(launcher: LauncherConfig, target: str) -> list[str]:
    """`target` names a single game, or "all" for every game known to the launcher."""
    if target == "all":
        return [game.name for game in list_games(launcher)]
    return [target]


def _run_install_hook(
    launcher: LauncherConfig, target: str, hook_name: str, verb: str
) -> None:
    for name in _install_targets(launcher, target):
        paths = user_service.target_paths(launcher, name)
        hooks_mod.run_hook(getattr(paths.hooks, hook_name), paths.mount_dir)
        print(f"{verb} {name}")


def cmd_install(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_games(launcher)
        return
    _run_install_hook(launcher, args.target, "install", "installed")


def cmd_uninstall(launcher: LauncherConfig, args: argparse.Namespace) -> None:
    if args.target is None:
        _list_games(launcher)
        return
    _run_install_hook(launcher, args.target, "uninstall", "uninstalled")


def cmd_fetch_binaries(args: argparse.Namespace) -> None:
    version_dir = binaries_mod.fetch_release(force=args.force)
    print(f"dwarfs binaries ready: {version_dir}")


def _target_parser(prog: str, help_: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, add_help=True)
    p.add_argument("target", nargs="?", default=None, help=help_)
    return p


def _parse_global(argv: list[str]) -> tuple[Path, list[str]]:
    """Pull -c/--config out of argv (it may appear anywhere); return the
    resolved config path and the remaining tokens."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-c", "--config", default=None)
    ns, rest = p.parse_known_args(argv)
    config_path = Path(ns.config) if ns.config else default_config_path()
    return config_path, rest


def _resolve_launcher(
    config: ServiceConfig, rest: list[str]
) -> tuple[LauncherConfig, list[str]]:
    """Consume a leading launcher-name token from `rest` if present, else
    default to the sole configured launcher, else list what's configured."""
    names = [launcher.name for launcher in config.launchers]

    if rest and rest[0] in names:
        launcher = next(item for item in config.launchers if item.name == rest[0])
        return launcher, rest[1:]

    if len(config.launchers) == 1:
        return config.launchers[0], rest

    if not config.launchers:
        sys.exit("dfsmount: no launchers configured in config.yaml")

    sys.exit("dfsmount: specify a launcher:\n" + "\n".join(f"  {n}" for n in names))


def _dispatch_launcher_command(
    launcher: LauncherConfig, action: str, action_args: list[str]
) -> None:
    if action == "create":
        p = argparse.ArgumentParser(prog="dfsmount create", add_help=True)
        p.add_argument(
            "source",
            nargs="?",
            default=None,
            help="directory to archive; target name is its final path "
            "component. Omit to list this launcher's game names instead "
            "(flagging which ones can be repacked)",
        )
        cmd_create(launcher, p.parse_args(action_args))
        return

    if action == "mount":
        args = _target_parser(
            "dfsmount mount", "target name; omit to list available archives"
        ).parse_args(action_args)
        cmd_mount(launcher, args)
        return

    if action == "unmount":
        args = _target_parser(
            "dfsmount unmount", "target name; omit to list mounted targets"
        ).parse_args(action_args)
        cmd_unmount(launcher, args)
        return

    if action == "repack":
        args = _target_parser(
            "dfsmount repack", "target name; omit to list repackable targets"
        ).parse_args(action_args)
        cmd_repack(launcher, args)
        return

    if action == "status":
        args = _target_parser(
            "dfsmount status", "target name; omit to list all known targets"
        ).parse_args(action_args)
        cmd_status(launcher, args)
        return

    if action == "install":
        args = _target_parser(
            "dfsmount install",
            "target name, or 'all' for every game the launcher knows; "
            "omit to list game names",
        ).parse_args(action_args)
        cmd_install(launcher, args)
        return

    if action == "uninstall":
        args = _target_parser(
            "dfsmount uninstall",
            "target name, or 'all' for every game the launcher knows; "
            "omit to list game names",
        ).parse_args(action_args)
        cmd_uninstall(launcher, args)
        return

    sys.exit(f"dfsmount: unknown action {action!r} (choose from: {', '.join(ACTIONS)})")


def main(argv: list[str] | None = None) -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        _main(argv)
    except KeyboardInterrupt:
        print("\ndfsmount: stopped")
        sys.exit(130)  # conventional exit code for SIGINT


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _main(argv: list[str] | None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    config_path, rest = _parse_global(argv)
    ensure_config_exists(config_path)

    if not rest:
        sys.exit(
            "dfsmount: missing command "
            f"(choose from: {', '.join((*LAUNCHER_FREE_COMMANDS, *ACTIONS))})"
        )

    if rest[0] == "service":
        argparse.ArgumentParser(prog="dfsmount service", add_help=True).parse_args(
            rest[1:]
        )
        cmd_service(load_config(config_path))
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
