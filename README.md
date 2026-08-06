# dfsmount

Pack bloated game directories into single-file immutable [DwarFS](https://github.com/mhx/dwarfs)
archives and have them auto-mount on access with a writable overlay when
a launcher (Steam, Lutris, Heroic, ...) actually tries to use them, and
disappear again when it's done.

## How it works

Each game you convert is stored as an immutable, compressed archive with
a revision tag that's incremented on subsequent re-packs:

```
mygame-<rev1>.dfs
```

A background service watches for the launcher processes you configure. When
one is running, dfsmount arms a lightweight [fanotify](https://man7.org/linux/man-pages/man7/fanotify.7.html)
The moment the launcher (or anything else) opens a file inside one of those
directories, dfsmount transparently mounts it on the spot and lets the
access through, so from the launcher's point of view the directory was
"already there."

Each mount is actually two layers stacked with `fuse-overlayfs`:

- **Lower (read-only):** the `.dfs` archive itself, mounted via `dwarfs`.
- **Upper (writable):** an empty overlay directory that catches saves,
  settings, DRM files, and anything else the game writes at runtime.

### Upgrading a game

If you've Sometimes you'll want to re-pack a game, r a game update, a mod
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
  - name: launcherexec  # matched against /proc/<pid>/comm (15-char truncated)
    archives_dir: /backups/game-archives # "<target>-rev<N>.dfs" files live here
    working_dir: .local/state/games/ # per-game writable dirs created here
    target_mount_dir: Games    # common dir where the games originally lived

    hooks:
      pre_archive: .local/bin/before-pack.sh      # arg: source_dir
      post_archive: .local/bin/after-pack.sh      # args: source_dir, archive_path
      pre_mount: .local/bin/before-mount.sh       # arg: mount_dir
      post_mount: .local/bin/after-mount.sh       # arg: mount_dir
      pre_unmount: .local/bin/before-unmount.sh   # arg: mount_dir
      post_unmount: .local/bin/after-unmount.sh   # arg: mount_dir

```

Configuration paths can be absolute, start with `~` (expanded to your
home directory or `run_as`'s home, for the `service` command), or be given
bare-relative (resolved the same way, so `Games/lutris/live` means
`~/Games/lutris/live`). Hook paths can additionally use builtin looks
by prefixing the path with `builtin:`, such as: `builtin:hooks/lutris-install.sh`

Add one block per launcher (Steam, Lutris, Heroic, etc.) — each gets its own
archives directory, working directory, mount area, and potential hooks.

### Hooks detailed discussion

Hooks are particularly useful for adjusting/cleaning a game directory and
collecting metadata prior to archiving, which can then be used by a hook
when pre-mounting. An example usecase is genericizing a directory and 
saving metadata such as the name, date, art, and Wine/Proton launch preferences
into common filenames inside the game archive itself. 

These can then be read during pre-mount to install (or check if it's installed)
into the target launcher. This way, the game archive becomes stand-alone and portable,
and is now liberated from it's unreliable/ephemeral online playstore like Steam, 
GoG, and Epic. With metadata embedded in the archive, the game is also
liberated from launchers (like Lutris or Heroic), which often store hardcoded paths
and mix metadata across folders and in databases, making "portability" combersome
and fragile.

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
