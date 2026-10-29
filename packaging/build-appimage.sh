#!/bin/sh
# Build dist/ArcheAge-Classic-Linux-Launcher-x86_64.AppImage. Needs python3 with pip, curl,
# and network access to PyPI and GitHub. Used by .github/workflows/release.yml.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
work=build/appimage
rm -rf "$work" dist
mkdir -p "$work" dist

python3 -m venv "$work/venv"
"$work/venv/bin/pip" install --quiet --upgrade pip
"$work/venv/bin/pip" install --quiet ".[gui]" pyinstaller

# Qt's xcb plugin loads libxcb-cursor at runtime (Qt 6.5+), so PyInstaller can't see it,
# and many distros don't install it. Bundle it and its helpers so the app opens everywhere.
# The xcb libraries Qt links directly are bundled by PyInstaller when installed here.
extra=""
for lib in libxcb-cursor.so.0 libxcb-render-util.so.0 libxcb-image.so.0 libxcb-util.so.1; do
  path=$(ldconfig -p | awk -v l="$lib" '$1 == l && /x86-64/ {print $NF; exit}')
  [ -n "$path" ] || { echo "missing $lib on the build machine" >&2; exit 1; }
  extra="$extra --add-binary $path:."
done

# shellcheck disable=SC2086
"$work/venv/bin/pyinstaller" --noconfirm --clean --windowed $extra \
  --name aac-linux-launcher \
  --distpath "$work/pyi" --workpath "$work/pyi-build" --specpath "$work" \
  --collect-data aacam \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf \
  packaging/appimage_entry.py

app="$work/AppDir"
mkdir -p "$app/usr/bin" "$app/usr/share/applications" "$app/usr/share/icons/hicolor/256x256/apps"
cp -r "$work/pyi/aac-linux-launcher/." "$app/usr/bin/"
cp packaging/aac-linux-launcher.desktop "$app/aac-linux-launcher.desktop"
sed -i 's|^Exec=.*|Exec=aac-linux-launcher|' "$app/aac-linux-launcher.desktop"
cp "$app/aac-linux-launcher.desktop" "$app/usr/share/applications/"
cp src/aacam/icon.png "$app/aac-linux-launcher.png"
cp src/aacam/icon.png "$app/usr/share/icons/hicolor/256x256/apps/aac-linux-launcher.png"
cat > "$app/AppRun" <<'RUN'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
exec "$here/usr/bin/aac-linux-launcher" "$@"
RUN
chmod +x "$app/AppRun"

tool="$work/appimagetool"
curl -fsSL -o "$tool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$tool"
ARCH=x86_64 "$tool" --appimage-extract-and-run "$app" dist/ArcheAge-Classic-Linux-Launcher-x86_64.AppImage
echo "Built dist/ArcheAge-Classic-Linux-Launcher-x86_64.AppImage"
