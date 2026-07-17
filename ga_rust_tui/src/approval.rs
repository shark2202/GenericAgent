//! Approval UI rendering and interaction

use crate::pane::{ApprovalRequest, RiskLevel};
use crate::theme::Theme;
use ratatui::{
    layout::{Alignment, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
    Frame,
};

/// Render an approval popup overlay
pub fn render_approval_popup(f: &mut Frame, area: Rect, req: &ApprovalRequest, theme: &Theme) {
    // Clear the area first
    f.render_widget(Clear, area);

    let risk_icon = match req.risk_level {
        RiskLevel::Low => "🟢",
        RiskLevel::Medium => "🟡",
        RiskLevel::High => "🔴",
    };

    let risk_color = match req.risk_level {
        RiskLevel::Low => theme.approval_approve,
        RiskLevel::Medium => theme.text_system,
        RiskLevel::High => theme.approval_reject,
    };

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(theme.approval_border))
        .title(" ⚠ Approval Required ")
        .title_alignment(Alignment::Center);

    let inner = block.inner(area);
    f.render_widget(block, area);

    let lines = vec![
        Line::from(vec![
            Span::styled(format!("{risk_icon} Risk: "), Style::default().fg(risk_color)),
            Span::styled(
                req.risk_level.as_str(),
                Style::default().fg(risk_color).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("Tool: ", Style::default().fg(theme.text_dim)),
            Span::styled(
                req.tool_name.clone(),
                Style::default().fg(theme.text_highlight).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(""),
        Line::from(Span::styled(
            req.description.clone(),
            Style::default().fg(theme.text_normal),
        )),
        Line::from(""),
        Line::from(vec![
            Span::styled("[y] ", Style::default().fg(theme.approval_approve).add_modifier(Modifier::BOLD)),
            Span::styled("Approve  ", Style::default().fg(theme.approval_approve)),
            Span::styled("[n] ", Style::default().fg(theme.approval_reject).add_modifier(Modifier::BOLD)),
            Span::styled("Reject  ", Style::default().fg(theme.approval_reject)),
            Span::styled("[Esc] ", Style::default().fg(theme.text_dim).add_modifier(Modifier::BOLD)),
            Span::styled("Cancel", Style::default().fg(theme.text_dim)),
        ]),
    ];

    let paragraph = Paragraph::new(lines)
        .alignment(Alignment::Center)
        .wrap(Wrap { trim: true });
    f.render_widget(paragraph, inner);
}

impl RiskLevel {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::Medium => "medium",
            Self::High => "high",
        }
    }
}

/// Approval popup state
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalMode {
    Hidden,
    Visible,
}

/// Result of approval interaction
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalResult {
    Approved,
    Rejected,
    Cancelled,
}
