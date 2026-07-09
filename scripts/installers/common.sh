#!/usr/bin/env bash
# common.sh — shared helpers for scripts/installers/*.sh and build-installer.sh.
#
# Sourced (not executed directly). Provides:
#   - logging (say/ok/die, matching bundle-python.sh / assemble-dist-local.sh)
#   - host-OS detection + arch→OS mapping + host-can-build guard
#   - version reading (single source of truth = pyproject.toml [project].version)
#   - artifact path + existence helpers
#
# Versioning contract (see docs/plans/2026-07-09-local-installer-packaging-design.md §3.1):
#   - ga_version_base  -> bare pyproject version, e.g. "0.2.0"
#   - ga_version       -> distribution version: bare on a release tag,
#                         else "<base>+g<sha7>[.dirty]" for dev builds;
#                         bare when not inside a git repo (tarball build).

# Repo root = two levels up from this file (scripts/installers/ -> scripts/ -> root).
_GA_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "${_GA_COMMON_DIR}/../.." && pwd)}"

# --- logging ---------------------------------------------------------------
say(){ printf '\033[36m[installer]\033[0m %s\n' "$*"; }
ok(){ printf '\033[32m[ok]\033[0m %s\n' "$*"; }
die(){ printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# --- host OS / arch --------------------------------------------------------

# Print the host OS family: win | mac | linux.
ga_host_os(){
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) printf 'win' ;;
    Darwin)                          printf 'mac' ;;
    Linux)                           printf 'linux' ;;
    *) die "unknown host OS: $(uname -s)" ;;
  esac
}

# Map a target arch to its OS family.
ga_arch_os(){
  case "$1" in
    win-x64)            printf 'win' ;;
    mac-arm64|mac-x64)  printf 'mac' ;;
    linux-x64)          printf 'linux' ;;
    *) die "unknown arch: $1" ;;
  esac
}

# Refuse to build an arch that doesn't match the host OS family.
# (bundle-python.sh is host-bound: Win can't build mac python, etc.)
ga_assert_host_can_build(){
  local arch="$1" host os
  host=$(ga_host_os)
  os=$(ga_arch_os "$arch")
  [[ "$host" == "$os" ]] || die "host OS '$host' cannot build arch '$arch' (needs $os host)"
}

# Require a command on PATH, else die.
ga_require(){
  command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"
}

# --- version ---------------------------------------------------------------

# Bare version from pyproject.toml [project].version.
ga_version_base(){
  local pyproject="${REPO_ROOT}/pyproject.toml"
  [[ -f "$pyproject" ]] || die "pyproject.toml not found: $pyproject"
  local ver
  ver=$(grep -E '^version[[:space:]]*=' "$pyproject" | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')
  [[ -n "$ver" ]] || die "could not parse [project].version from $pyproject"
  printf '%s' "$ver"
}

# Distribution version string.
#   release tag v<base>  -> base verbatim
#   any other git commit -> "<base>+g<sha7>[.dirty]"
#   not a git repo       -> base verbatim
ga_version(){
  local base tag sha suffix=""
  base=$(ga_version_base)
  git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { printf '%s' "$base"; return; }
  tag=$(git -C "$REPO_ROOT" describe --tags --exact-match HEAD 2>/dev/null || true)
  [[ "$tag" == "v${base}" ]] && { printf '%s' "$base"; return; }
  sha=$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)
  [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]] && suffix=".dirty"
  printf '%s+g%s%s' "$base" "$sha" "$suffix"
}

# --- artifact path / existence --------------------------------------------

# ga_artifact_path <arch> <version> <ext>
#   -> <REPO_ROOT>/dist/release/GenericAgent-<version>-<arch>.<ext>
ga_artifact_path(){
  printf '%s/dist/release/GenericAgent-%s-%s.%s' "$REPO_ROOT" "$2" "$1" "$3"
}

# Basic non-empty file check; dies on missing/empty.
ga_artifact_exists(){
  local path="$1"
  [[ -f "$path" ]] || die "artifact missing: $path"
  [[ -s "$path" ]] || die "artifact empty: $path"
}
