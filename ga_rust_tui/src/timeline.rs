//! Tool timeline rendering (herdr-style progress bars per tool call)

use crate::pane::{ToolCall, ToolCallStatus};
use crate::theme::Theme;
use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph, Wrap},
    Frame,
};

/// Render a tool timeline sidebar for a pane
pub fn render_timeline(f: &mut Frame, area: Rect, tool_calls: &[ToolCall], theme: &Theme) {
    let block = Block::default()
        .borders(Borders::RIGHT)
        .border_style(Style::default().fg(theme.border))
        .title(" Tools ");

    let inner = block.inner(area);
    f.render_widget(block, area);

    if tool_calls.is_empty() {
        let hint = Paragraph::new("No tool calls")
            .style(Style::default().fg(theme.text_dim));
        f.render_widget(hint, inner);
        return;
    }

    // Show most recent N tool calls that fit
    let max_lines = inner.height as usize;
    let start = tool_calls.len().saturating_sub(max_lines);
    let visible = &tool_calls[start..];

    let lines: Vec<Line> = visible
        .iter()
        .map(|tc| {
            let (icon, color) = match tc.status {
                ToolCallStatus::Pending => ("○", theme.timeline_pending),
                ToolCallStatus::Running => ("◑", theme.timeline_running),
                ToolCallStatus::Approved => ("✓", theme.timeline_done),
                ToolCallStatus::Rejected => ("✗", theme.timeline_failed),
                ToolCallStatus::Completed => ("●", theme.timeline_done),
                ToolCallStatus::Done => ("●", theme.timeline_done),
                ToolCallStatus::Failed => ("✗", theme.timeline_failed),
            };
            let elapsed = tc.finished_at
                .map(|end| {
                    let d = end - tc.started_at;
                    format!("{:.0}s", d.num_seconds())
                })
                .unwrap_or_else(|| {
                    let d = chrono::Local::now() - tc.started_at;
                    format!("{:.0}s", d.num_seconds())
                });

            Line::from(vec![
                Span::styled(format!("{icon} "), Style::default().fg(color)),
                Span::styled(
                    tc.name.chars().take(12).collect::<String>(),
                    Style::default().fg(color).add_modifier(Modifier::BOLD),
                ),
                Span::styled(format!(" {elapsed}"), Style::default().fg(theme.text_dim)),
            ])
        })
        .collect();

    let paragraph = Paragraph::new(lines).wrap(Wrap { trim: true });
    f.render_widget(paragraph, inner);
}
