#!/usr/bin/env bash
# build-installer.sh — build a native installer for one arch.
#
# Dispatcher: arch → platform builder, with a host-OS guard (Win host
# refuses mac .pkg, etc.). Followed by a verify stage that checks the
# artifact exists and lists its top-level contents.
#
# Usage:
#   ./scripts/build-installer.sh <arch> [--sign <cert-ref>] [--no-verify]
#     arch   ∈ {win-x64, mac-arm64, mac-x64, linux-x64}
#     --sign <cert-ref>   reserved for future code-signing (NOT implemented;
#                         passing it currently errors — see design §3.2)
#     --no-verify         skip the post-build verify stage
#
# Prereq:
#   ./scripts/assemble-dist-local.sh <arch>   # must have produced the
#       portable archive at dist/release/GenericAgent-<ver>-<arch>.{zip|tar.gz}
#
# Output:
#   dist/release/GenericAgent-<ver>-<arch>.{exe|pkg|deb|rpm}
#   (linux-x64 produces BOTH .deb and .rpm)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installers/common.sh
. "$SCRIPT_DIR/installers/common.sh"

ARCH="${1:-}"
[[ -n "$ARCH" ]] || die "Usage: $0 <arch> [--sign <cert-ref>] [--no-verify]"
shift

SIGN=""
VERIFY=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sign)      SIGN="${2:?--sign requires a value}"; shift 2 ;;
    --no-verify) VERIFY=0; shift ;;
    *)           die "Unknown flag: $1" ;;
  esac
done

[[ -z "$SIGN" ]] || die "--sign is reserved for future use; signing not implemented (design §3.2)"

ga_assert_host_can_build "$ARCH"
VERSION="$(ga_version)"

case "$ARCH" in
  win-x64)                SRC_EXT=zip;  OUT_EXT=exe ;;
  mac-arm64|mac-x64)      SRC_EXT=tar.gz; OUT_EXT=pkg ;;
  linux-x64)              SRC_EXT=tar.gz; OUT_EXT=deb ;;
  *) die "Unknown arch: $ARCH" ;;
esac

SRC_ARCHIVE="$(ga_artifact_path "$ARCH" "$VERSION" "$SRC_EXT")"
ga_artifact_exists "$SRC_ARCHIVE" \
  || die "source archive not found: $SRC_ARCHIVE
       run first: ./scripts/assemble-dist-local.sh $ARCH"

