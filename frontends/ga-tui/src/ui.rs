//! UI rendering for ga-tui multiplexer

use crate::app::{App, InputMode};
use crate::pane::{LineStyle, PaneStatus};
use crate::theme::Theme;
use crate::timeline;
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Tabs, Wrap},
    Frame,
};

/// Main render function
pub fn render(f: &mut Frame, app: &App) {
    let size = f.area();

    // If no panes, show welcome screen
    if app.panes.is_empty() {
        render_welcome(f, size, &app.theme);
        return;
    }

    // Layout: [tab_bar] [main_area] [status_bar]
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),       // tab bar
            Constraint::Min(5),          // main area
            Constraint::Length(1),       // status bar
        ])
        .split(size);

    render_tab_bar(f, chunks[0], app);
    render_main_area(f, chunks[1], app);
    render_status_bar(f, chunks[2], app);

    // Overlays
    if app.show_help {
        render_help_overlay(f, size, &app.theme);
    }

    // Approval popup for active pane
    if let Some(pane) = app.panes.get(app.active_pane) {
        if pane.pending_approval.is_some() {
            crate::approval::render_approval_popup(
                f,
                centered_rect(size, 60, 40),
                pane.pending_approval.as_ref().unwrap(),
                &app.theme,
            );
        }
    }
}

fn render_welcome(f: &mut Frame, area: Rect, theme: &Theme) {
    let lines = vec![
        Line::from(""),
        Line::from(Span::styled(
            "  ga-tui multiplexer",
            Style::default().fg(theme.accent).add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from(Span::styled(
            "  No sessions running. Press 'n' or Ctrl+B n to start one.",
            Style::default().fg(theme.text_dim),
        )),
        Line::from(Span::styled(
            "  Press '?' for help.",
            Style::default().fg(theme.text_dim),
        )),
    ];
    let paragraph = Paragraph::new(lines).wrap(Wrap { trim: false });
    f.render_widget(paragraph, area);
}

fn render_tab_bar(f: &mut Frame, area: Rect, app: &App) {
    let titles: Vec<Line> = app
        .panes
        .iter()
        .enumerate()
        .map(|(i, pane)| {
            let status_icon = match pane.status {
                PaneStatus::Working => "●",
                PaneStatus::Blocked => "■",
                PaneStatus::Done => "✓",
                PaneStatus::Error => "✗",
                PaneStatus::Detached => "○",
            };
            let status_color = match pane.status {
                PaneStatus::Working => app.theme.status_working,
                PaneStatus::Blocked => app.theme.status_blocked,
                PaneStatus::Done => app.theme.status_done,
                PaneStatus::Error => app.theme.status_error,
                PaneStatus::Detached => app.theme.text_dim,
            };
            let is_active = i == app.active_pane;
            let style = if is_active {
                Style::default().fg(app.theme.tab_active).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(app.theme.tab_inactive)
            };
            Line::from(vec![
                Span::styled(status_icon, Style::default().fg(status_color)),
                Span::styled(
                    format!(" {}:{} ", pane.runner, pane.session_id),
                    style,
                ),
            ])
        })
        .collect();

    let tabs = Tabs::new(titles)
        .style(Style::default().bg(app.theme.tab_bg))
        .highlight_style(
            Style::default()
                .fg(app.theme.tab_active)
                .add_modifier(Modifier::BOLD),
        )
        .select(app.active_pane);

    f.render_widget(tabs, area);
}

fn render_main_area(f: &mut Frame, area: Rect, app: &App) {
    if app.panes.is_empty() {
        return;
    }

    // Determine pane layout based on count
    let pane_count = app.panes.len();
    if pane_count == 1 {
        render_pane(f, area, app, 0);
    } else if pane_count == 2 {
        let chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
            .split(area);
        render_pane(f, chunks[0], app, 0);
        render_pane(f, chunks[1], app, 1);
    } else if pane_count <= 4 {
        // 2x2 grid
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
            .split(area);
        let top = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
            .split(rows[0]);
        let bottom = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
            .split(rows[1]);

        render_pane(f, top[0], app, 0);
        if pane_count > 1 { render_pane(f, top[1], app, 1); }
        if pane_count > 2 { render_pane(f, bottom[0], app, 2); }
        if pane_count > 3 { render_pane(f, bottom[1], app, 3); }
    } else {
        // For >4 panes, only show active pane (full screen)
        render_pane(f, area, app, app.active_pane);
    }
}

fn render_pane(f: &mut Frame, area: Rect, app: &App, pane_idx: usize) {
    let pane = match app.panes.get(pane_idx) {
        Some(p) => p,
        None => return,
    };

    let is_active = pane_idx == app.active_pane;
    let border_color = if is_active { app.theme.border_active } else { app.theme.border };

    // Layout: [content | timeline_sidebar]
    let show_timeline = !pane.tool_calls.is_empty() && area.width > 40;
    let content_area = if show_timeline {
        let chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Min(20), Constraint::Length(16)])
            .split(area);
        // Render timeline sidebar
        timeline::render_timeline(f, chunks[1], &pane.tool_calls, &app.theme);
        chunks[0]
    } else {
        area
    };

    // Pane border with title
    let title = format!(" {}:{} ", pane.runner, pane.session_id);
    let status_str = match pane.status {
        PaneStatus::Working => " WORKING ",
        PaneStatus::Blocked => " BLOCKED ",
        PaneStatus::Done => " DONE ",
        PaneStatus::Error => " ERROR ",
        PaneStatus::Detached => " DETACHED ",
    };
    let status_color = match pane.status {
        PaneStatus::Working => app.theme.status_working,
        PaneStatus::Blocked => app.theme.status_blocked,
        PaneStatus::Done => app.theme.status_done,
        PaneStatus::Error => app.theme.status_error,
        PaneStatus::Detached => app.theme.text_dim,
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(border_color))
        .title(Span::styled(&title, Style::default().fg(app.theme.fg).add_modifier(Modifier::BOLD)))
        .title_bottom(Span::styled(status_str, Style::default().fg(status_color)));

    let inner = block.inner(content_area);
    f.render_widget(block, content_area);

    // Render scrollback content
    render_pane_content(f, inner, app, pane_idx);
}

