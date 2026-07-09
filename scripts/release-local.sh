#!/usr/bin/env bash
# release-local.sh — one-shot local release: bundle → assemble → build-installer.
#
# Replaces the GitHub Actions release.yml flow (design: drop CI, keep archive +
# installer). Run on the build host for the target arch; the host-OS guard in
# common.sh refuses cross-platform builds (Win host can't build mac .pkg, etc.).
#
# Usage:
#   ./scripts/release-local.sh <arch> [--dry-run] [--no-bundle] [--no-verify]
#     arch       ∈ {win-x64, mac-arm64, mac-x64, linux-x64}
#     --dry-run  stop after assemble (archive only, no installer)
#     --no-bundle  skip bundle-python (assume dist/python-bundle/<arch> ready;
#                  useful when re-assembling after source-only changes)
#     --no-verify  skip build-installer's post-build verify stage
#
# Output:
#   dist/release/GenericAgent-<ver>-<arch>.{zip|tar.gz}   (always)
#   dist/release/GenericAgent-<ver>-<arch>.{exe|pkg|deb|rpm}  (unless --dry-run)
#
# Design: docs/plans/2026-07-09-local-installer-packaging-design.md §1 + §3.3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=installers/common.sh
. "$SCRIPT_DIR/installers/common.sh"

ARCH="${1:-}"
[[ -n "$ARCH" ]] || die "Usage: $0 <arch> [--dry-run] [--no-bundle] [--no-verify]"
shift

DRY_RUN=0
NO_BUNDLE=0
VERIFY=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)    DRY_RUN=1; shift ;;
    --no-bundle)  NO_BUNDLE=1; shift ;;
    --no-verify)  VERIFY=0; shift ;;
    *)            die "Unknown flag: $1" ;;
  esac
done

ga_assert_host_can_build "$ARCH"
VERSION="$(ga_version)"

case "$ARCH" in
  win-x64)                            SRC_EXT=zip ;;
  mac-arm64|mac-x64|linux-x64)        SRC_EXT=tar.gz ;;
  *) die "Unknown arch: $ARCH" ;;
esac

say "release-local: arch=$ARCH version=$VERSION$([[ $DRY_RUN == 1 ]] && echo ' (dry-run)')"

# Step 1 — bundle python (PBS CPython + GA deps). Slow (~30s warm); skippable.
if [[ "$NO_BUNDLE" == "1" ]]; then
  say "step 1/3: bundle python — SKIPPED (--no-bundle)"
else
  say "step 1/3: bundle python..."
  bash "$SCRIPT_DIR/bundle-python.sh" "$ARCH"
fi

# Step 2 — assemble portable archive.
say "step 2/3: assemble portable archive..."
bash "$SCRIPT_DIR/assemble-dist-local.sh" "$ARCH" "$VERSION"
ARCHIVE="$(ga_artifact_path "$ARCH" "$VERSION" "$SRC_EXT")"
ga_artifact_exists "$ARCHIVE" || die "assemble did not produce: $ARCHIVE"
ok "archive: $ARCHIVE ($(du -h "$ARCHIVE" | awk '{print $1}'))"

if [[ "$DRY_RUN" == "1" ]]; then
  ok "dry-run stop — archive ready, no installer built."
  exit 0
fi

# Step 3 — build native installer.
say "step 3/3: build installer..."
BUILD_ARGS=("$ARCH")
[[ "$VERIFY" == "1" ]] || BUILD_ARGS+=(--no-verify)
bash "$SCRIPT_DIR/build-installer.sh" "${BUILD_ARGS[@]}"

ok "release-local done."
