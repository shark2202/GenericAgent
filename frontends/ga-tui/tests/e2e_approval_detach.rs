//! E2E tests: Approval flow and session detach/attach lifecycle.
//!
//! Covers spec §6 (Approval Flow): approval_needed → blocked,
//! approval_resolved → working/done, detach/attach sessions.

use chrono::Local;
use ga_tui::app::App;
use ga_tui::config::Config;
use ga_tui::event::IpcMessage;
use ga_tui::pane::{ApprovalRequest, PaneStatus, RiskLevel};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

fn make_app() -> App {
    App::new(Config::default())
}

fn press(app: &mut App, code: KeyCode) {
    app.handle_key(KeyEvent::new(code, KeyModifiers::NONE));
}

fn make_approval(id: &str) -> ApprovalRequest {
    ApprovalRequest {
        id: id.to_string(),
        tool_name: "bash".to_string(),
        risk_level: RiskLevel::High,
        description: "Run command".to_string(),
        command_preview: "rm -rf /tmp/test".to_string(),
        timestamp: Local::now(),
    }
}

fn add_session(app: &mut App, sid: &str) {
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: sid.to_string(),
        runner: "subprocess".to_string(),
    });
}

// ── Approval flow ──

#[test]
fn approval_needed_blocks_pane() {
    let mut app = make_app();
    add_session(&mut app, "s1");
    assert_eq!(app.panes[0].status, PaneStatus::Working);

    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s1".into(),
        approval,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);
    assert!(app.panes[0].pending_approval.is_some());
}

#[test]
fn approval_resolved_approved_resumes() {
    let mut app = make_app();
    add_session(&mut app, "s1");

    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s1".into(),
        approval,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);

    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "s1".into(),
        approved: true,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);
    assert!(app.panes[0].pending_approval.is_none());
}

#[test]
fn approval_resolved_rejected_marks_done() {
    let mut app = make_app();
    add_session(&mut app, "s1");

    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s1".into(),
        approval,
    });

    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "s1".into(),
        approved: false,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Done);
    assert!(app.panes[0].pending_approval.is_none());
}

// ── Detach / Close ──

#[test]
fn close_active_pane_removes_pane() {
    let mut app = make_app();
    add_session(&mut app, "s1");
    add_session(&mut app, "s2");
    assert_eq!(app.panes.len(), 2);

    // 'c' closes active pane
    press(&mut app, KeyCode::Char('c'));
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].session_id, "s1");
}

#[test]
fn detach_session_removes_pane() {
    let mut app = make_app();
    add_session(&mut app, "s1");
    add_session(&mut app, "s2");
    assert_eq!(app.panes.len(), 2);

    // 'd' detaches (removes) active pane
    press(&mut app, KeyCode::Char('d'));
    assert_eq!(app.panes.len(), 1);
}

#[test]
fn close_last_pane_leaves_empty() {
    let mut app = make_app();
    add_session(&mut app, "s1");
    assert_eq!(app.panes.len(), 1);

    press(&mut app, KeyCode::Char('c'));
    assert_eq!(app.panes.len(), 0);
}

// ── Approval + Detach interaction ──

#[test]
fn approval_flow_then_close() {
    let mut app = make_app();
    add_session(&mut app, "s1");

    // Trigger approval
    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s1".into(),
        approval,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);

    // Approve
    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "s1".into(),
        approved: true,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);

    // Close the pane
    press(&mut app, KeyCode::Char('c'));
    assert_eq!(app.panes.len(), 0);
}

#[test]
fn approval_needed_on_wrong_session_ignored() {
    let mut app = make_app();
    add_session(&mut app, "s1");

    // Send approval for non-existent session
    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "nonexistent".into(),
        approval,
    });
    // Original pane unchanged
    assert_eq!(app.panes[0].status, PaneStatus::Working);
    assert!(app.panes[0].pending_approval.is_none());
}

#[test]
fn approval_resolved_on_wrong_session_ignored() {
    let mut app = make_app();
    add_session(&mut app, "s1");

    // Block the pane first
    let approval = make_approval("a1");
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s1".into(),
        approval,
    });
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);

    // Resolve for wrong session
    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "nonexistent".into(),
        approved: true,
    });
    // Original pane still blocked
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);
}
