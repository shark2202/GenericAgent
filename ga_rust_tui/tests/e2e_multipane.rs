//! E2E integration tests: multi-pane creation, switching, focus, and management.
//!
//! Covers spec §1 (Multi-Pane Runner View), §2 (Pane Status Display),
//! §3 (Agent-Agnostic Pane Rendering).

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ga_tui::app::App;
use ga_tui::config::Config;
use ga_tui::event::IpcMessage;
use ga_tui::pane::{PaneStatus, RunnerKind};

fn make_app() -> App {
    App::new(Config::default())
}

fn press(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

// ── Pane creation ──

#[test]
fn new_session_creates_ga_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
    assert_eq!(app.panes[0].session_id, "s1");
}

#[test]
fn new_session_creates_codex_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "c1".into(),
        runner: "codex".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Codex);
}

#[test]
fn new_session_creates_subprocess_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "sub1".into(),
        runner: "subprocess".into(),
    });
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
}

#[test]
fn multiple_sessions_create_multiple_panes() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    assert_eq!(app.panes.len(), 3);
}

#[test]
fn new_session_sets_active_pane_to_latest() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.active_pane, 0);
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.active_pane, 1);
}

// ── Pane switching via Tab ──

#[test]
fn tab_cycles_through_panes() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    // Active is last (2)
    assert_eq!(app.active_pane, 2);
    app.handle_key(press(KeyCode::Tab));
    assert_eq!(app.active_pane, 0);
    app.handle_key(press(KeyCode::Tab));
    assert_eq!(app.active_pane, 1);
}

// ── Pane switching via number keys ──

#[test]
fn number_key_selects_pane_by_index() {
    let mut app = make_app();
    for i in 0..5 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    app.handle_key(press(KeyCode::Char('1')));
    assert_eq!(app.active_pane, 0);
    app.handle_key(press(KeyCode::Char('3')));
    assert_eq!(app.active_pane, 2);
    app.handle_key(press(KeyCode::Char('5')));
    assert_eq!(app.active_pane, 4);
}

#[test]
fn number_key_ignores_out_of_range() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.active_pane = 0;
    // '9' is out of range for 1 pane
    app.handle_key(press(KeyCode::Char('9')));
    assert_eq!(app.active_pane, 0);
}

// ── Pane switching via arrow keys ──

#[test]
fn left_arrow_switches_to_previous_pane() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    app.active_pane = 2;
    app.handle_key(press(KeyCode::Left));
    assert_eq!(app.active_pane, 1);
}

#[test]
fn right_arrow_switches_to_next_pane() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    app.active_pane = 0;
    app.handle_key(press(KeyCode::Right));
    assert_eq!(app.active_pane, 1);
}

// ── Pane creation via `n` key ──

#[test]
fn n_key_creates_new_ga_pane() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char('n')));
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
}

#[test]
fn n_key_creates_multiple_panes() {
    let mut app = make_app();
    for _ in 0..3 {
        app.handle_key(press(KeyCode::Char('n')));
    }
    assert_eq!(app.panes.len(), 3);
    assert_eq!(app.active_pane, 2);
}

// ── Pane status via IPC ──

#[test]
fn ipc_session_status_updates_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Working);

    app.handle_ipc(IpcMessage::SessionStatus {
        session_id: "s1".into(),
        status: "done".into(),
    });
    assert_eq!(app.panes[0].status, PaneStatus::Done);
}

#[test]
fn ipc_session_status_error() {
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

// ── Agent-agnostic rendering ──

#[test]
fn different_runners_coexist() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "ga1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "codex1".into(),
        runner: "codex".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "sub1".into(),
        runner: "subprocess".into(),
    });
    assert_eq!(app.panes.len(), 3);
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
    assert_eq!(app.panes[1].runner, RunnerKind::Codex);
    assert_eq!(app.panes[2].runner, RunnerKind::Subprocess);
}

// ── Close pane via `c` key ──

#[test]
fn c_key_closes_active_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s0".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes.len(), 2);
    app.handle_key(press(KeyCode::Char('c')));
    assert_eq!(app.panes.len(), 1);
}

// ── Quit via `q` key ──

#[test]
fn q_key_sets_quit_flag() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('q')));
    assert!(app.should_quit);
}
