#!/usr/bin/env bash
# bundle-python.sh — build a self-contained CPython + GA deps runtime
# using python-build-standalone (PBS).
#
# Dev keeps uv (uv venv && uv pip install -e ".[ui]"). This script is
# distribution-only: produces a portable Python that runs GA without
# any system Python, uv, or compiler.
#
# Usage:
#   ./scripts/bundle-python.sh <arch>
#     arch ∈ {win-x64, mac-arm64, mac-x64, linux-x64}
#
# Output: dist/python-bundle/<arch>/python/
# Idempotent: re-running cleans and rebuilds the target arch.
# ~30s on a warm PBS cache.

set -euo pipefail

PBS_RELEASE="20260510"
PBS_PYTHON_VERSION="3.11.15"

# Pin GA deps for reproducible bundles. Core mirrors pyproject.toml
# [project.dependencies]; UI mirrors [project.optional-dependencies.ui].
# Bump together during maintenance — audit dep versions first.
GA_CORE_DEPS=(
  "requests==2.34.2"
  "beautifulsoup4==4.14.3"
  "bottle==0.13.4"
  "simple-websocket-server==0.4.4"
  "aiohttp==3.13.5"
)
GA_UI_DEPS=(
  "streamlit==1.45.0"
  "pywebview==5.3.0"
  "prompt_toolkit==3.0.50"
  "rich==13.9.4"
  "pillow==11.1.0"
  "textual==0.86.0"
)

say(){ printf '\033[36m[bundle-python]\033[0m %s\n' "$*"; }
ok(){ printf '\033[32m[ok]\033[0m %s\n' "$*"; }
die(){ printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

ARCH="${1:-}"
if [[ -z "$ARCH" ]]; then
  echo "Usage: $0 {win-x64|mac-arm64|mac-x64|linux-x64}" >&2
  exit 1
fi

case "$ARCH" in
  win-x64)    PBS_TRIPLE="x86_64-pc-windows-msvc" ;;
  mac-arm64)  PBS_TRIPLE="aarch64-apple-darwin" ;;
  mac-x64)    PBS_TRIPLE="x86_64-apple-darwin" ;;
  linux-x64)  PBS_TRIPLE="x86_64-unknown-linux-gnu" ;;
  *) die "Unknown arch: $ARCH" ;;
esac

PBS_FILE="cpython-${PBS_PYTHON_VERSION}+${PBS_RELEASE}-${PBS_TRIPLE}-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/dist/python-bundle/${ARCH}"
CACHE_DIR="${REPO_ROOT}/.cache/pbs"

say "arch=${ARCH} triple=${PBS_TRIPLE}"
say "output: ${OUT_DIR}/python"

mkdir -p "$CACHE_DIR"
if [[ ! -f "${CACHE_DIR}/${PBS_FILE}" ]]; then
  say "downloading PBS..."
  curl -fL --retry 3 -A "ga-bundle" -o "${CACHE_DIR}/${PBS_FILE}" "$PBS_URL"
else
  say "PBS cached"
fi

# Clean output, extract fresh.
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
tar -xzf "${CACHE_DIR}/${PBS_FILE}" -C "$OUT_DIR"

PYTHON_DIR="${OUT_DIR}/python"
if [[ "$ARCH" == "win-x64" ]]; then
  PYTHON_BIN="${PYTHON_DIR}/python.exe"
else
  PYTHON_BIN="${PYTHON_DIR}/bin/python3"
fi

[[ -x "$PYTHON_BIN" ]] || die "python binary not found at $PYTHON_BIN"
ok "Python: $("$PYTHON_BIN" --version)"

say "installing GA core deps..."
"$PYTHON_BIN" -m pip install \
  --no-warn-script-location \
  --no-compile \
  --disable-pip-version-check \
  --quiet \
  "${GA_CORE_DEPS[@]}"

say "installing GA UI deps..."
"$PYTHON_BIN" -m pip install \
  --no-warn-script-location \
  --no-compile \
  --disable-pip-version-check \
  --quiet \
  "${GA_UI_DEPS[@]}"

# Verify the dep import chain. Skip pywebview/textual here — pywebview
# needs system GTK on Linux to import fully, textual needs a TTY. pip
# install succeeding is sufficient for those; runtime import is on the
# user's system.
say "verifying deps..."
"$PYTHON_BIN" -c '
import requests, bs4, bottle, aiohttp, simple_websocket_server
import streamlit, prompt_toolkit, rich, PIL
print("  deps OK")
'

BUNDLE_SIZE=$(du -sh "$PYTHON_DIR" | awk '{print $1}')
ok "done. bundle size: ${BUNDLE_SIZE}"
ok "python bin: ${PYTHON_BIN}"
