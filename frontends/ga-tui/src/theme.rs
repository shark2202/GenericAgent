//! Theme system for ga-tui (herdr + superfile style)

use ratatui::style::Color;

/// Complete theme definition matching ui.rs expectations
#[derive(Debug, Clone)]
pub struct Theme {
    pub name: String,
    // Background / foreground
    pub bg: Color,
    pub fg: Color,
    // Pane borders
    pub border: Color,
    pub border_active: Color,
    // Tab bar background
    pub tab_bg: Color,
    pub tab_active: Color,
    pub tab_inactive: Color,
    // Status colors
    pub status_working: Color,
    pub status_blocked: Color,
    pub status_done: Color,
    pub status_error: Color,
    // Text style colors
    pub text_normal: Color,
    pub text_dim: Color,
    pub text_system: Color,
    pub text_highlight: Color,
    // Accent color (used for borders, mode labels, help keys)
    pub accent: Color,
    // Tool call/result
    pub tool_call: Color,
    pub tool_result: Color,
    // Approval colors
    pub approval_approve: Color,
    pub approval_reject: Color,
    pub approval_border: Color,
    // Timeline colors
    pub timeline_pending: Color,
    pub timeline_running: Color,
    pub timeline_done: Color,
    pub timeline_failed: Color,
}

impl Default for Theme {
    fn default() -> Self {
        Self::catppuccin_mocha()
    }
}

impl Theme {
    pub fn catppuccin_mocha() -> Self {
        Self {
            name: "catppuccin-mocha".into(),
            bg: Color::Rgb(30, 30, 46),        // Base
            fg: Color::Rgb(205, 214, 244),      // Text
            border: Color::Rgb(88, 91, 112),    // Surface2
            border_active: Color::Rgb(137, 180, 250), // Blue
            tab_bg: Color::Rgb(24, 24, 37),     // Mantle
            tab_active: Color::Rgb(137, 180, 250), // Blue
            tab_inactive: Color::Rgb(88, 91, 112), // Surface2
            status_working: Color::Rgb(137, 180, 250), // Blue
            status_blocked: Color::Rgb(243, 139, 168), // Pink
            status_done: Color::Rgb(166, 227, 161),    // Green
            status_error: Color::Rgb(243, 139, 168),   // Pink
            text_normal: Color::Rgb(205, 214, 244),    // Text
            text_dim: Color::Rgb(147, 153, 178),       // Subtext0
            text_system: Color::Rgb(180, 190, 254),    // Lavender
            text_highlight: Color::Rgb(249, 226, 175), // Yellow
            accent: Color::Rgb(137, 180, 250),         // Blue
            tool_call: Color::Rgb(245, 224, 220),      // Rosewater
            tool_result: Color::Rgb(166, 227, 161),    // Green
            approval_approve: Color::Rgb(166, 227, 161), // Green
            approval_reject: Color::Rgb(243, 139, 168), // Pink
            approval_border: Color::Rgb(180, 190, 254), // Lavender
            timeline_pending: Color::Rgb(147, 153, 178), // Subtext0
            timeline_running: Color::Rgb(137, 180, 250), // Blue
            timeline_done: Color::Rgb(166, 227, 161),    // Green
            timeline_failed: Color::Rgb(243, 139, 168),  // Pink
        }
    }

    pub fn tokyo_night() -> Self {
        Self {
            name: "tokyo-night".into(),
            bg: Color::Rgb(26, 27, 38),
            fg: Color::Rgb(192, 202, 245),
            border: Color::Rgb(59, 66, 88),
            border_active: Color::Rgb(122, 162, 247),
            tab_bg: Color::Rgb(22, 22, 34),
            tab_active: Color::Rgb(122, 162, 247),
            tab_inactive: Color::Rgb(59, 66, 88),
            status_working: Color::Rgb(122, 162, 247),
            status_blocked: Color::Rgb(255, 158, 100),
            status_done: Color::Rgb(158, 206, 106),
            status_error: Color::Rgb(255, 85, 85),
            text_normal: Color::Rgb(192, 202, 245),
            text_dim: Color::Rgb(108, 112, 134),
            text_system: Color::Rgb(187, 154, 247),
            text_highlight: Color::Rgb(224, 175, 208),
            accent: Color::Rgb(122, 162, 247),
            tool_call: Color::Rgb(224, 175, 208),
            tool_result: Color::Rgb(158, 206, 106),
            approval_approve: Color::Rgb(158, 206, 106),
            approval_reject: Color::Rgb(255, 85, 85),
            approval_border: Color::Rgb(187, 154, 247),
            timeline_pending: Color::Rgb(108, 112, 134),
            timeline_running: Color::Rgb(122, 162, 247),
            timeline_done: Color::Rgb(158, 206, 106),
            timeline_failed: Color::Rgb(255, 85, 85),
        }
    }

    pub fn by_name(name: &str) -> Self {
        match name {
            "catppuccin-mocha" => Self::catppuccin_mocha(),
            "tokyo-night" => Self::tokyo_night(),
            _ => Self::default(),
        }
    }

    /// Cycle to next theme
    pub fn cycle(&self) -> Self {
        match self.name.as_str() {
            "catppuccin-mocha" => Self::tokyo_night(),
            _ => Self::catppuccin_mocha(),
        }
    }
}
