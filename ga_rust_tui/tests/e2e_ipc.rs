//! E2E integration tests: IPC communication between ga-tui and ga-core daemon.
//!
//! Covers spec §5 (IPC Protocol): all IpcMessage variants,
//! session lifecycle, output streaming, error handling.

use ga_tui::app::App;
use ga_tui::config::Config;
use ga_tui::event::{GoalSummary, IpcMessage};
use ga_tui::pane::{PaneStatus, RunnerKind};

fn make_app() -> App {
    App::new(Config::default())
}

// ── SessionNew ──

#[test]
fn session_new_creates_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].session_id, "s1");
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
}

#[test]
fn session_new_multiple() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s2".into(),
        runner: "codex".into(),
    });
    assert_eq!(app.panes.len(), 2);
    assert_eq!(app.panes[0].session_id, "s1");
    assert_eq!(app.panes[1].session_id, "s2");
}

#[test]
fn session_new_subprocess_runner() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "sub-1".into(),
        runner: "subprocess".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
}

// ── SessionOutput ──

#[test]
fn session_output_appends_line() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "Hello world\n".into(),
    });
    assert!(app.panes[0].lines.iter().any(|l| l.text.contains("Hello world")));
}

#[test]
fn session_output_multiple_lines() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "Line 1\n".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "Line 2\n".into(),
    });
    assert!(app.panes[0].lines.iter().any(|l| l.text.contains("Line 1")));
    assert!(app.panes[0].lines.iter().any(|l| l.text.contains("Line 2")));
}

// ── SessionStatus ──

#[test]
fn session_status_working() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "working".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);
}

#[test]
fn session_status_done() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "done".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Done);
}

#[test]
fn session_status_error() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "error".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Error);
}

#[test]
fn session_status_does_not_affect_other_sessions() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s2".into(),
        runner: "codex".into(),
    });
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "error".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Error);
    assert_eq!(app.panes[1].status, PaneStatus::Working);
}

// ── GoalUpdate ──

#[test]
fn goal_update_message() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    // GoalUpdate has goal_id + state fields
    app.handle_ipc(IpcMessage::GoalUpdate {
        goal_id: "g1".into(),
        state: "in_progress".into(),
    });
    // Verify app still functional after goal update
    assert_eq!(app.panes.len(), 1);
}

// ── GoalListResult ──

#[test]
fn goal_list_result_message() {
    let mut app = make_app();
    let goals = vec![
        GoalSummary {
            id: "g1".into(),
            title: "First goal".into(),
            state: "done".into(),
            priority: 1,
        },
        GoalSummary {
            id: "g2".into(),
            title: "Second goal".into(),
            state: "in_progress".into(),
            priority: 2,
        },
    ];
    app.handle_ipc(IpcMessage::GoalListResult { goals });
    assert_eq!(app.panes.len(), 0); // no panes created by goal list
}

// ── GoalError ──

#[test]
fn goal_error_message() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::GoalError {
        goal_id: Some("g1".into()),
        message: "Something went wrong".into(),
    });
    // App should still be functional
    assert!(!app.should_quit);
}

#[test]
fn goal_error_without_goal_id() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::GoalError {
        goal_id: None,
        message: "General error".into(),
    });
    assert!(!app.should_quit);
}

// ── Error ──

#[test]
fn ipc_error_message() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::Error {
        message: "Connection lost".into(),
    });
    // App should handle error gracefully
    assert!(!app.should_quit);
}
