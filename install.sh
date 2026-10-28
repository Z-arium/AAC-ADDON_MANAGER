#!/bin/sh
# Install aacam into its own virtualenv and link it into ~/.local/bin.
# Needs only python3; works without pipx. Run again to upgrade.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
venv="${XDG_DATA_HOME:-$HOME/.local/share}/aacam/venv"
bindir="$HOME/.local/bin"

command -v python3 >/dev/null || { echo "python3 is required (sudo pacman -S python)" >&2; exit 1; }

python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade "$here"
mkdir -p "$bindir"
ln -sf "$venv/bin/aacam" "$bindir/aacam"
echo "Installed aacam to $bindir/aacam"

case ":$PATH:" in
  *":$bindir:"*) ;;
  *)
    echo
    echo "$bindir is not on your PATH. Add it with one of:"
    echo "  fish:      fish_add_path $bindir"
    echo "  bash/zsh:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile"
    ;;
esac
