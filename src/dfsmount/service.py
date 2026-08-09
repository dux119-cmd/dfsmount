"""User-space service: syncs systemd user mount units with archived games.

Every poll_interval seconds: adds unit files for new archives and removes
them for deleted ones, then arms (starts) a target's overlay mount while
its launcher is running and disarms (stops) it otherwise.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field

from .archive import discover_targets, latest_archive
from .config import require_executable
from .launcher import is_launcher_running
from .models import LauncherConfig, ServiceConfig, TargetPaths
from .systemd import (
    UNIT_DIR,
    daemon_reload,
    disable_service,
    enable_service,
    owned_units,
    owner_tag,
    remove_units,
    render,
    start_units,
    stop_units,
    unit_names,
    write_units,
)

SERVICE_UNIT_NAME = "dfsmount.service"


def _service_unit_content() -> str:
    executable = shutil.which("dfsmount") or f"{sys.executable} -m dfsmount"
    return (
        "[Unit]\n"
        "Description=dfsmount launcher watcher\n\n"
        "[Service]\n"
        f"ExecStart={executable} service\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_service() -> None:
    require_executable("systemctl")
    write_units({SERVICE_UNIT_NAME: _service_unit_content()})
    daemon_reload()
    enable_service(SERVICE_UNIT_NAME)


def remove_service() -> None:
    require_executable("systemctl")
    disable_service(SERVICE_UNIT_NAME)
    (UNIT_DIR / SERVICE_UNIT_NAME).unlink(missing_ok=True)
    daemon_reload()


@dataclass
class _LauncherState:
    targets: set[str] = field(default_factory=set)
    armed: set[str] = field(default_factory=set)


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
    return {
        owner_tag(launcher.name, target)
        for launcher in config.launchers
        for target in discover_targets(launcher.archives_dir)
        if latest_archive(TargetPaths.for_target(launcher, target)) is not None
    }


def _purge_stale_units(config: ServiceConfig) -> None:
    valid = _valid_owners(config)
    stale = [name for name, owner in owned_units().items() if owner not in valid]
    if not stale:
        return
    remove_units(stale)
    daemon_reload()
    for name in stale:
        print(f"[dfsmount] purged stale unit: {name}")


def _add_target(launcher: LauncherConfig, target: str) -> None:
    paths = TargetPaths.for_target(launcher, target)
    archive = latest_archive(paths)
    if archive is None:
        return
    for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    write_units(render(paths, archive, launcher.name))
    daemon_reload()
    print(f"[dfsmount] {launcher.name}/{target}: registered mount units")


def _remove_target(launcher: LauncherConfig, target: str) -> None:
    names = unit_names(TargetPaths.for_target(launcher, target))
    remove_units(names.all_names())
    daemon_reload()
    print(f"[dfsmount] {launcher.name}/{target}: removed mount units")


def _arm_target(launcher: LauncherConfig, target: str) -> None:
    start_units([unit_names(TargetPaths.for_target(launcher, target)).overlay_mount])
    print(f"[dfsmount] {launcher.name}/{target}: mounted")


def _disarm_target(launcher: LauncherConfig, target: str) -> None:
    stop_units([unit_names(TargetPaths.for_target(launcher, target)).overlay_mount])
    print(f"[dfsmount] {launcher.name}/{target}: unmounted")
