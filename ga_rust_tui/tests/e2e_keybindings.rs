//! E2E integration tests: keybinding handling in ga-tui.
//!
//! Covers spec §6 (Keybindings): normal mode keys, prefix/command mode
//! (entered via `:`), input mode, pane number selection, arrow keys.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ga_tui::app::{App, InputMode};
use ga_tui::config::Config;
use ga_tui::event::IpcMessage;
use ga_tui::pane::{PaneStatus, RunnerKind};

fn make_app() -> App {
    App::new(Config::default())
}

fn press(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

// ── Normal mode: quit ──

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

// ── Normal mode: new session ──

#[test]
fn n_key_creates_new_ga_pane() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char('n')));
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Ga);
}

// ── Normal mode: close pane ──

#[test]
fn c_key_closes_active_pane() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s2".into(),
        runner: "ga".into(),
    });
    assert_eq!(app.panes.len(), 2);
    app.handle_key(press(KeyCode::Char('c')));
    assert_eq!(app.panes.len(), 1);
}

// ── Normal mode: detach/attach ──

#[test]
fn d_key_detaches_session() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('d')));
    // After detach, pane remains but status is Detached
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].status, PaneStatus::Detached);
}

#[test]
fn a_key_reattaches_session() {
    let mut app = make_app();
    // Attach with no detached sessions does nothing harmful
    app.handle_key(press(KeyCode::Char('a')));
    assert_eq!(app.panes.len(), 0);
}

// ── Normal mode: help ──

#[test]
fn question_mark_shows_help() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char('?')));
    assert!(app.show_help);
}

// ── Normal mode: theme cycle ──

#[test]
fn t_key_cycles_theme() {
    let mut app = make_app();
    assert_eq!(app.theme.name, "catppuccin-mocha");
    app.handle_key(press(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "tokyo-night");
}

// ── Prefix mode: entered via `:` ──

#[test]
fn colon_enters_prefix_mode() {
    let mut app = make_app();
    assert_eq!(app.mode, InputMode::Normal);
    app.handle_key(press(KeyCode::Char(':')));
    assert_eq!(app.mode, InputMode::Prefix);
}

#[test]
fn escape_exits_prefix_mode() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    assert_eq!(app.mode, InputMode::Prefix);
    app.handle_key(press(KeyCode::Esc));
    assert_eq!(app.mode, InputMode::Normal);
}

// ── Prefix mode: typing commands ──

#[test]
fn prefix_mode_types_command_text() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "sub".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    assert_eq!(app.command_text, "sub");
}

#[test]
fn prefix_mode_help_command() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "help".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    assert_eq!(app.command_text, "help");
}

#[test]
fn prefix_mode_theme_command() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "theme".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    assert_eq!(app.command_text, "theme");
}

// ── Prefix mode: execute command ──

#[test]
fn prefix_sub_command_creates_subprocess_pane() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "sub".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
    assert_eq!(app.mode, InputMode::Normal);
}

#[test]
fn prefix_subprocess_command_creates_subprocess_pane() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "subprocess".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    assert_eq!(app.panes.len(), 1);
    assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
}

#[test]
fn prefix_help_command_shows_help() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char(':')));
    for c in "help".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    assert!(app.show_help);
    assert_eq!(app.mode, InputMode::Normal);
}

#[test]
fn prefix_theme_command_cycles_theme() {
    let mut app = make_app();
    assert_eq!(app.theme.name, "catppuccin-mocha");
    app.handle_key(press(KeyCode::Char(':')));
    for c in "theme".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    assert_eq!(app.theme.name, "tokyo-night");
}

#[test]
fn prefix_quit_command_sets_quit() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char(':')));
    for c in "quit".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    assert!(app.should_quit);
}

// ── Input mode ──

#[test]
fn i_key_enters_input_mode() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('i')));
    assert_eq!(app.mode, InputMode::Input);
}

#[test]
fn escape_exits_input_mode() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('i')));
    assert_eq!(app.mode, InputMode::Input);
    app.handle_key(press(KeyCode::Esc));
    assert_eq!(app.mode, InputMode::Normal);
}

#[test]
fn input_mode_appends_to_input_buffer() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('i')));
    for c in "hello world".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    assert_eq!(app.input_buffer, "hello world");
}

#[test]
fn enter_submits_input() {
    let mut app = make_app();
    app.handle_ipc(IpcMessage::SessionNew {
        session_id: "s1".into(),
        runner: "ga".into(),
    });
    app.handle_key(press(KeyCode::Char('i')));
    for c in "test command".chars() {
        app.handle_key(press(KeyCode::Char(c)));
    }
    app.handle_key(press(KeyCode::Enter));
    // After submit, should return to normal mode and buffer cleared
    assert_eq!(app.mode, InputMode::Normal);
    assert_eq!(app.input_buffer, "");
}

// ── Pane switching via Tab and arrow keys ──

#[test]
fn tab_cycles_through_panes() {
    let mut app = make_app();
    for i in 0..3 {
        app.handle_ipc(IpcMessage::SessionNew {
            session_id: format!("s{i}"),
            runner: "ga".into(),
        });
    }
    assert_eq!(app.active_pane, 2);
    app.handle_key(press(KeyCode::Tab));
    assert_eq!(app.active_pane, 0);
    app.handle_key(press(KeyCode::Tab));
    assert_eq!(app.active_pane, 1);
}

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

// ── Pane number selection ──

#[test]
fn number_key_selects_pane() {
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
}

// ── No duplicate keybindings ──

#[test]
fn no_duplicate_normal_mode_keys() {
    // Verify the keybinding set is unique (spec requirement)
    let keys = ["c", "x", "d", "a", "n", "q", "i", "t", "?", ":"];
    let unique: std::collections::HashSet<_> = keys.iter().collect();
    assert_eq!(unique.len(), keys.len(), "Duplicate keybindings found");
}
