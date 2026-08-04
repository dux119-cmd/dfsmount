"""dfsmount: convert, service, mount, unmount, repack, status."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import archive, service
from . import mount as mount_mod
from . import repack as repack_mod
from .config import (
    default_config_path,
    find_process,
    load_config,
    resolve_run_as,
    resolve_user_path,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("dfsmount: this command requires root (fanotify needs it)")


def _target_paths(args: argparse.Namespace):
    config = load_config(Path(args.config))
    proc = find_process(config, args.process)
    return config, service.target_paths(proc, args.target)


def cmd_convert(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    proc = find_process(config, args.process)
    source = resolve_user_path(args.source, Path.home())
    target = archive.target_from_source(source)
    output = archive.create_archive(
        source, proc.archives_dir, target, hooks=proc.hooks
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


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c", "--config", default=str(default_config_path()), help="path to config.yaml"
    )


def _add_process_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("process", help="process name as configured in config.yaml")
    parser.add_argument("target", help="target name (archive basename, e.g. 'assets')")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfsmount")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("convert", help="pack a directory into a new archive revision")
    _add_config_arg(p)
    p.add_argument("process", help="process name as configured in config.yaml")
    p.add_argument(
        "source", help="directory to archive; target name is its final path component"
    )
    p.set_defaults(func=cmd_convert)

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
    _add_process_target_args(p)
    p.set_defaults(func=cmd_mount)

    p = sub.add_parser("unmount", help="tear down a target's mount")
    _add_config_arg(p)
    _add_process_target_args(p)
    p.set_defaults(func=cmd_unmount)

    p = sub.add_parser(
        "repack", help="write a new archive revision from the live mount"
    )
    _add_config_arg(p)
    _add_process_target_args(p)
    p.set_defaults(func=cmd_repack)

    p = sub.add_parser(
        "status", help="show mount status and latest revision for a target"
    )
    _add_config_arg(p)
    _add_process_target_args(p)
    p.set_defaults(func=cmd_status)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
