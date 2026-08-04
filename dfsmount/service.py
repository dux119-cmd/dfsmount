"""Long-running service loop.

Every `poll_interval` seconds, check which configured processes are running.
For each running process, arm a fanotify watch (if not already watched or
mounted) on each of its targets; for each that has stopped, disarm the
watch. Independently of launcher liveness, every currently-mounted target is
checked for actual use (open fds/mmaps/cwd under it) and reaped the moment
it's idle - so a mount is torn down as soon as nothing is using it, even if
its launcher is still running, and stays up as long as something (e.g. the
game itself) is still using it after the launcher exits.

Fanotify events themselves are handled immediately via select() - only the
process-liveness/idle checks are throttled to poll_interval.

This process runs as root (required for fanotify). Actual mounting,
unmounting, and archive creation run under the configured `run_as` user's
credentials via privsep.as_user - fuse-overlayfs and dwarfs need no root.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from dataclasses import dataclass

from .archive import discover_targets
from .config import ProcessConfig, ServiceConfig, require_executable
from .fanotify import FAN_OPEN_PERM, Fanotify
from .mount import TargetPaths, is_mounted, mount, unmount
from .privsep import UserCreds
from .process import is_mount_busy, is_process_running


def target_paths(proc: ProcessConfig, target: str) -> TargetPaths:
    return TargetPaths(
        target=target,
        archives_dir=proc.archives_dir,
        mount_dir=proc.target_mount_dir / target,
        ro_mount=proc.working_dir / target / "ro",
        upper=proc.working_dir / target / "upper",
        work=proc.working_dir / target / "work",
        hooks=proc.hooks,
    )


@dataclass
class _Watch:
    fan: Fanotify
    paths: TargetPaths


def _bulk_reap_at_startup(config: ServiceConfig, run_as: UserCreds) -> None:
    """Once, on service startup: scan every configured process's archives_dir
    for targets, and unmount any that are already mounted (e.g. left over from
    a prior service run) and idle. Mounts still in use are left alone; the
    normal reconcile loop will reap them once they go idle.
    """
    for proc in config.processes:
        for target in discover_targets(proc.archives_dir):
            paths = target_paths(proc, target)
            if not is_mounted(paths.mount_dir):
                continue
            if is_mount_busy(paths.mount_dir):
                print(f"[dfsmount] startup scan: {paths.mount_dir} busy; leaving mounted")
                continue
            unmount(paths, run_as)
            print(f"[dfsmount] startup scan: reaped idle mount {paths.mount_dir}")


def run(config: ServiceConfig, run_as: UserCreds) -> None:
    require_executable("fuser")
    _bulk_reap_at_startup(config, run_as)

    watches: dict[str, _Watch] = {}  # mount_dir -> armed, not-yet-mounted watch
    mounted: dict[str, TargetPaths] = {}  # mount_dir -> mounted target
    last_poll = 0.0

    while True:
        fd_to_key = {watch.fan.fd: key for key, watch in watches.items()}
        timeout = max(0.0, config.poll_interval - (time.monotonic() - last_poll))
        ready, _, _ = select.select(list(fd_to_key), [], [], timeout)

        for fd in ready:
            _handle_events(fd_to_key[fd], watches, mounted, run_as)

        if time.monotonic() - last_poll >= config.poll_interval:
            _reconcile(config, watches, mounted, run_as)
            last_poll = time.monotonic()


def _handle_events(
    key: str,
    watches: dict[str, _Watch],
    mounted: dict[str, TargetPaths],
    run_as: UserCreds,
) -> None:
    watch = watches[key]
    for mask, ev_fd, pid in watch.fan.read_events():
        if mask & FAN_OPEN_PERM:
            print(
                f"[dfsmount] access from pid {pid} on {watch.paths.mount_dir}; mounting"
            )
            try:
                mount(watch.paths, run_as)
            except (OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"[dfsmount] mount failed for {watch.paths.mount_dir}: {exc}")
                watch.fan.respond(ev_fd, allow=False)
            else:
                watch.fan.respond(ev_fd, allow=True)
                mounted[key] = watch.paths
        if ev_fd >= 0:
            os.close(ev_fd)
    watch.fan.close()
    del watches[key]


def _reconcile(
    config: ServiceConfig,
    watches: dict[str, _Watch],
    mounted: dict[str, TargetPaths],
    run_as: UserCreds,
) -> None:
    for proc in config.processes:
        running = is_process_running(proc.name)
        for target in discover_targets(proc.archives_dir):
            paths = target_paths(proc, target)
            key = str(paths.mount_dir)

            if running:
                _arm_if_needed(key, paths, watches, mounted)
            else:
                _disarm_if_needed(proc.name, key, paths, watches)

            if key in mounted:
                _reap_if_idle(key, paths, mounted, run_as)


def _arm_if_needed(
    key: str,
    paths: TargetPaths,
    watches: dict[str, _Watch],
    mounted: dict[str, TargetPaths],
) -> None:
    if key in watches or key in mounted:
        return
    if is_mounted(paths.mount_dir):
        mounted[key] = (
            paths  # mounted outside our lifetime (e.g. service restart); adopt it
        )
        return

    paths.mount_dir.mkdir(parents=True, exist_ok=True)
    fan = Fanotify()
    fan.mark_dir(str(paths.mount_dir))
    watches[key] = _Watch(fan=fan, paths=paths)
    print(f"[dfsmount] {paths.target}: watching {paths.mount_dir}")


def _disarm_if_needed(
    process_name: str,
    key: str,
    paths: TargetPaths,
    watches: dict[str, _Watch],
) -> None:
    if key not in watches:
        return
    watches[key].fan.close()
    del watches[key]
    print(f"[dfsmount] {process_name} stopped; disarming watch on {paths.mount_dir}")


def _reap_if_idle(
    key: str,
    paths: TargetPaths,
    mounted: dict[str, TargetPaths],
    run_as: UserCreds,
) -> None:
    """Unmount the moment nothing is accessing it - independent of launcher state."""
    if is_mount_busy(paths.mount_dir):
        return
    unmount(mounted[key], run_as)
    del mounted[key]
    print(f"[dfsmount] {paths.mount_dir} idle; reaped")
