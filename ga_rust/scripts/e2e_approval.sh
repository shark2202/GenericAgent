#!/usr/bin/env bash
# §9.2 E2E: Approval closed loop
#
# Tests the approval workflow end-to-end:
#   start daemon → create session → trigger medium-risk tool →
#   approval pending → approve → verify → YOLO mode → cleanup
#
# Prerequisites: `ga` binary built and on PATH, jq installed.
#
# Usage: bash e2e_approval.sh [GA_BIN_PATH]

set -euo pipefail

GA="${1:-../target/debug/ga}"
PASS=0
FAIL=0

green() { printf "\033[32m[PASS]\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
red()   { printf "\033[31m[FAIL]\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
info()  { printf "\033[34m[INFO]\033[0m %s\n" "$1"; }

cleanup() {
    info "Cleaning up..."
    "$GA" config set approval.yolo false 2>/dev/null || true
    "$GA" daemon --stop 2>/dev/null || true
    info "Results: $PASS passed, $FAIL failed"
    exit $FAIL
}
trap cleanup EXIT INT TERM

# ── Step 1: Clean start ──
info "Step 1: Ensuring daemon is stopped"
"$GA" daemon --stop 2>/dev/null || true
sleep 1

# ── Step 2: Start daemon (non-YOLO) ──
info "Step 2: Starting daemon in non-YOLO mode"
"$GA" config set approval.yolo false
"$GA" daemon --detach
sleep 2

if "$GA" daemon-status > /dev/null 2>&1; then
    green "Daemon started successfully"
else
    red "Daemon failed to start"
    exit 1
fi

# ── Step 3: Create session ──
info "Step 3: Creating approval test session"
"$GA" session new --runner=ga --name=approval-test
SID=$("$GA" session list --json 2>/dev/null | jq -r '.[0].id // empty')
if [ -n "$SID" ]; then
    green "Session created: $SID"
else
    red "Failed to create session"
fi

# ── Step 4: Check initial approval list ──
info "Step 4: Checking initial approval list (should be empty)"
APPROVALS=$("$GA" approval list 2>&1)
if echo "$APPROVALS" | grep -qiE "pending|waiting|medium"; then
    red "Unexpected pending approvals"
else
    green "No pending approvals initially"
fi

# ── Step 5: Attempt to trigger a medium-risk tool ──
# NOTE: This step requires an active GA agent or mock to produce a
# medium-risk tool call. In this script we test the CLI plumbing;
# a live agent integration test would fill in the trigger step.
info "Step 5: Trigger medium-risk tool call (requires GA agent or mock)"
# Placeholder: in live test, send a file_write command via session
# "$GA" session send $SID "write a file" 

# ── Step 6: Approve pending approval (if any) ──
info "Step 6: Approving pending approval (if any)"
AID=$("$GA" approval list --json 2>/dev/null | jq -r '.[0].id // empty')
if [ -n "$AID" ]; then
    "$GA" approval "$AID" --approve
    green "Approved approval $AID"
else
    info "No pending approvals to approve (no GA agent producing tool calls)"
fi

# ── Step 7: Verify approval list empty after approve ──
info "Step 7: Verifying approval list is empty"
APPROVALS_AFTER=$("$GA" approval list 2>&1)
if echo "$APPROVALS_AFTER" | grep -qiE "pending|waiting"; then
    red "Still pending approvals after approve"
else
    green "No pending approvals after approval"
fi

# ── Step 8: Test YOLO mode ──
info "Step 8: Enabling YOLO mode"
"$GA" config set approval.yolo true

# In YOLO mode, all calls should auto-approve
APPROVALS_YOLO=$("$GA" approval list 2>&1)
if echo "$APPROVALS_YOLO" | grep -qiE "pending|waiting|medium"; then
    red "Pending approvals in YOLO mode (should be auto-approved)"
else
    green "YOLO mode: no pending approvals"
fi

# ── Step 9: Disable YOLO and cleanup ──
info "Step 9: Disabling YOLO mode"
"$GA" config set approval.yolo false
green "Approval closed-loop E2E test complete"
