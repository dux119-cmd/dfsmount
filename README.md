# dfsmount

Pack game directories into single-file [DwarFS](https://github.com/mhx/dwarfs)
archives that auto-mount (read-only archive + writable overlay) when a
launcher touches them, and unmount again when idle. Everything runs as your
own user via `systemctl --user` — no root.

## Install

The quickest path is the one-shot installer. It clones dfsmount into
`~/.local/share/dfsmount`, links `dfsmount` onto your PATH, fetches the
`dwarfs` binaries, writes a starter config to `~/.config/dfsmount.yaml`, and
installs the background service — running each of those steps through the
same `dfsmount` actions described below.

```
curl -fsSL https://raw.githubusercontent.com/dux119-cmd/dfsmount/main/install.sh | bash
```

Then edit `~/.config/dfsmount.yaml` and adjust `archives_dir`,
`working_dir`, and `target_mount_dir` for your setup — the running service
picks up changes to that file automatically.

### Manual install

If you'd rather do each step yourself, or aren't installing from GitHub:

```
git clone https://github.com/dux119-cmd/dfsmount.git ~/.local/share/dfsmount
cd ~/.local/share/dfsmount

python3 -m pip install --user pyyaml

./dfsmount install-bin        # symlinks ./dfsmount into ~/.local/bin
dfsmount fetch-binaries       # pulls mkdwarfs/dwarfs/dwarfsck/dwarfsextract
dfsmount service-install      # installs and starts the systemd --user service

cp config.example.yaml ~/.config/dfsmount.yaml
$EDITOR ~/.config/dfsmount.yaml
```

`fuse-overlayfs` isn't fetched by dfsmount — install it via your distro's
package manager (e.g. `apt install fuse-overlayfs`).

### Alternative: pip

```
pip install .
```

Or run it in place without installing, from a checkout:

```
./dfsmount ...
```

Requires Python ≥ 3.10, `pyyaml`, `fuse-overlayfs`, `systemd` (user mode),
and the `dwarfs` tools (`mkdwarfs`, `dwarfs`, `dwarfsck`, `dwarfsextract`):

```
dfsmount fetch-binaries   # or install them via your distro's package manager
```

## Put dfsmount on your PATH

```
dfsmount install-bin
```

Symlinks the `dfsmount` launcher script from wherever it's checked out into
`~/.local/bin/dfsmount`. Since the launcher runs the package straight out of
its source tree (`src/`), the checkout stays self-contained — nothing is
copied elsewhere, so `git pull` inside it is enough to update. If
`~/.local/bin` isn't already on your `PATH`, `install-bin` prints a warning
with the line to add to your shell profile.

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
      pack: builtin:lutris/pack.py
      install: builtin:lutris/install.py
      remove: builtin:lutris/remove.py
```

Paths may be absolute, `~`-relative, or bare-relative (resolved against
`$HOME`). Hooks can be a single command or a list; `builtin:` refers to a
script bundled with dfsmount, resolved relative to its `hooks/` directory
(e.g. `builtin:lutris/pack.py` -> `dfsmount/hooks/lutris/pack.py`).

While `dfsmount service` is running, it watches this file's modification
time and reloads it automatically whenever it changes — no restart needed
after editing paths or launchers. A bad edit is logged and ignored, and the
service keeps running on the last good config.

## Usage

```
dfsmount <launcher> <action> [target] [options]
```

Leave off `<launcher>` if you only have one configured. Leave off `[target]`
to list the names available for that action.

```
dfsmount lutris pack ~/Games/lutris/live/mygame     # archive a directory
dfsmount lutris mount mygame                        # mount it by hand
dfsmount lutris unmount mygame
dfsmount lutris repack mygame                        # bake live changes into a new revision
dfsmount lutris status mygame
dfsmount lutris install mygame                       # register with the launcher
dfsmount lutris install all
dfsmount lutris remove mygame                        # remove from the launcher
```

Run `dfsmount service` as a background process (see below) and mounting
happens automatically whenever the configured launcher is running — the
commands above are for manual control and debugging.

## Run the service

```
dfsmount service-install   # writes ~/.config/systemd/user/dfsmount.service,
                            # daemon-reloads, and starts it
dfsmount service-remove    # stops and removes it
```

`service-install` writes a unit equivalent to:

```ini
[Unit]
Description=dfsmount launcher watcher

[Service]
ExecStart=dfsmount service
Restart=on-failure

[Install]
WantedBy=default.target
```

On a headless box, also run `loginctl enable-linger $USER` so your session
(and its automounts) stays up without a login shell.

## Portable metadata

The Lutris `pack` hook writes the fields listed in
`dfsmount/hooks/lutris/database-to-config-fields.yaml` (id, name, runner,
platform, year by default) into the `game:` section of a portable
`.dfsmount/config.yml` (plus `.dfsmount/art/`) inside the archive itself,
instead of leaving that information only in Lutris's own database. The
`install` hook reads the fields listed in
`dfsmount/hooks/lutris/config-to-database-fields.yaml` back out to
register the game: `directory` is set from the mount path, `configpath`
from the identity field's value (whichever config field maps to the
database's `slug` column), and `installed` is always set to `1`. Artwork
under `.dfsmount/art/` is installed as-is — whatever files are present are
what gets restored. This makes an archive self-contained: it can be
installed into Lutris on a different machine. See
`dfsmount/hooks/lutris/_lutris_common.py`.

Three YAML files under `dfsmount/hooks/lutris/` drive both hooks, so
fields aren't hard-coded twice in opposite directions:

- `database-to-config-fields.yaml` — `pga.db` column -> `config.yml`
  field, used by `pack.py`.
- `config-to-database-fields.yaml` — `config.yml` field -> `pga.db`
  column, used by `install.py` (and to find the identity field, used by
  `install.py`/`remove.py` to look a game up).
- `config-exclusions.yaml` — top-level `config.yml` keys stripped by
  `pack.py` before archiving (data already carried by the field maps, or
  launcher-internal bookkeeping).

Add a line to a mapping file to carry an extra field through pack/install;
no code change needed. Only fields with no natural round-trip — computed
at install time rather than read from config.yml (`directory`,
`configpath`, `installed`) — stay hard-coded in `install.py`.

## Notes

- `mount`/`unmount`/`repack` operate per-game, not per-launcher.
- Archives are revisioned: `mygame-rev1.dfs`, `mygame-rev2.dfs`, ... Older
  revisions are left on disk; delete them yourself to reclaim space.
- `dfsmount service` only manages systemd unit files and calls `systemctl`;
  it never touches a mount directly.
