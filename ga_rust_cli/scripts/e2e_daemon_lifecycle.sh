#!/usr/bin/env bash
# §9.1 E2E: CLI dispatch → event stream → reattach
#
# Full end-to-end test of the daemon lifecycle and session management.
# Prerequisites: `ga` binary built and on PATH, jq installed.
#
# Usage: bash e2e_daemon_lifecycle.sh [GA_BIN_PATH]
#   GA_BIN_PATH defaults to ../target/debug/ga

set -euo pipefail

GA="${1:-../target/debug/ga}"
EVENTS_FILE="/tmp/ga_e2e_events_$$.jsonl"
PASS=0
FAIL=0

green() { printf "\033[32m[PASS]\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
red()   { printf "\033[31m[FAIL]\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
info()  { printf "\033[34m[INFO]\033[0m %s\n" "$1"; }

cleanup() {
    info "Cleaning up..."
    kill "$WATCH_PID" 2>/dev/null || true
    "$GA" daemon --stop 2>/dev/null || true
    rm -f "$EVENTS_FILE"
    info "Results: $PASS passed, $FAIL failed"
    exit $FAIL
}
trap cleanup EXIT INT TERM

# ── Step 1: Ensure clean state ──
info "Step 1: Ensuring daemon is stopped"
"$GA" daemon --stop 2>/dev/null || true
sleep 1

# ── Step 2: Start daemon ──
info "Step 2: Starting daemon in detached mode"
"$GA" daemon --detach
sleep 2

if "$GA" daemon-status > /dev/null 2>&1; then
    green "Daemon started successfully"
else
    red "Daemon failed to start"
    exit 1
fi

# ── Step 3: Create GA session ──
info "Step 3: Creating GA session"
"$GA" session new --runner=ga --name=test-ga
GA_SID=$("$GA" session list --json 2>/dev/null | jq -r '.[0].id // empty')
if [ -n "$GA_SID" ]; then
    green "GA session created: $GA_SID"
else
    red "Failed to create GA session"
fi

# ── Step 4: Create PTY session ──
info "Step 4: Creating generic PTY session"
"$GA" session new --runner=generic --name=test-pty
PTY_SID=$("$GA" session list --json 2>/dev/null | jq -r '.[1].id // empty')
if [ -n "$PTY_SID" ]; then
    green "PTY session created: $PTY_SID"
else
    red "Failed to create PTY session"
fi

# ── Step 5: Verify both sessions active ──
info "Step 5: Verifying both sessions active"
STATUS_OUTPUT=$("$GA" status 2>&1)
if echo "$STATUS_OUTPUT" | grep -q "test-ga" && echo "$STATUS_OUTPUT" | grep -q "test-pty"; then
    green "Both sessions visible in status"
else
    red "Sessions not both visible in status"
fi

# ── Step 6: Watch event stream ──
info "Step 6: Watching event stream"
"$GA" session watch "$GA_SID" > "$EVENTS_FILE" 2>/dev/null &
WATCH_PID=$!
sleep 1

# ── Step 7: Trigger activity ──
info "Step 7: Sending input to GA session"
"$GA" session send "$GA_SID" "hello" 2>/dev/null || true
sleep 2

EVENT_COUNT=$(grep -c "tool_call\|output" "$EVENTS_FILE" 2>/dev/null || echo "0")
if [ "$EVENT_COUNT" -gt 0 ]; then
    green "Event stream captured $EVENT_COUNT events"
else
    red "No events captured in stream (may be expected if no GA agent running)"
fi

# ── Step 8: Reattach test ──
info "Step 8: Testing daemon restart + session reattach"
"$GA" daemon --stop
sleep 1
"$GA" daemon --detach
sleep 2

if "$GA" daemon-status > /dev/null 2>&1; then
    green "Daemon restarted successfully"
else
    red "Daemon failed to restart"
fi

STATUS_AFTER=$("$GA" status 2>&1)
if echo "$STATUS_AFTER" | grep -q "reattach-test\|test-ga\|test-pty"; then
    green "Sessions reattached after daemon restart"
else
    red "Sessions not found after restart"
fi

# ── Step 9: Cleanup ──
info "Step 9: Archiving sessions and stopping daemon"
"$GA" session archive "$GA_SID" 2>/dev/null || true
"$GA" session archive "$PTY_SID" 2>/dev/null || true
"$GA" daemon --stop 2>/dev/null || true

green "E2E daemon lifecycle test complete"
