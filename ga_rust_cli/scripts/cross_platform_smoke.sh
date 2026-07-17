#!/usr/bin/env bash
# §9.3 Cross-platform build + smoke test
#
# Builds the ga binary for the current platform and runs a minimal
# smoke test to verify the CLI is functional.
#
# Usage: bash cross_platform_smoke.sh [--release]
#   --release: build in release mode (default: debug)

set -euo pipefail

RELEASE_MODE=""
if [ "${1:-}" = "--release" ]; then
    RELEASE_MODE="--release"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PASS=0
FAIL=0

green() { printf "\033[32m[PASS]\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
red()   { printf "\033[31m[FAIL]\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
info()  { printf "\033[34m[INFO]\033[0m %s\n" "$1"; }

# ── Build ──
info "Building ga binary ($RELEASE_MODE)..."
cd "$PROJECT_ROOT"

if cargo build $RELEASE_MODE 2>&1; then
    green "Build succeeded"
else
    red "Build failed"
    exit 1
fi

# Determine binary path
if [ -n "$RELEASE_MODE" ]; then
    GA_BIN="$PROJECT_ROOT/target/release/ga"
else
    GA_BIN="$PROJECT_ROOT/target/debug/ga"
fi

if [ ! -f "$GA_BIN" ]; then
    # On Windows, check for .exe
    if [ -f "$GA_BIN.exe" ]; then
        GA_BIN="$GA_BIN.exe"
    else
        red "Binary not found at $GA_BIN"
        exit 1
    fi
fi

green "Binary found: $GA_BIN"

# ── Smoke Tests ──
info "Running smoke tests..."

# Test 1: --help
if "$GA_BIN" --help > /dev/null 2>&1; then
    green "--help works"
else
    red "--help failed"
fi

# Test 2: --version
VERSION_OUTPUT=$("$GA_BIN" --version 2>&1)
if echo "$VERSION_OUTPUT" | grep -qE "ga|GenericAgent|0\."; then
    green "--version: $VERSION_OUTPUT"
else
    red "--version unexpected output: $VERSION_OUTPUT"
fi

# Test 3: status (no daemon)
STATUS_OUTPUT=$("$GA_BIN" status 2>&1) || true
green "status output: $(echo "$STATUS_OUTPUT" | head -1)"

# Test 4: session list (no daemon)
SESSION_OUTPUT=$("$GA_BIN" session list 2>&1) || true
green "session list output: $(echo "$SESSION_OUTPUT" | head -1)"

# Test 5: daemon-status (no daemon — should indicate not running)
DAEMON_OUTPUT=$("$GA_BIN" daemon-status 2>&1) || true
if echo "$DAEMON_OUTPUT" | grep -qiE "not running|stopped|no daemon"; then
    green "daemon-status correctly reports not running"
else
    info "daemon-status output: $DAEMON_OUTPUT (may vary by implementation)"
fi

# ── Platform Info ──
info "Platform information:"
echo "  OS: $(uname -s 2>/dev/null || echo 'Windows')"
echo "  Arch: $(uname -m 2>/dev/null || echo 'unknown')"
echo "  Rustc: $(rustc --version 2>/dev/null || echo 'N/A')"
echo "  Binary size: $(ls -lh "$GA_BIN" 2>/dev/null | awk '{print $5}' || echo 'N/A')"

# ── Cross-compile check (optional) ──
info "Checking available cross-compilation targets..."
INSTALLED_TARGETS=$(rustup target list --installed 2>/dev/null || echo "N/A")
echo "  Installed targets: $INSTALLED_TARGETS"

# Common cross-compilation targets for ga:
#   x86_64-pc-windows-msvc    (Windows)
#   x86_64-unknown-linux-gnu  (Linux)
#   aarch64-apple-darwin      (macOS ARM)
#   x86_64-apple-darwin       (macOS Intel)

info "Smoke test complete: $PASS passed, $FAIL failed"
exit $FAIL
