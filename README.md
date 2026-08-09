# dfsmount

Pack game directories into single-file [DwarFS](https://github.com/mhx/dwarfs)
archives that auto-mount (read-only archive + writable overlay) when a
launcher touches them, and unmount again when idle. Everything runs as your
own user via `systemctl --user` — no root.

## Install

```
pip install .
```

Requires Python ≥ 3.10, `pyyaml`, `fuse-overlayfs`, `systemd` (user mode),
and the `dwarfs` tools (`mkdwarfs`, `dwarfs`, `dwarfsck`, `dwarfsextract`):

```
dfsmount fetch-binaries   # or install them via your distro's package manager
```

## Configure

Create `~/.config/dfsmount.yaml` (see `config.example.yaml`), one entry per
launcher:

```yaml
launchers:
  - name: lutris                       # matched against /proc/<pid>/comm
    archives_dir: /backups/game-archives
    working_dir: .local/state/dfsmount/lutris
    target_mount_dir: Games/lutris
    hooks:
      pre_archive: builtin:hooks/lutris/prepack.py
      install: builtin:hooks/lutris/install.py
      uninstall: builtin:hooks/lutris/uninstall.py
```

Paths may be absolute, `~`-relative, or bare-relative (resolved against
`$HOME`). Hooks can be a single command or a list; `builtin:` refers to a
script bundled with dfsmount.

## Usage

```
dfsmount <launcher> <action> [target]
```

Leave off `<launcher>` if you only have one configured. Leave off `[target]`
to list the names available for that action.

```
dfsmount lutris create ~/Games/lutris/live/mygame   # archive a directory
dfsmount lutris mount mygame                        # mount it by hand
dfsmount lutris unmount mygame
dfsmount lutris repack mygame                        # bake live changes into a new revision
dfsmount lutris status mygame
dfsmount lutris install mygame                       # register with the launcher
dfsmount lutris install all
dfsmount lutris uninstall mygame                     # remove from the launcher
```

Run `dfsmount service` as a background process (see below) and mounting
happens automatically whenever the configured launcher is running — the
commands above are for manual control and debugging.

## Run the service

`~/.config/systemd/user/dfsmount.service`:

```ini
[Unit]
Description=dfsmount launcher watcher

[Service]
ExecStart=/usr/local/bin/dfsmount service --config %h/.config/dfsmount.yaml
Restart=on-failure

[Install]
WantedBy=default.target
```

```
systemctl --user enable --now dfsmount
```

On a headless box, also run `loginctl enable-linger $USER` so your session
(and its automounts) stays up without a login shell.

## Portable metadata

The Lutris `pre_archive` hook writes a launcher-agnostic `.metadata/`
directory (game name, runner, launch config, artwork) into the archive
itself, instead of leaving that information only in Lutris's own database.
The `install` hook reads it back to register the game. This makes an
archive self-contained: it can be installed into Lutris on a different
machine, or a different launcher's hooks can read the same `.metadata/`
format. See `dfsmount/hooks/metadata.py`.

## Notes

- `mount`/`unmount`/`repack` operate per-game, not per-launcher.
- Archives are revisioned: `mygame-rev1.dfs`, `mygame-rev2.dfs`, ... Older
  revisions are left on disk; delete them yourself to reclaim space.
- `dfsmount service` only manages systemd unit files and calls `systemctl`;
  it never touches a mount directly.