say "arch=$ARCH version=$VERSION"
say "source archive: $SRC_ARCHIVE"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---- build ----
case "$ARCH" in
  win-x64)
    # Find Inno Setup compiler (ISCC). PATH first, then default install dirs
    # (git-bash /c/... and WSL /mnt/c/... forms).
    ISCC=""
    if command -v iscc >/dev/null 2>&1; then
      ISCC="$(command -v iscc)"
    else
      for p in \
        "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" \
        "/mnt/c/Program Files (x86)/Inno Setup 6/ISCC.exe" \
        "/c/Program Files/Inno Setup 6/ISCC.exe" \
        "/mnt/c/Program Files/Inno Setup 6/ISCC.exe"; do
        [[ -x "$p" ]] && { ISCC="$p"; break; }
      done
    fi
    [[ -n "$ISCC" ]] || die "Inno Setup compiler (ISCC) not found. Install Inno Setup 6 or put iscc on PATH."

    # Convert bash paths to Windows form for ISCC (a native Windows binary).
    to_win() {
      if   command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"
      elif command -v wslpath >/dev/null 2>&1; then wslpath -w "$1"
      else echo "$1"
      fi
    }

    # Extract the portable archive into a staging dir; ISCC packs it.
    STAGE="$WORK/stage"
    mkdir -p "$STAGE"
    # Extract portable archive (zip for win, tar.gz for mac/linux) into staging.
    case "$SRC_ARCHIVE" in
      *.zip)
        if command -v unzip >/dev/null 2>&1; then unzip -q "$SRC_ARCHIVE" -d "$STAGE"
        elif command -v bsdtar >/dev/null 2>&1; then bsdtar -xf "$SRC_ARCHIVE" -C "$STAGE"
        else tar -xf "$SRC_ARCHIVE" -C "$STAGE"   # tar=bsdtar on Win/macOS
        fi ;;
      *) tar -xf "$SRC_ARCHIVE" -C "$STAGE" ;;
    esac   # → $STAGE/GenericAgent/

    # Place a branded icon at the payload root for the .lnk shortcuts.
    cp "$REPO_ROOT/frontends/desktop/src-tauri/icons/icon.ico" \
       "$STAGE/GenericAgent/ga.ico" 2>/dev/null || true

    OUT_DIR="$REPO_ROOT/dist/release"
    mkdir -p "$OUT_DIR"
    # VersionInfoVersion must be 4-part numeric (x.y.z.w); dev suffixes like
    # "+g<sha>.dirty" are illegal there. Strip suffix and pad to 4 parts.
    # AppVersion (display string) keeps the full version incl. dev suffix.
    ver_base="${VERSION%%[+-]*}"
    IFS=. read -ra _vp <<< "$ver_base"
    while (( ${#_vp[@]} < 4 )); do _vp+=(0); done
    VERSION_NUM="${_vp[0]}.${_vp[1]}.${_vp[2]}.${_vp[3]}"
    say "running ISCC..."
    # ISCC is a native Windows binary; under git-bash/MSYS, argv that look like
    # paths (the /Q /D /O /F flags here) get auto-converted to Windows form and
    # mangled (e.g. /Q → a drive path), which makes ISCC report "You may not
    # specify more than one script filename". Suppress MSYS path conversion for
    # this single invocation only — cygpath above already produced Windows paths.
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" \
    "$ISCC" /Q \
      /DSourceRoot="$(to_win "$STAGE")" \
      /DAppVersion="$VERSION" \
      /DVersionInfoVersion="$VERSION_NUM" \
      /DAppArch="$ARCH" \
      /O"$(to_win "$OUT_DIR")" \
      /F"GenericAgent-$VERSION-$ARCH" \
      "$(to_win "$SCRIPT_DIR/installers/win.iss")"
    ;;
  mac-arm64|mac-x64)
    bash "$SCRIPT_DIR/installers/mac-pkg.sh" "$ARCH" "$VERSION" "$SRC_ARCHIVE"
    ;;
  linux-x64)
    bash "$SCRIPT_DIR/installers/linux-deb.sh" "$ARCH" "$VERSION" "$SRC_ARCHIVE"
    bash "$SCRIPT_DIR/installers/linux-rpm.sh" "$ARCH" "$VERSION" "$SRC_ARCHIVE"
    ;;
esac

# ---- verify ----
if [[ "$VERIFY" == "1" ]]; then
  say "verify stage..."
  ART="$(ga_artifact_path "$ARCH" "$VERSION" "$OUT_EXT")"
  ga_artifact_exists "$ART" || die "verify failed: artifact not found: $ART"
  say "  artifact: $ART ($(du -h "$ART" | awk '{print $1}'))"
  case "$ARCH" in
    win-x64)
      say "  (Win .exe is opaque; run installer to verify contents)"
      ;;
    mac-arm64|mac-x64)
      ga_require pkgutil
      pkgutil --expand "$ART" "$WORK/pkg-expand" 2>/dev/null || true
      say "  pkg expanded (top-level):"
      ls -1 "$WORK/pkg-expand" 2>/dev/null | sed 's/^/    /' || true
      ;;
    linux-x64)
      if command -v dpkg-deb >/dev/null 2>&1; then
        say "  .deb contents (top 5):"
        dpkg-deb -c "$ART" 2>/dev/null | head -5 | sed 's/^/    /' || true
      fi
      ;;
  esac
  ok "verify ok"
fi

ok "done: $(ga_artifact_path "$ARCH" "$VERSION" "$OUT_EXT")"
