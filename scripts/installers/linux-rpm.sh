#!/usr/bin/env bash
# linux-rpm.sh — build an .rpm installer for GenericAgent on Linux.
#
# Mirrors linux-deb.sh: /opt/GenericAgent + .desktop + /usr/local/bin/ga shim,
# with rpm-native install/remove. Declares webkit2gtk/gtk runtime requires.
#
# Usage:
#   linux-rpm.sh <arch> <version> <src-archive>
#     arch        must be linux-x64
#     version     full version string (e.g. 0.1.0+g9f3a2b.dirty)
#     src-archive dist/release/GenericAgent-<ver>-linux-x64.tar.gz (from assemble)
#
# Output: dist/release/GenericAgent-<version>-linux-x64.rpm
# Prereq: run on Fedora/RHEL/SUSE; rpmbuild available (rpm-build).
# Design: docs/plans/2026-07-09-local-installer-packaging-design.md §2 linux.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ARCH="${1:?arch required}"
VERSION="${2:?version required}"
SRC_ARCHIVE="${3:?src-archive required}"
case "$ARCH" in
  linux-x64) ;;
  *) die "linux-rpm.sh only builds linux-x64, got: $ARCH" ;;
esac

[[ "$(ga_host_os)" == "linux" ]] || die "linux-rpm.sh must run on Linux (host=$(ga_host_os))"
ga_require rpmbuild

# Internal rpm Version uses the bare base (dev suffix dropped); rpm version
# strings disallow '-' and dislike '+'. Release is fixed at 1.
PKG_VERSION="$(ga_version_base)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Stage the payload tree exactly like the .deb (so both packages are identical
# on disk), then hand it to rpmbuild via a spec + %install cp.
STAGE="$WORK/stage"
say "extracting source archive..."
tar -xf "$SRC_ARCHIVE" -C "$WORK"              # → $WORK/GenericAgent/
SRC_DIR="$WORK/GenericAgent"
[[ -d "$SRC_DIR/python/bin" ]] || die "no python/bin in source archive (assemble not run for $ARCH?)"

OPT_DIR="$STAGE/opt/GenericAgent"
mkdir -p "$OPT_DIR"
cp -a "$SRC_DIR/." "$OPT_DIR/"
chmod +x "$OPT_DIR/python/bin/python3" 2>/dev/null || true

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

BIN_DIR="$STAGE/usr/local/bin"
mkdir -p "$BIN_DIR"
ln -s /opt/GenericAgent/ga-cli.sh "$BIN_DIR/ga"

# rpm build tree + spec.
TOPDIR="$WORK/rpm"
mkdir -p "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

cat > "$TOPDIR/SPECS/genericagent.spec" <<SPEC
Name:           genericagent
Version:        ${PKG_VERSION}
Release:        1%{?dist}
Summary:        Minimalist self-evolving autonomous agent framework
License:        MIT
BuildArch:      x86_64
Requires:       webkit2gtk4.1
Requires:       gtk3
AutoReqProv:    no

%description
Self-contained CPython runtime + GUI (pywebview/streamlit) + TUI + CLI.
Installed to /opt/GenericAgent; 'ga' command available in PATH.

%install
mkdir -p "%{buildroot}"
cp -a "${STAGE}/." "%{buildroot}/"

%files
"/opt/GenericAgent"
"/usr/share/applications/genericagent.desktop"
"/usr/local/bin/ga"

%post
update-desktop-database -q /usr/share/applications 2>/dev/null || :

%postun
rm -f /usr/local/bin/ga 2>/dev/null || :
update-desktop-database -q /usr/share/applications 2>/dev/null || :
SPEC

say "building .rpm (version=${PKG_VERSION}-1)..."
rpmbuild -bb \
  --define "_topdir ${TOPDIR}" \
  --define "_rpmfilename %%{ARCH}/%%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm" \
  "$TOPDIR/SPECS/genericagent.spec" >/dev/null

# Locate the built rpm (arch subdir under RPMS).
BUILT="$(find "$TOPDIR/RPMS" -name 'genericagent-*.rpm' -print -quit)"
[[ -n "$BUILT" ]] || die "rpmbuild did not produce an rpm"

OUT="$(ga_artifact_path "$ARCH" "$VERSION" rpm)"
mkdir -p "$(dirname "$OUT")"
cp "$BUILT" "$OUT"

if ga_artifact_exists "$OUT"; then
  ok "done: $OUT ($(du -h "$OUT" | awk '{print $1}'))"
  ok "  inspect: rpm -qlp \"$OUT\" | head -20"
else
  die "failed to place rpm at $OUT"
fi
