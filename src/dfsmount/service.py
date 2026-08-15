"""User-space service: syncs systemd user mount units with archived games.

Every poll_interval seconds: adds unit files for new archives and removes
them for deleted ones, then arms (starts) a target's overlay mount while
its launcher is running and disarms (stops) it otherwise. When a target is
visible to more than one launcher (a shared archives_dir), only one running
launcher may arm it at a time - see `_resolve_owners`.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_config, require_executable
from .launcher import is_launcher_running
from .models import LauncherConfig, ServiceConfig, TargetPaths
from .mount import cleanup_unmounted
from .pack import discover_targets, latest_pack
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


def _config_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _reload_if_changed(
    path: Path,
    last_mtime: float | None,
    config: ServiceConfig,
    state: dict[str, _LauncherState],
) -> tuple[ServiceConfig, float | None, dict[str, _LauncherState]]:
    mtime = _config_mtime(path)
    if mtime == last_mtime:
        return config, last_mtime, state

    try:
        config = load_config(path)
    except Exception as exc:  # keep serving the old config on a bad edit
        print(f"[dfsmount] config reload failed, keeping previous config: {exc}")
        return config, mtime, state

    state = {
        launcher.name: state.get(launcher.name, _LauncherState())
        for launcher in config.launchers
    }
    _purge_stale_units(config)
    print(f"[dfsmount] reloaded config from {path}")
    return config, mtime, state


def run(config_path: Path) -> None:
    require_executable("systemctl")
    require_executable("systemd-escape")

    config = load_config(config_path)
    config_mtime = _config_mtime(config_path)
    _purge_stale_units(config)
    state = {launcher.name: _LauncherState() for launcher in config.launchers}

    while True:
        config, config_mtime, state = _reload_if_changed(
            config_path, config_mtime, config, state
        )
        owners = _resolve_owners(config)
        for launcher in config.launchers:
            _reconcile_launcher(launcher, state[launcher.name], owners)
        time.sleep(config.poll_interval)


def _resolve_owners(config: ServiceConfig) -> dict[tuple[Path, str], str]:
    """
    Map each (archives_dir, target) to the first running launcher
    allowed to mount it.
    """
    owners: dict[tuple[Path, str], str] = {}
    for launcher in config.launchers:
        if not is_launcher_running(launcher.name):
            continue
        for target in discover_targets(launcher.archives_dir):
            owners.setdefault((launcher.archives_dir, target), launcher.name)
    return owners


def _reconcile_launcher(
    launcher: LauncherConfig,
    state: _LauncherState,
    owners: dict[tuple[Path, str], str],
) -> None:
    current = discover_targets(launcher.archives_dir)
    running = is_launcher_running(launcher.name)

    for target in current - state.targets:
        _add_target(launcher, target)
    for target in state.targets - current:
        _remove_target(launcher, target)
        state.armed.discard(target)
    state.targets = current

    if running:
        desired_armed = {
            target
            for target in current
            if owners.get((launcher.archives_dir, target)) == launcher.name
        }
    else:
        desired_armed = set()
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
        if latest_pack(TargetPaths.for_target(launcher, target)) is not None
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
    pack_file = latest_pack(paths)
    if pack_file is None:
        return
    for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    write_units(render(paths, pack_file, launcher.name))
    daemon_reload()
    print(f"[dfsmount] {launcher.name}/{target}: registered mount units")


def _remove_target(launcher: LauncherConfig, target: str) -> None:
    paths = TargetPaths.for_target(launcher, target)
    remove_units(unit_names(paths).all_names())
    daemon_reload()
    cleanup_unmounted(paths)
    print(f"[dfsmount] {launcher.name}/{target}: removed mount units")


def _arm_target(launcher: LauncherConfig, target: str) -> None:
    start_units([unit_names(TargetPaths.for_target(launcher, target)).overlay_mount])
    print(f"[dfsmount] {launcher.name}/{target}: mounted")


def _disarm_target(launcher: LauncherConfig, target: str) -> None:
    paths = TargetPaths.for_target(launcher, target)
    stop_units([unit_names(paths).overlay_mount])
    cleanup_unmounted(paths)
    print(f"[dfsmount] {launcher.name}/{target}: unmounted")
