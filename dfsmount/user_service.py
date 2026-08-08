"""User-space service: keeps systemd user mount units in sync with a
launcher's archived games.

No socket, no fanotify, no root component, and no kernel autofs (see
systemd_units.py for why). Every `poll_interval` seconds, this service:

- adds unit files for newly-created archives, and removes them entirely
  for archives that have been deleted
- starts a target's overlay mount (bringing dwarfs up with it) while its
  launcher is running
- stops it the moment the launcher exits

Mounting itself (`dfsmount mount`/`unmount` outside this service) still
runs dwarfs/fuse-overlayfs directly - this service only ever manages unit
files and calls systemctl.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .archive import discover_targets, latest_archive
from .config import LauncherConfig, ServiceConfig, require_executable
from .launcher import is_launcher_running
from .mount import TargetPaths
from .systemd_units import owner_tag, render, unit_names
from .unit_sync import (
    daemon_reload,
    owned_units,
    remove_units,
    start_units,
    stop_units,
    write_units,
)


def target_paths(launcher: LauncherConfig, target: str) -> TargetPaths:
    return TargetPaths(
        target=target,
        archives_dir=launcher.archives_dir,
        mount_dir=launcher.target_mount_dir / target,
        ro_mount=launcher.working_dir / target / "ro",
        upper=launcher.working_dir / target / "upper",
        work=launcher.working_dir / target / "work",
        hooks=launcher.hooks,
    )


@dataclass
class _LauncherState:
    targets: set[str] = field(default_factory=set)
    armed: set[str] = field(default_factory=set)  # targets currently enabled


def run(config: ServiceConfig) -> None:
    require_executable("systemctl")
    require_executable("systemd-escape")

    _purge_stale_units(config)
    state = {launcher.name: _LauncherState() for launcher in config.launchers}
    while True:
        for launcher in config.launchers:
            _reconcile_launcher(launcher, state[launcher.name])
        time.sleep(config.poll_interval)


def _reconcile_launcher(launcher: LauncherConfig, state: _LauncherState) -> None:
    current = discover_targets(launcher.archives_dir)
    running = is_launcher_running(launcher.name)

    for target in current - state.targets:
        _add_target(launcher, target)
    for target in state.targets - current:
        _remove_target(launcher, target)
        state.armed.discard(target)
    state.targets = current

    desired_armed = current if running else set()
    for target in desired_armed - state.armed:
        _arm_target(launcher, target)
    for target in state.armed - desired_armed:
        _disarm_target(launcher, target)
    state.armed = desired_armed


def _valid_owners(config: ServiceConfig) -> set[str]:
    """owner tags for every (launcher, target) that currently has an
    archive - i.e. everything _add_target would (re)create units for."""
    return {
        owner_tag(launcher.name, target)
        for launcher in config.launchers
        for target in discover_targets(launcher.archives_dir)
        if latest_archive(launcher.archives_dir, target) is not None
    }


def _purge_stale_units(config: ServiceConfig) -> None:
    """Remove dfsmount-owned unit files left over from a launcher or
    target that no longer exists (renamed launcher, deleted archive
    directory, edited config, ...). Only touches units carrying our
    `X-Dfsmount-Owner=` marker - never units dfsmount didn't create."""
    valid = _valid_owners(config)
    stale = [name for name, owner in owned_units().items() if owner not in valid]
    if not stale:
        return
    remove_units(stale)
    daemon_reload()
    for name in stale:
        print(f"[dfsmount] purged stale unit: {name}")


def _add_target(launcher: LauncherConfig, target: str) -> None:
    paths = target_paths(launcher, target)
    archive = latest_archive(paths.archives_dir, target)
    if archive is None:
        return
    for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    write_units(render(paths, archive, launcher.name))
    daemon_reload()
    print(f"[dfsmount] {launcher.name}/{target}: registered mount units")


def _remove_target(launcher: LauncherConfig, target: str) -> None:
    names = unit_names(target_paths(launcher, target))
    remove_units(names.all_names())
    daemon_reload()
    print(f"[dfsmount] {launcher.name}/{target}: removed mount units")


def _arm_target(launcher: LauncherConfig, target: str) -> None:
    names = unit_names(target_paths(launcher, target))
    start_units([names.overlay_mount])
    print(f"[dfsmount] {launcher.name}/{target}: mounted")


def _disarm_target(launcher: LauncherConfig, target: str) -> None:
    names = unit_names(target_paths(launcher, target))
    stop_units([names.overlay_mount])
    print(f"[dfsmount] {launcher.name}/{target}: unmounted")
