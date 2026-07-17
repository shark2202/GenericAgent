//! E2E integration tests for ga-tui §8.1–§8.5
//!
//! Tests exercise the full App state machine: IPC messages → key events → render,
//! using ratatui::TestBackend for deterministic buffer inspection.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ga_tui::app::{App, InputMode};
use ga_tui::config::Config;
use ga_tui::event::{GoalSummary, IpcMessage};
use ga_tui::pane::{PaneStatus, RiskLevel, RunnerKind};
use ratatui::backend::TestBackend;
use ratatui::Terminal;

/// Helper: create a key press event
fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

/// Helper: create App with default config
fn make_app() -> App {
    App::new(Config::default())
}

// ═══════════════════════════════════════════════════════════════
// §8.1 — IPC Message Processing
// ═══════════════════════════════════════════════════════════════

#[test]
fn ipc_session_new_creates_pane() {
    let mut app = make_app();
    assert!(app.panes.is_empty());

    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].session_id, "s1");
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
    assert_eq!(app.panes[0].status, PaneStatus::Working);
}

#[test]
fn ipc_session_new_all_runner_kinds() {
    let mut app = make_app();
    for (runner_str, expected) in [
        ("ga", RunnerKind::Ga),
        ("codex", RunnerKind::Codex),
        ("claude", RunnerKind::Claude),
        ("subprocess", RunnerKind::Subprocess),
    ] {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s-{runner_str}"),
            runner: runner_str.into(),
        });
        let last = app.panes.last().unwrap();
        assert_eq!(last.runner, expected, "Failed for runner: {runner_str}");
    }
    assert_eq!(app.panes.len(), 4);
}

#[test]
fn ipc_session_new_custom_runner() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s-custom".into(),
        runner: "aider".into(),
    });
    assert_eq!(app.panes[0].runner, RunnerKind::Custom("aider".into()));
}

#[test]
fn ipc_session_output_appends_line() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });

    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "hello world".into(),
    });

    assert_eq!(app.panes[0].lines.len(), 1);
    assert_eq!(app.panes[0].lines[0].text, "hello world");
}

#[test]
fn ipc_session_output_ignores_unknown_session() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "nonexistent".into(),
        line: "orphan line".into(),
    });
    assert!(app.panes.is_empty());
}

#[test]
fn ipc_session_status_updates() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });

    for (status_str, expected) in [
        ("working", PaneStatus::Working),
        ("blocked", PaneStatus::Blocked),
        ("done", PaneStatus::Done),
        ("error", PaneStatus::Error),
        ("detached", PaneStatus::Detached),
    ] {
        app.handle_ipc(IpcMessage::SessionStatus {
            session_id: "s1".into(),
            status: status_str.into(),
        });
        assert_eq!(app.panes[0].status, expected, "Failed for status: {status_str}");
    }
}

#[test]
fn ipc_session_status_unknown_defaults_working() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "unknown_status".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);
}

#[test]
fn ipc_goal_update_stores_summary() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::GoalUpdate {
        goal_id: "g1".into(),
        state: "in_progress".into(),
    });
    assert!(app.goal_summary.is_some());
    assert!(app.goal_summary.as_ref().unwrap().contains("in_progress"));
}

#[test]
fn ipc_goal_list_result_stores_goals() {
    let mut app = make_app();
    let goals = vec![
        GoalSummary {
            id: "g1".into(),
            title: "Build TUI".into(),
            state: "done".into(),
            priority: 1,
        },
        GoalSummary {
            id: "g2".into(),
            title: "Write tests".into(),
            state: "in_progress".into(),
            priority: 2,
        },
    ];
    app.handle_ipc(IpcMessage::GoalListResult { goals });
    assert_eq!(app.goals.len(), 2);
    assert_eq!(app.goals[0].title, "Build TUI");
    assert_eq!(app.goals[1].state, "in_progress");
}

#[test]
fn ipc_goal_error_stores_message() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::GoalError {
        goal_id: Some("g1".into()),
        message: "deadline exceeded".into(),
    });
    assert_eq!(app.last_error.as_deref(), Some("deadline exceeded"));
}

#[test]
fn ipc_error_stores_message() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::Error {
        message: "connection lost".into(),
    });
    assert_eq!(app.last_error.as_deref(), Some("connection lost"));
}

// ═══════════════════════════════════════════════════════════════
// §8.2 — Multi-Pane Navigation
// ═══════════════════════════════════════════════════════════════

#[test]
fn multipane_switch_active() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "codex".into(),
    });
    assert_eq!(app.active_pane, 1); // last created is active

    // Switch to first pane
    app.handle_key(key(KeyCode::Left));
    assert_eq!(app.active_pane, 0);

    // Switch back
    app.handle_key(key(KeyCode::Right));
    assert_eq!(app.active_pane, 1);
}

#[test]
fn multipane_output_routed_to_correct_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "codex".into(),
    });

    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s0".into(),
        line: "from s0".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "from s1".into(),
    });

    assert_eq!(app.panes[0].lines.len(), 1);
    assert_eq!(app.panes[0].lines[0].text, "from s0");
    assert_eq!(app.panes[1].lines.len(), 1);
    assert_eq!(app.panes[1].lines[0].text, "from s1");
}

#[test]
fn multipane_three_panes_cycling() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    assert_eq!(app.panes.len(), 3);
    assert_eq!(app.active_pane, 2);

    // Left decrements: 2→1→0 (no wrap)
    app.handle_key(key(KeyCode::Left));
    assert_eq!(app.active_pane, 1);
    app.handle_key(key(KeyCode::Left));
    assert_eq!(app.active_pane, 0);
    app.handle_key(key(KeyCode::Left));
    assert_eq!(app.active_pane, 0); // clamps at 0
}

