#!/bin/sh
# Build dist/AAC-Addon-Manager-x86_64.AppImage. Needs python3 with pip, curl,
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

"$work/venv/bin/pyinstaller" --noconfirm --clean --windowed \
  --name aac-addon-manager \
  --distpath "$work/pyi" --workpath "$work/pyi-build" --specpath "$work" \
  --collect-data aacam \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf \
  packaging/appimage_entry.py

app="$work/AppDir"
mkdir -p "$app/usr/bin" "$app/usr/share/applications" "$app/usr/share/icons/hicolor/256x256/apps"
cp -r "$work/pyi/aac-addon-manager/." "$app/usr/bin/"
cp packaging/aac-addon-manager.desktop "$app/aac-addon-manager.desktop"
sed -i 's|^Exec=.*|Exec=aac-addon-manager|' "$app/aac-addon-manager.desktop"
cp "$app/aac-addon-manager.desktop" "$app/usr/share/applications/"
cp src/aacam/icon.png "$app/aac-addon-manager.png"
cp src/aacam/icon.png "$app/usr/share/icons/hicolor/256x256/apps/aac-addon-manager.png"
cat > "$app/AppRun" <<'RUN'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
exec "$here/usr/bin/aac-addon-manager" "$@"
RUN
chmod +x "$app/AppRun"

tool="$work/appimagetool"
curl -fsSL -o "$tool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$tool"
ARCH=x86_64 "$tool" --appimage-extract-and-run "$app" dist/AAC-Addon-Manager-x86_64.AppImage
echo "Built dist/AAC-Addon-Manager-x86_64.AppImage"
