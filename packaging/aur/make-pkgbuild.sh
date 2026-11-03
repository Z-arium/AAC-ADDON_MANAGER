#!/bin/sh
# Write packaging/aur/PKGBUILD for a release: make-pkgbuild.sh VERSION APPIMAGE [OUTDIR]
# VERSION is like 0.5.0 (no v). The AppImage is hashed to fill in sha256sums.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
version=${1:?version, e.g. 0.5.0}
appimage=${2:?path to the release AppImage}
out=${3:-$here}
sum=$(sha256sum "$appimage" | cut -d' ' -f1)
mkdir -p "$out"
sed -e "s/@VERSION@/$version/" -e "s/@SHA256@/$sum/" "$here/PKGBUILD.in" > "$out/PKGBUILD"
echo "Wrote $out/PKGBUILD for $version ($sum)"
