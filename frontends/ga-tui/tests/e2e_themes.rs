//! E2E integration tests: theme system in ga-tui.
//!
//! Covers spec §7 (Themes): two built-in themes (catppuccin-mocha, tokyo-night),
//! theme cycling via `t` key, Theme::by_name lookup, config-based selection.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ga_tui::app::App;
use ga_tui::config::Config;
use ga_tui::theme::Theme;

fn make_app() -> App {
    App::new(Config::default())
}

fn press(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

// ── Built-in themes ──

#[test]
fn default_theme_is_catppuccin_mocha() {
    let app = make_app();
    assert_eq!(app.theme.name, "catppuccin-mocha");
}

#[test]
fn catppuccin_mocha_has_correct_name() {
    let theme = Theme::catppuccin_mocha();
    assert_eq!(theme.name, "catppuccin-mocha");
}

#[test]
fn tokyo_night_has_correct_name() {
    let theme = Theme::tokyo_night();
    assert_eq!(theme.name, "tokyo-night");
}

#[test]
fn themes_have_distinct_bg_colors() {
    let mocha = Theme::catppuccin_mocha();
    let tokyo = Theme::tokyo_night();
    assert_ne!(mocha.bg, tokyo.bg);
}

#[test]
fn themes_have_distinct_fg_colors() {
    let mocha = Theme::catppuccin_mocha();
    let tokyo = Theme::tokyo_night();
    assert_ne!(mocha.fg, tokyo.fg);
}

#[test]
fn themes_have_distinct_border_active_colors() {
    let mocha = Theme::catppuccin_mocha();
    let tokyo = Theme::tokyo_night();
    assert_ne!(mocha.border_active, tokyo.border_active);
}

// ── Theme::by_name ──

#[test]
fn by_name_returns_catppuccin_mocha() {
    let theme = Theme::by_name("catppuccin-mocha");
    assert_eq!(theme.name, "catppuccin-mocha");
}

#[test]
fn by_name_returns_tokyo_night() {
    let theme = Theme::by_name("tokyo-night");
    assert_eq!(theme.name, "tokyo-night");
}

#[test]
fn by_name_unknown_falls_back_to_default() {
    let theme = Theme::by_name("nonexistent");
    assert_eq!(theme.name, "catppuccin-mocha");
}

#[test]
fn by_name_returns_valid_theme_for_any_input() {
    let theme = Theme::by_name("anything");
    assert!(!theme.name.is_empty());
}

// ── Theme::cycle ──

#[test]
fn cycle_from_catppuccin_mocha_to_tokyo_night() {
    let theme = Theme::catppuccin_mocha();
    let next = theme.cycle();
    assert_eq!(next.name, "tokyo-night");
}

#[test]
fn cycle_from_tokyo_night_to_catppuccin_mocha() {
    let theme = Theme::tokyo_night();
    let next = theme.cycle();
    assert_eq!(next.name, "catppuccin-mocha");
}

#[test]
fn cycle_is_reversible() {
    let start = Theme::catppuccin_mocha();
    let after_one = start.cycle();
    let after_two = after_one.cycle();
    assert_eq!(after_two.name, start.name);
}

#[test]
fn cycle_two_times_returns_to_start() {
    let start = Theme::catppuccin_mocha();
    let mut current = start.clone();
    for _ in 0..2 {
        current = current.cycle();
    }
    assert_eq!(current.name, start.name);
}

// ── Theme cycling via `t` key ──

#[test]
fn t_key_cycles_theme() {
    let mut app = make_app();
    assert_eq!(app.theme.name, "catppuccin-mocha");
    app.handle_key(press(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "tokyo-night");
}

#[test]
fn t_key_cycles_theme_twice_back_to_start() {
    let mut app = make_app();
    app.handle_key(press(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "tokyo-night");
    app.handle_key(press(KeyCode::Char('t')));
    assert_eq!(app.theme.name, "catppuccin-mocha");
}

// ── Config-based theme selection ──

#[test]
fn config_default_theme_is_catppuccin_mocha() {
    let app = App::new(Config::default());
    assert_eq!(app.theme.name, "catppuccin-mocha");
}

#[test]
fn config_with_tokyo_night_theme() {
    let mut config = Config::default();
    config.theme = "tokyo-night".into();
    let app = App::new(config);
    assert_eq!(app.config.theme, "tokyo-night");
}

// ── Theme color sanity ──

#[test]
fn catppuccin_mocha_status_colors_are_distinct() {
    let theme = Theme::catppuccin_mocha();
    assert_ne!(theme.status_working, theme.status_blocked);
    assert_ne!(theme.status_done, theme.status_error);
}

#[test]
fn tokyo_night_status_colors_are_distinct() {
    let theme = Theme::tokyo_night();
    assert_ne!(theme.status_working, theme.status_blocked);
    assert_ne!(theme.status_done, theme.status_error);
}
