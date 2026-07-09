#!/usr/bin/env bash
# mac-pkg.sh — build a .pkg installer for GenericAgent on macOS.
#
# Produces a self-contained .app at /Applications/GenericAgent.app (Launchpad
# icon via Info.plist + icon.icns) and a /usr/local/bin/ga CLI shim. The .pkg
# needs admin auth (mac standard); uninstall.sh is bundled inside the .app.
#
# Usage:
#   mac-pkg.sh <arch> <version> <src-archive>
#     arch        ∈ {mac-arm64, mac-x64}
#     version     full version string (e.g. 0.1.0+g9f3a2b.dirty)
#     src-archive dist/release/GenericAgent-<ver>-<arch>.tar.gz (from assemble)
#
# Output: dist/release/GenericAgent-<version>-<arch>.pkg
# Prereq: run on macOS; pkgbuild + productbuild available (Xcode CLT).
#
# Design: docs/plans/2026-07-09-local-installer-packaging-design.md §2 mac.
# Deviation from design: the "optional CLI choice" (choices outline) is deferred
# to v1.1 — the /usr/local/bin/ga shim is always installed via postinstall.
# macOS version-string strictness means the .pkg *internal* version uses the
# bare base (e.g. 0.1.0); the *filename* keeps the full dev-suffix version.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ARCH="${1:?arch required (mac-arm64|mac-x64)}"
VERSION="${2:?version required}"
SRC_ARCHIVE="${3:?src-archive required}"

[[ "$(ga_host_os)" == "mac" ]] || die "mac-pkg.sh must run on macOS (host=$(ga_host_os))"
case "$ARCH" in
  mac-arm64) HOST_ARCH="arm64" ;;
  mac-x64)   HOST_ARCH="x86_64" ;;
  *) die "arch must be mac-arm64 or mac-x64, got: $ARCH" ;;
esac

ga_require pkgbuild
ga_require productbuild

# macOS version fields dislike '+' / multiple dots; use the bare base internally.
PKG_VERSION="$(ga_version_base)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

APP_ROOT="$WORK/app-root"                       # payload root → installs at /
APP_DIR="$APP_ROOT/Applications/GenericAgent.app"
RES_DIR="$APP_DIR/Contents/Resources"
MACOS_DIR="$APP_DIR/Contents/MacOS"
mkdir -p "$RES_DIR" "$MACOS_DIR"

say "extracting source archive..."
tar -xf "$SRC_ARCHIVE" -C "$WORK"               # → $WORK/GenericAgent/
SRC_DIR="$WORK/GenericAgent"
[[ -d "$SRC_DIR/python/bin" ]] || die "no python/bin in source archive (assemble not run for $ARCH?)"

# Source tree (python/, launch.pyw, ga_cli/, frontends/, run.sh, ga.sh, ...) → Resources/
cp -a "$SRC_DIR/." "$RES_DIR/"
chmod +x "$RES_DIR/python/bin/python3" 2>/dev/null || true

# .app main executable — a shell script exec'ing the internal python on launch.pyw.
# (Per design future-caveat: if we later codesign+notarize, Gatekeeper may frown on
#  a script as the main executable; then swap in a ~20-line C stub. Not needed unsigned.)
cat > "$MACOS_DIR/GenericAgent" <<'SH'
#!/bin/sh
cd "$(dirname "$0")/../Resources"
exec "./python/bin/python3" "./launch.pyw" "$@"
SH
chmod +x "$MACOS_DIR/GenericAgent"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>GenericAgent</string>
  <key>CFBundleDisplayName</key><string>GenericAgent</string>
  <key>CFBundleIdentifier</key><string>com.genericagent.app</string>
  <key>CFBundleVersion</key><string>${PKG_VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${PKG_VERSION}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>GenericAgent</string>
  <key>CFBundleIconFile</key><string>icon.icns</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

ICON_SRC="$REPO_ROOT/frontends/desktop/src-tauri/icons/icon.icns"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$RES_DIR/icon.icns"
else
  say "warn: icon.icns not found at $ICON_SRC — .app will have a generic icon"
fi

# CLI shim — resolves its own dir through the /usr/local/bin symlink (BSD-readlink safe).
cat > "$RES_DIR/ga-cli.sh" <<'SH'
#!/bin/sh
# ga — GenericAgent CLI. Resolves own dir even through the /usr/local/bin symlink.
SELF="$0"
while [ -L "$SELF" ]; do
  cd "$(dirname "$SELF")"
  SELF="$(readlink "$SELF")"
done
cd "$(dirname "$SELF")"
exec "./python/bin/python3" -m ga_cli "$@"
SH
chmod +x "$RES_DIR/ga-cli.sh"

# uninstall.sh — run with sudo; removes app + CLI symlink. (User data lived inside
# the .app and is removed with it; back up mykey.jsonc/memory before running.)
cat > "$RES_DIR/uninstall.sh" <<'SH'
#!/bin/sh
[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo $0"; exit 1; }
rm -f /usr/local/bin/ga
rm -rf "/Applications/GenericAgent.app"
echo "GenericAgent removed. (User data inside the .app was removed with it.)"
SH
chmod +x "$RES_DIR/uninstall.sh"

# Component postinstall: create /usr/local/bin/ga → installed app's ga-cli.sh.
# $DSTROOT is the install root (usually /, or a chosen volume). Respects volume choice.
SCRIPTS_DIR="$WORK/scripts"
mkdir -p "$SCRIPTS_DIR"
cat > "$SCRIPTS_DIR/postinstall" <<'SH'
#!/bin/sh
APP="$DSTROOT/Applications/GenericAgent.app/Contents/Resources/ga-cli.sh"
mkdir -p /usr/local/bin
ln -sf "$APP" /usr/local/bin/ga
exit 0
SH
chmod +x "$SCRIPTS_DIR/postinstall"

say "building component package (version=$PKG_VERSION)..."
COMP_PKG="$WORK/GenericAgent-app.pkg"
pkgbuild \
  --root "$APP_ROOT" \
  --identifier com.genericagent.app \
  --version "$PKG_VERSION" \
  --scripts "$SCRIPTS_DIR" \
  --install-location / \
  --ownership recommended \
  "$COMP_PKG"

# Distribution: volume selection via <domains>; restricts to matching arch.
cat > "$WORK/Distribution.xml" <<XML
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<installer-gui-script minSpecVersion="2">
  <title>GenericAgent</title>
  <options hostArch="${HOST_ARCH}" customize="never" allow-external-scripts="no"/>
  <domains enable_anywhere="true" enable_currentUserHome="false" enable_localSystem="true"/>
  <choices-outline>
    <line choice="choice.app"/>
  </choices-outline>
  <choice id="choice.app" title="GenericAgent" description="GenericAgent ${PKG_VERSION}">
    <pkg-ref id="com.genericagent.app"/>
  </choice>
  <pkg-ref id="com.genericagent.app" version="${PKG_VERSION}" onConclusion="None">GenericAgent-app.pkg</pkg-ref>
</installer-gui-script>
XML

say "building product archive (.pkg)..."
OUT="$(ga_artifact_path "$ARCH" "$VERSION" pkg)"
mkdir -p "$(dirname "$OUT")"
productbuild \
  --distribution "$WORK/Distribution.xml" \
  --package-path "$WORK" \
  "$OUT"

if ga_artifact_exists "$OUT"; then
  ok "done: $OUT ($(du -h "$OUT" | awk '{print $1}'))"
  ok "  inspect: pkgutil --expand-full \"$OUT\" /tmp/ga-pkg-check"
else
  die "productbuild did not produce $OUT"
fi