fn render_pane_content(f: &mut Frame, area: Rect, app: &App, pane_idx: usize) {
    let pane = match app.panes.get(pane_idx) {
        Some(p) => p,
        None => return,
    };

    // Split: [scrollback] [input_line]
    let show_input = pane_idx == app.active_pane && app.mode == InputMode::Input;
    let (content_area, input_area) = if show_input && area.height > 2 {
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Min(1), Constraint::Length(1)])
            .split(area);
        (chunks[0], Some(chunks[1]))
    } else {
        (area, None)
    };

    // Render scrollback lines
    let visible_height = content_area.height as usize;
    let total_lines = pane.lines.len();

    let start_idx = if total_lines > visible_height {
        let scroll_base = total_lines - visible_height;
        scroll_base.saturating_sub(pane.scroll_offset)
    } else {
        0
    };

    let styled_lines: Vec<Line> = pane
        .lines
        .iter()
        .skip(start_idx)
        .take(visible_height)
        .map(|line| {
            let style = match line.style {
                LineStyle::Normal => Style::default().fg(app.theme.fg),
                LineStyle::System => Style::default().fg(app.theme.accent),
                LineStyle::ToolCall => Style::default().fg(app.theme.tool_call),
                LineStyle::ToolResult => Style::default().fg(app.theme.tool_result),
                LineStyle::Error => Style::default().fg(app.theme.status_error),
                LineStyle::Approval => Style::default().fg(app.theme.status_blocked),
                LineStyle::Success => Style::default().fg(app.theme.status_done),
            };
            Line::from(Span::styled(&line.text, style))
        })
        .collect();

    let paragraph = Paragraph::new(styled_lines).wrap(Wrap { trim: false });
    f.render_widget(paragraph, content_area);

    // Render input line
    if let Some(input_area) = input_area {
        let input_line = Paragraph::new(Line::from(vec![
            Span::styled("> ", Style::default().fg(app.theme.accent).add_modifier(Modifier::BOLD)),
            Span::styled(&app.input_buffer, Style::default().fg(app.theme.fg)),
            Span::styled("▎", Style::default().fg(app.theme.accent)),
        ]));
        f.render_widget(input_line, input_area);
    }
}

