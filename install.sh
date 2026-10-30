#!/bin/sh
# Install aacam (command line) and the ArcheAge Classic Linux Launcher desktop app into
# their own virtualenv, link them into ~/.local/bin and add a menu entry.
# Needs only python3; works without pipx. Run again to upgrade.
# Pass --no-gui to skip the desktop app (and its ~100 MB Qt download).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
venv="$data/aacam/venv"
bindir="$HOME/.local/bin"
gui=1
[ "${1:-}" = "--no-gui" ] && gui=0

command -v python3 >/dev/null || { echo "python3 is required (sudo pacman -S python)" >&2; exit 1; }

python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade pip
if [ "$gui" = 1 ]; then
  "$venv/bin/pip" install --quiet --upgrade "$here[gui]"
else
  "$venv/bin/pip" install --quiet --upgrade "$here"
fi
mkdir -p "$bindir"
ln -sf "$venv/bin/aacam" "$bindir/aacam"
echo "Installed aacam to $bindir/aacam"

if [ "$gui" = 1 ]; then
  ln -sf "$venv/bin/aacam-gui" "$bindir/aacam-gui"
  mkdir -p "$data/applications"
  for png in "$here"/src/aacam/icons/*.png; do
    size=$(basename "$png" .png)
    mkdir -p "$data/icons/hicolor/${size}x${size}/apps"
    cp "$png" "$data/icons/hicolor/${size}x${size}/apps/aac-linux-launcher.png"
  done
  rm -f "$data/applications/aac-addon-manager.desktop" "$data/icons/hicolor/256x256/apps/aac-addon-manager.png"  # old name
  sed "s|^Exec=.*|Exec=$bindir/aacam-gui|" "$here/packaging/aac-linux-launcher.desktop" \
    > "$data/applications/aac-linux-launcher.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" 2>/dev/null || true
  echo "Installed the desktop app: find \"ArcheAge Classic Linux Launcher\" in your app menu, or run aacam-gui"
fi

case ":$PATH:" in
  *":$bindir:"*) ;;
  *)
    echo
    echo "$bindir is not on your PATH. Add it with one of:"
    echo "  fish:      fish_add_path $bindir"
    echo "  bash/zsh:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile"
    ;;
esac
