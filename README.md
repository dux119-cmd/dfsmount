# dfsmount

Store your game libraries as compressed, read-only [dwarfs](https://github.com/mhx/dwarfs)
archives instead of raw directories — then have them **appear** as normal,
writable directories the instant a launcher (Steam, Lutris, Heroic, ...)
actually tries to use them, and disappear again when it's done.

No always-on mounts. No manually mounting before you play. No wasted disk
space from long-since-finished games sitting around uncompressed.

## How it works

Each game you convert is stored as an immutable, compressed archive:

```
mygame-rev1.dfs
```

A background service watches for the launcher processes you configure. When
one is running, dfsmount arms a lightweight [fanotify](https://man7.org/linux/man-pages/man7/fanotify.7.html)
watch on each of that launcher's game directories — nothing is mounted yet.
The moment the launcher (or anything else) opens a file inside one of those
directories, dfsmount transparently mounts it on the spot and lets the
access through, so from the launcher's point of view the directory was just
"already there."

Each mount is actually two layers stacked with `fuse-overlayfs`:

- **Lower (read-only):** the `.dfs` archive itself, mounted via `dwarfs`.
- **Upper (writable):** an empty overlay directory that catches saves,
  settings, DRM files, and anything else the game writes at runtime.

The archive is never touched. Everything the game changes lives only in the
upper layer.

Reaping is based purely on use, not on the launcher: the moment nothing has
the mounted directory open (checked every `poll_interval`), dfsmount tears
it down — whether or not the launcher is still running. So a game that
outlives the launcher (common — Steam/Lutris often exit while the game
keeps going) stays mounted until *it* exits, and a mount doesn't linger
just because the launcher happens to still be open.

### Upgrading a game

Sometimes you'll want to re-pack a game — after a game update, a mod
install, or just to bake accumulated save data into a smaller image. That's
what `repack` does:

1. Reads the *live, merged* view (archive + your changes) and writes it out
   as the next revision: `mygame-rev2.dfs`.
2. Unmounts everything.
3. **Clears the writable overlay** — the old upper layer is discarded, since
   its contents are now baked into the new archive.
4. Remounts against the new revision with a fresh, empty overlay.

dfsmount always mounts whichever revision has the highest `-revN` number, so
older revisions are simply left on disk (delete them yourself if you want
the space back).

## Installation

Requires:

- Python ≥ 3.10 and `pyyaml`
- [`mkdwarfs` and `dwarfs`](https://github.com/mhx/dwarfs) on `PATH`
- `fuse-overlayfs` on `PATH`
- `mountpoint` / `umount` (util-linux, near-universally present)
- Linux with fanotify support and `fuser` (from `psmisc`, nearly always
  already installed) — only the `service` command needs root, for fanotify;
  everything else (mounting, unmounting, converting) runs as your own user

```
pip install .
```

This installs the `dfsmount` command.

## Configuring

dfsmount is driven by one YAML file, `~/.config/dfsmount.yaml` by default
(override anywhere with `-c/--config`). One entry per launcher you want
watched:

```yaml
poll_interval: 2  # seconds between /proc scans

# Only needed for `dfsmount service` — see "Running the service" below.
run_as: alice

processes:
  - name: lutris                    # matched against /proc/<pid>/comm
    archives_dir: Games/lutris/archives    # where <target>-revN.dfs files live
    working_dir: ~/.local/state/dfsmount/lutris  # per-game ro/upper/work dirs
    target_mount_dir: Games/lutris/live    # each game appears at <this>/<target>
```

- **`name`** is matched against `/proc/<pid>/comm`, which is truncated to 15
  characters — the same limit `ps -C` and `pgrep -x` use. If your launcher's
  binary name is longer, use the truncated form. For launchers that are
  really a Python script (`comm` shows up as `python3`, e.g.
  `python3 /usr/bin/lutris`), dfsmount falls back to matching `name` against
  the script/module basename in the process's argv — so `name: lutris`
  still works unmodified.
- **`archives_dir`** holds every game's `.dfs` archives for this launcher.
  One directory, many games.
- **`working_dir`** holds the per-game scratch space (dwarfs mount point +
  overlay upper/work dirs). Not something you need to look inside.
- **`target_mount_dir`** is where the live, writable game directories show
  up — `target_mount_dir/<target>`. Point your launcher's library path here.

Any of these three paths may be absolute, start with `~` (expanded to your
home directory — or `run_as`'s home, for the `service` command), or be given
bare-relative (resolved the same way, so `Games/lutris/live` means
`~/Games/lutris/live`).

Add one block per launcher (Steam, Lutris, Heroic, etc.) — each gets its own
archives directory, working directory, and mount area.

## Usage

Only `service` needs root (for fanotify). `convert`, `mount`, `unmount`,
`repack`, and `status` all run as whichever user invokes them — that's the
whole point of `fuse-overlayfs`/`dwarfs`, neither needs privilege.

### Convert a game to an archive

```
dfsmount convert lutris /path/to/existing/mygame-directory
```

Packs the given directory into `mygame-rev1.dfs` inside the `lutris`
process's `archives_dir`. The **target** name is taken from the directory
you point at — `.../mygame-directory` → target `mygame-directory` — and
that's what you use in every other command below.

Once packed, you can safely delete (or move elsewhere) the original,
uncompressed directory.

### Run the service

```
sudo dfsmount service
```

Needs root, purely for fanotify. Reads the whole config, watches every
configured launcher, and mounts/reaps games automatically — but the actual
mount/unmount/archive work runs under the credentials of the user set by
`run_as` in the config (or `-u/--user` on the command line, or `$SUDO_USER`
if you invoked it with plain `sudo`). This is the one command you actually
run day-to-day (as a systemd unit, ideally — see below). Everything below
this is for manual control or debugging — with the service running you
generally don't need any of it.

### Mount / unmount a game by hand

```
dfsmount mount lutris mygame
dfsmount unmount lutris mygame
```

No root needed. Useful for testing a game outside of a launcher, or forcing
a mount before the launcher would normally trigger one.

### Check on a game

```
dfsmount status lutris mygame
```

```
~/Games/lutris/live/mygame: mounted
  latest archive: ~/Games/lutris/archives/mygame-rev3.dfs
```

### Write a new revision

```
dfsmount repack lutris mygame
```

Requires the game to already be mounted (`dfsmount mount` it first, or let
the service mount it). Packs the current live state into the next
revision, clears the overlay, and remounts — see [Upgrading a
game](#upgrading-a-game) above.

## Example systemd unit

The service itself must run as root (fanotify), but set `run_as` in the
config (or pass `-u`) so mounts/archives are owned by you, not root. Since
systemd services don't inherit a login `$HOME`, point `--config` at your
actual config file explicitly:

```ini
[Unit]
Description=dfsmount auto-mount service
After=local-fs.target

[Service]
ExecStart=/usr/local/bin/dfsmount service --config /home/alice/.config/dfsmount.yaml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

with `run_as: alice` set in that config file (or add `-u alice` to
`ExecStart` instead, if you'd rather not put it in the file).

## Notes & caveats

- Archives are compressed with `zstd` at a fairly aggressive level
  (`compress-level=9`, long-range matching) — expect `mkdwarfs` to take a
  while on large game libraries. This happens once, at `convert`/`repack`
  time, not on every mount.
- Mounting is lazy per-*game*, not per-launcher — a launcher with 50 games
  configured only triggers mounts for the ones it actually opens.
- If the service restarts while a game is already mounted (e.g. you
  restarted the service mid-session), it detects and adopts the existing
  mount rather than re-mounting or erroring.
- Reaping is use-based (via `fuser -m`), checked every `poll_interval`
  seconds — a mount is torn down within one interval of becoming idle,
  independent of whether its launcher is still running.