fn render_status_bar(f: &mut Frame, area: Rect, app: &App) {
    let mode_str = match app.mode {
        InputMode::Normal => "NORMAL",
        InputMode::Prefix => "PREFIX",
        InputMode::Input => "INPUT",
    };
    let mode_color = match app.mode {
        InputMode::Normal => app.theme.status_working,
        InputMode::Prefix => app.theme.status_blocked,
        InputMode::Input => app.theme.accent,
    };

    let pane_count = app.panes.len();
    let active = app.active_pane;

    let mut spans = vec![
        Span::styled(
            format!(" {} ", mode_str),
            Style::default().fg(mode_color).add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!(" Panes:{} ", pane_count),
            Style::default().fg(app.theme.text_dim),
        ),
        Span::styled(
            format!(" Active:{} ", active),
            Style::default().fg(app.theme.text_dim),
        ),
        Span::styled(
            format!(" Theme:{} ", app.theme.name),
            Style::default().fg(app.theme.text_dim),
        ),
    ];

    if let Some(ref err) = app.last_error {
        spans.push(Span::styled(
            format!(" ERR:{} ", err),
            Style::default().fg(app.theme.status_error),
        ));
    }

    let line = Line::from(spans);
    let paragraph = Paragraph::new(line).style(Style::default().bg(app.theme.tab_bg));
    f.render_widget(paragraph, area);
}

fn render_help_overlay(f: &mut Frame, area: Rect, theme: &Theme) {
    let help_area = centered_rect(area, 70, 60);
    f.render_widget(Clear, help_area);

    let lines = vec![
        Line::from(Span::styled(
            "  ga-tui HELP",
            Style::default().fg(theme.accent).add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from(vec![
            Span::styled("  Tab/Shift+Tab   ", Style::default().fg(theme.accent)),
            Span::raw("Next/prev pane"),
        ]),
        Line::from(vec![
            Span::styled("  Alt+1-9         ", Style::default().fg(theme.accent)),
            Span::raw("Switch to pane N"),
        ]),
        Line::from(vec![
            Span::styled("  n               ", Style::default().fg(theme.accent)),
            Span::raw("New ga session"),
        ]),
        Line::from(vec![
            Span::styled("  i               ", Style::default().fg(theme.accent)),
            Span::raw("Enter input mode"),
        ]),
        Line::from(vec![
            Span::styled("  Esc             ", Style::default().fg(theme.accent)),
            Span::raw("Back to normal / Quit"),
        ]),
        Line::from(vec![
            Span::styled("  j/k or ↑/↓      ", Style::default().fg(theme.accent)),
            Span::raw("Scroll"),
        ]),
        Line::from(vec![
            Span::styled("  T               ", Style::default().fg(theme.accent)),
            Span::raw("Cycle theme"),
        ]),
        Line::from(""),
        Line::from(Span::styled("  PREFIX MODE (Ctrl+B then:)", Style::default().fg(theme.accent).add_modifier(Modifier::BOLD))),
        Line::from(vec![
            Span::styled("  n               ", Style::default().fg(theme.accent)),
            Span::raw("New session"),
        ]),
        Line::from(vec![
            Span::styled("  c               ", Style::default().fg(theme.accent)),
            Span::raw("New codex session"),
        ]),
        Line::from(vec![
            Span::styled("  d               ", Style::default().fg(theme.accent)),
            Span::raw("Detach pane"),
        ]),
        Line::from(vec![
            Span::styled("  a               ", Style::default().fg(theme.accent)),
            Span::raw("Reattach pane"),
        ]),
        Line::from(vec![
            Span::styled("  x               ", Style::default().fg(theme.accent)),
            Span::raw("Close pane"),
        ]),
        Line::from(""),
        Line::from(Span::styled("  Press any key to close", Style::default().fg(theme.text_dim))),
    ];

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(theme.accent))
        .title(" Help ");

    let paragraph = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });

    f.render_widget(paragraph, help_area);
}

/// Helper: centered rect for overlays
fn centered_rect(r: Rect, percent_x: u16, percent_y: u16) -> Rect {
    let popup_x = r.width * percent_x / 100;
    let popup_y = r.height * percent_y / 100;
    Rect::new(
        r.x + (r.width.saturating_sub(popup_x)) / 2,
        r.y + (r.height.saturating_sub(popup_y)) / 2,
        popup_x.max(20),
        popup_y.max(10),
    )
}
