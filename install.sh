#!/usr/bin/env bash
# One-shot installer for dfsmount. Clones the repo into a self-contained
# checkout under $HOME, links `dfsmount` onto PATH, fetches the dwarfs
# binaries, drops a starter config into ~/.config, and installs the user
# service. Re-run to update an existing checkout.
set -euo pipefail

REPO_URL="https://github.com/dux119-cmd/dfsmount.git"
INSTALL_DIR="${DFSMOUNT_INSTALL_DIR:-$HOME/.local/share/dfsmount}"
CONFIG_PATH="$HOME/.config/dfsmount.yaml"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "dfsmount: updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "dfsmount: cloning to $INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

python3 -c 'import yaml' 2>/dev/null || python3 -m pip install --user pyyaml

"$INSTALL_DIR/dfsmount" install-bin
"$INSTALL_DIR/dfsmount" fetch-binaries

mkdir -p "$(dirname "$CONFIG_PATH")"
if [ ! -f "$CONFIG_PATH" ]; then
    cp "$INSTALL_DIR/config.example.yaml" "$CONFIG_PATH"
    echo "dfsmount: wrote starter config to $CONFIG_PATH"
fi

"$INSTALL_DIR/dfsmount" service-install

cat <<EOF

dfsmount installed to $INSTALL_DIR
Edit $CONFIG_PATH and adjust archives_dir / working_dir / target_mount_dir
for your setup - the running service picks up changes automatically.
EOF
