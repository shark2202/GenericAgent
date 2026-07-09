#!/usr/bin/env bash
# linux-deb.sh — build a .deb installer for GenericAgent on Linux.
#
# Installs a self-contained tree to /opt/GenericAgent, a .desktop entry for the
# app menu, and a /usr/local/bin/ga CLI shim. Declares runtime deps for
# pywebview (webkit2gtk + GTK) so apt pulls them in. Native apt remove cleans up.
#
# Usage:
#   linux-deb.sh <arch> <version> <src-archive>
#     arch        must be linux-x64
#     version     full version string (e.g. 0.1.0+g9f3a2b.dirty)
#     src-archive dist/release/GenericAgent-<ver>-linux-x64.tar.gz (from assemble)
#
# Output: dist/release/GenericAgent-<version>-linux-x64.deb
# Prereq: run on Debian/Ubuntu; dpkg-deb available (dpkg-dev).
# Design: docs/plans/2026-07-09-local-installer-packaging-design.md §2 linux.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ARCH="${1:?arch required}"
VERSION="${2:?version required}"
SRC_ARCHIVE="${3:?src-archive required}"
case "$ARCH" in
  linux-x64) ;;
  *) die "linux-deb.sh only builds linux-x64, got: $ARCH" ;;
esac

[[ "$(ga_host_os)" == "linux" ]] || die "linux-deb.sh must run on Linux (host=$(ga_host_os))"
ga_require dpkg-deb

# Internal deb version uses the bare base (dev suffix dropped) for safety.
PKG_VERSION="$(ga_version_base)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STAGE="$WORK/stage"                            # payload root → installs at /
say "extracting source archive..."
tar -xf "$SRC_ARCHIVE" -C "$WORK"              # → $WORK/GenericAgent/
SRC_DIR="$WORK/GenericAgent"
[[ -d "$SRC_DIR/python/bin" ]] || die "no python/bin in source archive (assemble not run for $ARCH?)"

OPT_DIR="$STAGE/opt/GenericAgent"
mkdir -p "$OPT_DIR"
cp -a "$SRC_DIR/." "$OPT_DIR/"
chmod +x "$OPT_DIR/python/bin/python3" 2>/dev/null || true

# CLI shim — self-resolves through the /usr/local/bin/ga symlink (same as mac).
cat > "$OPT_DIR/ga-cli.sh" <<'SH'
#!/bin/sh
SELF="$0"
while [ -L "$SELF" ]; do
  cd "$(dirname "$SELF")"
  SELF="$(readlink "$SELF")"
done
cd "$(dirname "$SELF")"
exec "./python/bin/python3" -m ga_cli "$@"
SH
chmod +x "$OPT_DIR/ga-cli.sh"

ICON_SRC="$REPO_ROOT/frontends/desktop/src-tauri/icons/icon.png"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$OPT_DIR/ga.png"
else
  say "warn: icon.png not found at $ICON_SRC — .desktop will have no icon"
fi

DESKTOP_DIR="$STAGE/usr/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/genericagent.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=GenericAgent
Comment=Minimalist self-evolving autonomous agent framework
Exec=/opt/GenericAgent/run.sh
Icon=/opt/GenericAgent/ga.png
Terminal=false
Categories=Utility;Development;
DESK

# /usr/local/bin/ga as a payload symlink (dpkg tracks + removes it on apt remove).
BIN_DIR="$STAGE/usr/local/bin"
mkdir -p "$BIN_DIR"
ln -s /opt/GenericAgent/ga-cli.sh "$BIN_DIR/ga"

DEB_DIR="$STAGE/DEBIAN"
mkdir -p "$DEB_DIR"
INSTALLED_SIZE="$(du -sk "$OPT_DIR" | awk '{print $1}')"
cat > "$DEB_DIR/control" <<CTRL
Package: genericagent
Version: ${PKG_VERSION}-1
Section: utils
Priority: optional
Architecture: amd64
Depends: libwebkit2gtk-4.1-0, gir1.2-gtk-3.0
Maintainer: GenericAgent <noreply@genericagent.example>
Installed-Size: ${INSTALLED_SIZE}
Description: Minimalist self-evolving autonomous agent framework
 Self-contained CPython runtime + GUI (pywebview/streamlit) + TUI + CLI.
 Installed to /opt/GenericAgent; 'ga' command available in PATH.
CTRL

cat > "$DEB_DIR/postinst" <<'SH'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications
fi
exit 0
SH
chmod +x "$DEB_DIR/postinst"

cat > "$DEB_DIR/postrm" <<'SH'
#!/bin/sh
set -e
# dpkg removes payload files (incl. the /usr/local/bin/ga symlink), but clean a
# dangling one if a prior postinstall created it out-of-band.
rm -f /usr/local/bin/ga 2>/dev/null || true
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications
fi
exit 0
SH
chmod +x "$DEB_DIR/postrm"

say "building .deb (version=${PKG_VERSION}-1)..."
OUT="$(ga_artifact_path "$ARCH" "$VERSION" deb)"
mkdir -p "$(dirname "$OUT")"
# --root-owner-group: sets root:root ownership without needing fakeroot.
dpkg-deb --root-owner-group --build "$STAGE" "$OUT"

if ga_artifact_exists "$OUT"; then
  ok "done: $OUT ($(du -h "$OUT" | awk '{print $1}'))"
  ok "  inspect: dpkg-deb -c \"$OUT\" | head -20"
else
  die "dpkg-deb did not produce $OUT"
fi
