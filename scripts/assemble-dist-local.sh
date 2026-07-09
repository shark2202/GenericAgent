#!/usr/bin/env bash
# assemble-dist-local.sh — local mirror of release.yml's assemble step.
#
# Bundles the already-built PBS Python interpreter + full GA source tree +
# launchers into a portable distribution archive. The CI release.yml does
# this in GitHub Actions; this script lets you do the same locally for
# architectures you can build on this host.
#
# Usage:
#   ./scripts/assemble-dist-local.sh <arch> [version]
#     arch   ∈ {win-x64, mac-arm64, mac-x64, linux-x64}
#     version defaults to pyproject [project].version (+ dev suffix off-tag)
#
# Prereq:
#   ./scripts/bundle-python.sh <arch>   # must have run successfully first
#
# Output:
#   dist/release/GenericAgent-<version>-<arch>.{zip|tar.gz}

set -euo pipefail

ARCH="${1:-win-x64}"
VERSION="${2:-}"

case "$ARCH" in
  win-x64)                ARCHIVE_EXT=zip ;;
  mac-arm64|mac-x64|linux-x64) ARCHIVE_EXT=tar.gz ;;
  *) echo "Unknown arch: $ARCH" >&2; exit 1 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Default version: read from pyproject.toml [project].version; add a dev
# suffix (+g<sha7>[.dirty]) when not on the matching release tag, so local
# builds never collide with a clean release. Explicit $2 always wins.
if [[ -z "$VERSION" ]]; then
  _PP="${REPO_ROOT}/pyproject.toml"
  VERSION=$(grep -E '^version[[:space:]]*=' "$_PP" | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')
  [[ -n "$VERSION" ]] || { echo "could not parse version from $_PP" >&2; exit 1; }
  if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    _tag=$(git -C "$REPO_ROOT" describe --tags --exact-match HEAD 2>/dev/null || true)
    if [[ "$_tag" != "v${VERSION}" ]]; then
      _sha=$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)
      _dirty=""; [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]] && _dirty=".dirty"
      VERSION="${VERSION}+g${_sha}${_dirty}"
    fi
  fi
fi

BUNDLE_DIR="${REPO_ROOT}/dist/python-bundle/${ARCH}/python"

if [[ ! -d "$BUNDLE_DIR" ]]; then
  echo "PBS bundle not found: $BUNDLE_DIR" >&2
  echo "Run first: ./scripts/bundle-python.sh $ARCH" >&2
  exit 1
fi

say(){ printf '\033[36m[assemble]\033[0m %s\n' "$*"; }
ok(){ printf '\033[32m[ok]\033[0m %s\n' "$*"; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
DIST_DIR="${STAGE}/GenericAgent"
mkdir -p "$DIST_DIR"

say "arch=$ARCH version=$VERSION"
say "copying source tree (tracked + new untracked, .gitignore respected)..."
# Combine tracked files and new untracked files (respecting .gitignore),
# then copy each preserving directory structure. Paths with spaces are
# handled because we read NUL-delimited and feed xargs with -0.
{
  git -C "$REPO_ROOT" ls-files -z
  git -C "$REPO_ROOT" ls-files --others --exclude-standard -z
} | while IFS= read -r -d '' f; do
  [[ -z "$f" ]] && continue
  mkdir -p "${DIST_DIR}/$(dirname "$f")"
  cp "$REPO_ROOT/$f" "${DIST_DIR}/$f"
done

say "copying python bundle..."
cp -R "$BUNDLE_DIR" "${DIST_DIR}/python"

say "writing launchers (all four, so archive is portable across OSes)..."
cat > "${DIST_DIR}/run.cmd" <<'CMD'
@echo off
cd /d "%~dp0"
if not exist mykey.jsonc copy mykey_template.jsonc mykey.jsonc >nul 2>&1
if not exist mykey.py copy mykey_template_en.py mykey.py >nul 2>&1
"python\python.exe" "launch.pyw" %*
CMD

cat > "${DIST_DIR}/run.sh" <<'SH'
#!/bin/sh
cd "$(dirname "$0")"
[ -f mykey.jsonc ] || [ -f mykey.py ] || { [ -f mykey_template.jsonc ] && cp mykey_template.jsonc mykey.jsonc || cp mykey_template_en.py mykey.py; }
exec "python/bin/python3" "launch.pyw" "$@"
SH
chmod +x "${DIST_DIR}/run.sh"

cat > "${DIST_DIR}/ga.cmd" <<'CMD'
@echo off
cd /d "%~dp0"
"python\python.exe" -m ga_cli %*
CMD

cat > "${DIST_DIR}/ga.sh" <<'SH'
#!/bin/sh
cd "$(dirname "$0")"
exec "python/bin/python3" -m ga_cli "$@"
SH
chmod +x "${DIST_DIR}/ga.sh"

ARCHIVE_NAME="GenericAgent-${VERSION}-${ARCH}"
OUT_DIR="${REPO_ROOT}/dist/release"
mkdir -p "$OUT_DIR"
ARCHIVE_PATH="${OUT_DIR}/${ARCHIVE_NAME}.${ARCHIVE_EXT}"

say "creating archive: ${ARCHIVE_PATH}"
if [[ "$ARCHIVE_EXT" == "zip" ]]; then
  # .zip: GNU tar can't write it. Prefer `zip` if present (WSL/Linux/macOS);
  # else bsdtar `tar -a` (Windows System32 tar.exe = bsdtar, macOS libarchive).
  if command -v zip >/dev/null 2>&1; then
    (cd "$STAGE" && zip -qr "$ARCHIVE_PATH" GenericAgent)
  else
    tar -a -c -f "$ARCHIVE_PATH" -C "$STAGE" GenericAgent
  fi
else
  tar -czf "$ARCHIVE_PATH" -C "$STAGE" GenericAgent
fi

ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | awk '{print $1}')
ok "done: ${ARCHIVE_PATH} (${ARCHIVE_SIZE})"
ok "contents (top level):"
ls -1 "$DIST_DIR" | sed 's/^/    /'