// ═══════════════════════════════════════════════════════════════
// §8.3 — Approval & Detach/Reattach Flow
// ═══════════════════════════════════════════════════════════════

#[test]
fn approval_flow_approve() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });

    let approval = ga_tui::pane::ApprovalRequest {
        id: "appr-1".into(),
        tool_name: "shell_exec".into(),
        risk_level: RiskLevel::Medium,
        description: "Run build script".into(),
        command_preview: "cargo build".into(),
        timestamp: chrono::Local::now(),
    };
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s0".into(),
        approval,
    });

    assert!(app.panes[0].pending_approval.is_some());
    assert_eq!(app.panes[0].status, PaneStatus::Blocked);

    // Approve
    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "s0".into(),
        approved: true,
    });
    assert!(app.panes[0].pending_approval.is_none());
    assert_eq!(app.panes[0].status, PaneStatus::Working);
}

#[test]
fn approval_flow_reject() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });

    let approval = ga_tui::pane::ApprovalRequest {
        id: "appr-2".into(),
        tool_name: "file_write".into(),
        risk_level: RiskLevel::High,
        description: "Write to system dir".into(),
        command_preview: "rm -rf /".into(),
        timestamp: chrono::Local::now(),
    };
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s0".into(),
        approval,
    });

    // Reject
    app.handle_ipc(IpcMessage::ApprovalResolved {
        session_id: "s0".into(),
        approved: false,
    });
    assert!(app.panes[0].pending_approval.is_none());
    assert_eq!(app.panes[0].status, PaneStatus::Done);
}

#[test]
fn detach_reattach_flow() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });

    // Detach via IPC
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s0".into(),
        status: "detached".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Detached);

    // Reattach via IPC
    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s0".into(),
        status: "working".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);
}

// ═══════════════════════════════════════════════════════════════
// §8.4 — Key Bindings
// ═══════════════════════════════════════════════════════════════

#[test]
fn key_i_enters_input_mode() {
    let mut app = make_app();
    assert_eq!(app.mode, InputMode::Normal);
    app.handle_key(key(KeyCode::Char('i')));
    assert_eq!(app.mode, InputMode::Input);
}

#[test]
fn key_colon_enters_prefix_mode() {
    let mut app = make_app();
    app.handle_key(key(KeyCode::Char(':')));
    assert_eq!(app.mode, InputMode::Prefix);
}

#[test]
fn key_escape_returns_to_normal() {
    let mut app = make_app();
    app.handle_key(key(KeyCode::Char('i')));
    assert_eq!(app.mode, InputMode::Input);
    app.handle_key(key(KeyCode::Esc));
    assert_eq!(app.mode, InputMode::Normal);
}

#[test]
fn key_question_toggles_help() {
    let mut app = make_app();
    assert!(!app.show_help);
    app.handle_key(key(KeyCode::Char('?')));
    assert!(app.show_help);
    app.handle_key(key(KeyCode::Char('?')));
    assert!(!app.show_help);
}

#[test]
fn key_t_cycles_theme() {
    let mut app = make_app();
    assert_eq!(app.theme.name, "catppuccin-mocha");
    app.handle_key(key(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "tokyo-night");
    app.handle_key(key(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "catppuccin-mocha"); // cycles back
}

#[test]
fn key_scroll_up_down() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });

    // Add enough lines to enable scrolling
    for i in 0..50 {
        app.handle_ipc(IpcMessage::SessionOutput {
            session_id: "s0".into(),
            line: format!("line {i}"),
        });
    }

    let initial_offset = app.panes[0].scroll_offset;
    app.handle_key(key(KeyCode::Up));
    assert!(app.panes[0].scroll_offset > initial_offset);

    app.handle_key(key(KeyCode::Down));
    assert!(app.panes[0].scroll_offset <= initial_offset + 3);
}

// ═══════════════════════════════════════════════════════════════
// §8.5 — Render Smoke Tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn render_empty_app_no_panic() {
    let app = make_app();
    let backend = TestBackend::new(80, 24);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| app.render(f)).unwrap();
}

#[test]
fn render_with_single_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s0".into(),
        line: "Hello from ga".into(),
    });

    let backend = TestBackend::new(80, 24);
    let mut terminal = Terminal::new(backend).unwrap();
    // Just verify render doesn't panic
    terminal.draw(|f| app.render(f)).unwrap();
}

#[test]
fn render_with_help_overlay() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_key(key(KeyCode::Char('?')));

    let backend = TestBackend::new(80, 24);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| app.render(f)).unwrap();
}

#[test]
fn render_multipane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "codex".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s0".into(),
        line: "ga output".into(),
    });
    app.handle_ipc(IpcMessage::SessionOutput {
        session_id: "s1".into(),
        line: "codex output".into(),
    });

    let backend = TestBackend::new(120, 30);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| app.render(f)).unwrap();
}

#[test]
fn render_approval_overlay() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    let approval = ga_tui::pane::ApprovalRequest {
        id: "appr-3".into(),
        tool_name: "shell_exec".into(),
        risk_level: RiskLevel::High,
        description: "Dangerous command".into(),
        command_preview: "sudo rm -rf /".into(),
        timestamp: chrono::Local::now(),
    };
    app.handle_ipc(IpcMessage::ApprovalNeeded {
        session_id: "s0".into(),
        approval,
    });

    let backend = TestBackend::new(80, 24);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| app.render(f)).unwrap();
}

#[test]
fn render_with_theme_switch() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_key(key(KeyCode::Char('t'))); // tokyo-night

    let backend = TestBackend::new(80, 24);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| app.render(f)).unwrap();
}
