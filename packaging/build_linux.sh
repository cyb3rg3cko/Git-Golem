#!/usr/bin/env bash
# Builds the Linux binary and a .deb package into dist/.
#
#   bash packaging/build_linux.sh
#
# Optional environment variables:
#   VERSION     e.g. 1.2.0 or v1.2.0 (default: latest git tag, else 0.1.0)
#   PYTHON      Python to build with (default: python3) — must have Tkinter
#   MAINTAINER  "Name <email>" for the .deb (default: from GitHub or your git config)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

APP=git-project-manager
PYTHON="${PYTHON:-python3}"
VERSION="${VERSION:-$(git describe --tags --abbrev=0 2>/dev/null || echo 0.1.0)}"
VERSION="${VERSION#v}"
DEB_VERSION="${VERSION//-/\~}"          # 1.0.0-beta -> 1.0.0~beta (sorts before 1.0.0)
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

if [ -z "${MAINTAINER:-}" ]; then
    if [ -n "${GITHUB_REPOSITORY_OWNER:-}" ]; then
        MAINTAINER="$GITHUB_REPOSITORY_OWNER <$GITHUB_REPOSITORY_OWNER@users.noreply.github.com>"
    else
        MAINTAINER="$(git config user.name || echo Unknown) <$(git config user.email || echo unknown@localhost)>"
    fi
fi

[ -f icon.png ] || { echo "icon.png is missing from the project folder." >&2; exit 1; }
"$PYTHON" -c 'import tkinter' 2>/dev/null || {
    echo "$PYTHON has no Tkinter. Install it (e.g. sudo apt install python3-tk) or set PYTHON=..." >&2; exit 1; }

echo "==> Building $APP $VERSION ($ARCH)"
rm -rf build dist
"$PYTHON" -m venv build/venv
VPY="$ROOT/build/venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip pyinstaller pillow
"$VPY" packaging/make_icons.py

echo "==> PyInstaller"
"$VPY" -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name "$APP" \
    --add-data "$ROOT/icon.png:." \
    --workpath build/pyinstaller --specpath build --distpath dist \
    git_project_manager.py

echo "==> .deb"
PKG="build/deb/${APP}_${DEB_VERSION}_${ARCH}"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/pixmaps"
install -m 755 "dist/$APP" "$PKG/usr/bin/$APP"
install -m 644 build/icon-256.png "$PKG/usr/share/icons/hicolor/256x256/apps/$APP.png"
install -m 644 build/icon-256.png "$PKG/usr/share/pixmaps/$APP.png"

cat > "$PKG/usr/share/applications/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Git Project Manager
Comment=Maintain your GitHub projects
Exec=$APP
Icon=$APP
Terminal=false
Categories=Development;RevisionControl;
StartupWMClass=Git-project-manager
EOF

cat > "$PKG/DEBIAN/control" <<EOF
Package: $APP
Version: $DEB_VERSION
Section: devel
Priority: optional
Architecture: $ARCH
Depends: git
Maintainer: $MAINTAINER
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Description: Desktop app for maintaining GitHub projects
 Import, create and clone projects, run git commands from buttons,
 log in to GitHub once, create releases, and keep a log of every command.
EOF

dpkg-deb --build --root-owner-group "$PKG" "dist/${APP}_${DEB_VERSION}_${ARCH}.deb"
mv "dist/$APP" "dist/${APP}-${VERSION}-linux-${ARCH}"

echo "==> Done:"
ls -lh dist
