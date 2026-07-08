//! §9.1 Integration test: CLI dispatch → event stream → reattach
//!
//! Tests the full daemon lifecycle: start → create sessions → watch events →
//! stop → restart (reattach) → verify sessions persist.
//!
//! Prerequisites: §7 daemon mode + §7.2 event consumption + §7.3 reattach
//! must be substantially complete. Run with `cargo test --test e2e_daemon_lifecycle -- --ignored`.

use assert_cmd::Command;
use predicates::prelude::*;
use std::thread;
use std::time::Duration;

/// Helper: get the `ga` binary as an assert_cmd Command
fn ga() -> Command {
    Command::cargo_bin("ga").unwrap()
}

/// Helper: ensure daemon is stopped before/after tests
fn ensure_daemon_stopped() {
    let _ = ga().args(["daemon-stop"]).assert();
    thread::sleep(Duration::from_millis(500));
}

/// Helper: wait for daemon to become ready
fn wait_for_daemon(timeout_secs: u64) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed().as_secs() < timeout_secs {
        if ga().arg("daemon-status").assert().try_success().is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

#[test]
fn test_ga_status_without_daemon() {
    // `ga status` should work even without daemon (shows empty state)
    ga().arg("status").assert().success();
}

#[test]
#[ignore = "daemon detach mode not yet implemented"]
fn test_daemon_detach_and_stop() {
    ensure_daemon_stopped();

    // Start daemon in detached mode
    ga().args(["daemon-start"]).assert().success();
    assert!(wait_for_daemon(5), "Daemon did not become ready within timeout");

    // Verify daemon is running
    ga().arg("daemon-status").assert().success();

    // Stop daemon
    ga().args(["daemon-stop"]).assert().success();
    thread::sleep(Duration::from_millis(500));

    // Verify daemon stopped
    ga().arg("daemon-status").assert().failure();
}

#[test]
#[ignore = "daemon detach mode not yet implemented"]
fn test_session_create_and_list() {
    ensure_daemon_stopped();

    // Start daemon
    ga().args(["daemon-start"]).assert().success();
    assert!(wait_for_daemon(5), "Daemon did not become ready");

    // Create GA session
    ga().args(["session", "new", "--runner=ga", "--name=test-ga-e2e"])
        .assert()
        .success();

    // Create generic PTY session
    ga().args(["session", "new", "--runner=generic", "--name=test-pty-e2e"])
        .assert()
        .success();

    // List sessions — should contain both
    ga().args(["session", "list"])
        .assert()
        .success()
        .stdout(predicate::str::contains("test-ga-e2e"))
        .stdout(predicate::str::contains("test-pty-e2e"));

    // Cleanup
    ensure_daemon_stopped();
}

#[test]
#[ignore = "daemon detach mode not yet implemented"]
fn test_daemon_reattach_sessions() {
    ensure_daemon_stopped();

    // Start daemon, create sessions
    ga().args(["daemon-start"]).assert().success();
    assert!(wait_for_daemon(5), "Daemon did not become ready");

    ga().args(["session", "new", "--runner=ga", "--name=reattach-test"])
        .assert()
        .success();

    // Stop daemon
    ga().args(["daemon-stop"]).assert().success();
    thread::sleep(Duration::from_millis(500));

    // Restart daemon — sessions should reattach
    ga().args(["daemon-start"]).assert().success();
    assert!(wait_for_daemon(5), "Daemon did not become ready on restart");

    // Verify session still exists after reattach
    ga().args(["session", "list"])
        .assert()
        .success()
        .stdout(predicate::str::contains("reattach-test"));

    // Cleanup
    ensure_daemon_stopped();
}

#[test]
#[ignore = "daemon foreground mode not yet implemented"]
fn test_daemon_foreground() {
    // Foreground daemon should run and be stoppable
    // We test this by spawning it in a thread and stopping it
    ensure_daemon_stopped();

    let handle = thread::spawn(|| {
        ga().args(["daemon-start", "--foreground"]).assert().success();
    });

    // Give it time to start
    thread::sleep(Duration::from_secs(2));

    // Stop it
    ga().args(["daemon-stop"]).assert().success();

    let _ = handle.join();
}
