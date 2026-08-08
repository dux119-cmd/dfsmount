# dfsmount

Pack bloated game directories into single-file immutable [DwarFS](https://github.com/mhx/dwarfs)
archives and have them auto-mount on access with a writable overlay when
a launcher (Steam, Lutris, Heroic, ...) actually tries to use them, and
disappear again when it's done.

## How it works

Each game you create an archive for is stored as an immutable, compressed
archive with a revision tag that's incremented on subsequent re-packs:

```
mygame-<rev1>.dfs
```

A lightweight, unprivileged background service (`dfsmount service`) watches
for the launchers you configure. It generates, per archived game, one real
**systemd user automount** (the overlay) plus a plain oneshot **service**
that mounts dwarfs as a prerequisite for it:

- **dwarfs service** (read-only): a oneshot `.service` unit whose
  `ExecStart` runs the `dwarfs` binary directly against the `.dfs` archive
  - the same invocation used for manual mounts - and whose `ExecStop`
  unmounts it. It's not a systemd mount/automount itself; it's just a
  prerequisite action the overlay unit depends on (`Requires=`/`After=`),
  and `StopWhenUnneeded=yes` means systemd runs its `ExecStop` as a
  post-action the moment the overlay that needed it is gone - not before.
- **overlay automount** (writable): a real systemd `.automount`, triggering
  a `fuse-overlayfs` mount stacked on the dwarfs mount above, catching
  saves/settings/DRM files at runtime.

Only the overlay gets an actual kernel autofs mount. Stacking a second one
underneath it (an earlier version of this tool automounted dwarfs too) ran
into unprivileged `systemctl --user` sessions rejecting the nested autofs
setup outright (`Operation not permitted`) - kernel autofs plumbing is
touchy enough without nesting it, even before getting into whether a given
distro's user session has the privilege for autofs at all. If you still see
`Operation not permitted` on the *overlay's* automount, that's this same
unprivileged-autofs limitation at the kernel level, independent of
dfsmount's unit layout - check whether your systemd user session has
`CAP_SYS_ADMIN` available for autofs.

The overlay automount carries a one-minute idle timeout - once it's idle,
systemd stops the overlay mount, which (via `StopWhenUnneeded`) tears the
dwarfs service down right behind it, entirely on its own.

The service keeps these unit files in sync with reality:

- While the launcher is running, every archived game's overlay automount is
  **armed** (`systemctl --user enable --now`) - so touching the game's
  directory triggers the mount, same as before.
- The moment the launcher isn't running, its overlay automount is
  **disarmed** (stopped and disabled) - systemd stops watching that path
  entirely until the launcher comes back.
- A newly created archive gets its unit files generated and registered
  with systemd on the next poll; a deleted archive has its unit files
  removed entirely.

Each mount is actually two layers:

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
- [`mkdwarfs`, `dwarfs`, `dwarfsck`, `dwarfsextract`](https://github.com/mhx/dwarfs) —
  either on `PATH`, or fetched by dfsmount itself (see below)
- `fuse-overlayfs` on `PATH`, plus the generic FUSE mount helper
  (`mount.fuse`, from your distro's `fuse`/`fuse3` package) that systemd's
  generated `.mount` units invoke
- `mountpoint` / `umount` (util-linux, near-universally present)
- `systemd` in user mode (`systemctl --user`, `systemd-escape`) — this is
  what actually owns mounting/unmounting for `dfsmount service`; on a
  headless box you'll need `loginctl enable-linger $USER` so your user
  session (and its automounts) stays up without a login shell
- Everything (mounting, unmounting, creating archives, running the
  service) always runs as your own user - there's no root component

```
pip install .
```

This installs the `dfsmount` command.

### dwarfs binaries

Instead of installing `mkdwarfs`/`dwarfs`/`dwarfsck`/`dwarfsextract` through
your distro's package manager, dfsmount can fetch the matching prebuilt
binaries straight from
[github.com/mhx/dwarfs/releases](https://github.com/mhx/dwarfs/releases):

```
dfsmount fetch-binaries
```

This downloads the latest release's Linux tarball for your architecture
(x86_64 or aarch64) and unpacks `mkdwarfs`, `dwarfs`, `dwarfsck`, and
`dwarfsextract` into `dfsmount/bin/<version>/`, with a `current` symlink
pointing at the active version. Once fetched, every dfsmount command prefers
these bundled binaries over `PATH`; run `dfsmount fetch-binaries --force` to
re-fetch (e.g. after a new dwarfs release). If nothing has been fetched,
dfsmount falls back to `mkdwarfs`/`dwarfs` on `PATH` as before.

## Configuring

dfsmount is driven by one YAML file, `~/.config/dfsmount.yaml` by default
(override anywhere with `-c/--config`). One entry per launcher you want
watched:

```yaml
poll_interval: 2  # seconds between /proc scans

launchers:
  - name: launcherexec  # matched against /proc/<pid>/comm (15-char truncated)
    archives_dir: /backups/game-archives # "<target>-rev<N>.dfs" files live here
    working_dir: .local/state/games/ # per-game writable dirs created here
    target_mount_dir: Games    # common dir where the games originally lived

    hooks:
      pre_archive: .local/bin/before-pack.sh      # arg: source_dir
      post_archive: .local/bin/after-pack.sh      # args: source_dir, archive_path
      install: .local/bin/install-into-lutris.sh    # arg: mount_dir; run by `dfsmount install`
      uninstall: .local/bin/remove-from-lutris.sh   # arg: mount_dir; run by `dfsmount uninstall`

```

There are no `pre_mount`/`post_mount`/`pre_unmount`/`post_unmount` hooks:
mounting and unmounting are owned by systemd automount units, which don't
have a hook point to run arbitrary commands around each mount/unmount
cycle. Use `pre_archive`/`post_archive` for anything that needs to happen
around packing a game, and `install`/`uninstall` for one-off registration
with a launcher's own library.

Configuration paths can be absolute, start with `~` (expanded to your home
directory), or be given bare-relative (resolved the same way, so
`Games/lutris/live` means `~/Games/lutris/live`). Hook paths can additionally
reference a script
bundled with dfsmount itself by prefixing the path with `builtin:`, resolved
relative to the package, such as: `builtin:hooks/lutris/prepack.sh`

Any hook can also be given as a list of commands instead of a single one, to
run several scripts in sequence for that event:

```yaml
    hooks:
      pre_archive:
        - builtin:hooks/lutris/prepack.sh
        - .local/bin/before-pack.sh
```

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

Commands take the launcher first, then the action, then (optionally) a
target: `dfsmount <launcher> <action> [target]`. If only one launcher is
configured, leave it off entirely — `dfsmount <action> [target]` — and
dfsmount assumes it. If several launchers are configured and you don't name
one, dfsmount lists what's available instead of guessing:

```
$ dfsmount mount
dfsmount: specify a launcher:
  lutris
  heroic
```

`service` and `fetch-binaries` never take a launcher — `service` runs for
every configured launcher at once, and `fetch-binaries` isn't launcher-
scoped at all.

Every command, including `service`, always runs as whichever user invokes
it — that's the whole point of `fuse-overlayfs`/`dwarfs`, neither needs
privilege, and `service` itself only ever talks to `systemctl --user`.

### Leaving off the target lists short names

Every action that takes a target will list the relevant short names instead
of acting, if you leave the target off:

- `mount` lists archives available to mount (from `archives_dir`)
- `unmount` and `repack` list targets currently mounted
- `status` lists every known target (archived or mounted) with its state
- `create`, `install`, and `uninstall` list known game names (same set,
  flagging `(repackable)` ones for `create`)

### Create an archive from a game

```
dfsmount lutris create /path/to/existing/mygame-directory
```

Packs the given directory into `mygame-rev1.dfs` inside the `lutris`
launcher's `archives_dir`. The **target** name is taken from the directory
you point at — `.../mygame-directory` → target `mygame-directory` — and
that's what you use in every other command below.

Once packed, you can safely delete (or move elsewhere) the original,
uncompressed directory.

### List a launcher's games

Leave off the source directory to list game names instead of creating an
archive:

```
dfsmount lutris create
```

```
mygame
otherclassic (repackable)
```

Names come from the directories under the launcher's `target_mount_dir`
(and any target that only exists as an archive so far). `(repackable)` marks
games that already have an archive *and* unsaved changes in their overlay —
i.e. `dfsmount repack` has something new to bake in. A game with no archive
yet, or with an empty/missing overlay, isn't repackable.

### Run the service

```
dfsmount service
```

Plain user process, no root. Every `poll_interval` seconds it:

1. Diffs each launcher's `archives_dir` against what it saw last poll —
   new archives get systemd unit files generated and registered
   (`systemctl --user daemon-reload`); removed archives have their unit
   files deleted outright.
2. Checks whether the launcher is currently running (`/proc` scan). If so,
   every archived target's overlay automount is armed (`systemctl --user
   enable --now`); if not, it's disarmed (stopped then disabled) so systemd
   isn't watching paths the
   launcher can't currently trigger.

It logs a status line for each unit it registers/removes/arms/disarms. Run
it as a systemd user unit (see below) so it comes up with your session.
Everything past this point is for manual control or debugging; with the
service running you generally don't need any of it.

### Mount / unmount a game by hand

```
dfsmount lutris mount mygame
dfsmount lutris unmount mygame
```

No root needed. Useful for testing a game outside of a launcher, or forcing
a mount before the launcher would normally trigger one.

### Check on a game

```
dfsmount lutris status mygame
```

```
~/Games/lutris/live/mygame: mounted
  latest archive: ~/Games/lutris/archives/mygame-rev3.dfs
```

### Write a new revision

```
dfsmount lutris repack mygame
```

Requires the game to already be mounted (`dfsmount lutris mount mygame`
first, or let the service mount it). Packs the current live state into the
next revision, clears the overlay, and remounts — see [Upgrading a
game](#upgrading-a-game) above.

### Install / uninstall a game

```
dfsmount lutris install mygame
dfsmount lutris install all
dfsmount lutris uninstall mygame
dfsmount lutris uninstall all
```

Runs the launcher's `install` (or `uninstall`) hook, passing it the game's
mount dir. `all` runs it for every game name known to the launcher (same set
leaving the target off lists), instead of a single target. These commands
don't mount or unmount anything themselves — they're for hooks that
register (or remove) a game with the launcher's own library, independent of
whether it's currently mounted.

## Example systemd unit

One user unit, running as you. It doesn't inherit a login `$HOME` by
default, so point `--config` at your actual config file explicitly.

`~/.config/systemd/user/dfsmount.service`:

```ini
[Unit]
Description=dfsmount launcher watcher / automount manager (user)

[Service]
ExecStart=/usr/local/bin/dfsmount service --config %h/.config/dfsmount.yaml
Restart=on-failure

[Install]
WantedBy=default.target
```

Enable it: `systemctl --user enable --now dfsmount`. On a headless box
also run `loginctl enable-linger $USER` so your user session (and any
armed automounts) survive without a login shell.

The per-game `<ro_mount>.service` and `<mount_dir>.{mount,automount}`
units it generates under `~/.config/systemd/user/` are managed entirely by
this service — don't hand-edit them, they get overwritten/removed as
archives and launcher state change.

## Notes & caveats

- Archives are compressed with `zstd` at a fairly aggressive level
  (`compress-level=9`, long-range matching) — expect `mkdwarfs` to take a
  while on large game libraries. This happens once, at `create`/`repack`
  time, not on every mount.
- Mounting is lazy per-*game*, not per-launcher — a launcher with 50 games
  configured only triggers automounts for the ones it actually opens.
- Idle reaping is systemd's own job (`TimeoutIdleSec=60` on each
  automount) — a mount is torn down about a minute after becoming idle,
  independent of whether its launcher is still running or the dfsmount
  service is even up.
- The unit name for a `.mount` unit must match its mount path
  (`systemd-escape --path`), so unit filenames are derived from
  `working_dir`/`target_mount_dir`, not from the game's name directly.
