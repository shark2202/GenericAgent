//! Per-runner pane model

use chrono::{DateTime, Local};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

/// Runner kind (agent-agnostic)
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RunnerKind {
    Ga,
    Codex,
    Claude,
    Subprocess,
    Custom(String),
}

impl std::fmt::Display for RunnerKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunnerKind::Ga => write!(f, "ga"),
            RunnerKind::Codex => write!(f, "codex"),
            RunnerKind::Claude => write!(f, "claude"),
            RunnerKind::Subprocess => write!(f, "subprocess"),
            RunnerKind::Custom(s) => write!(f, "{}", s),
        }
    }
}

/// Pane status
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum PaneStatus {
    Working,
    Blocked,
    Done,
    Error,
    Detached,
}

/// Line style for scrollback rendering
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum LineStyle {
    Normal,
    System,
    Error,
    Success,
    ToolCall,
    ToolResult,
    Approval,
}

/// A single scrollback line
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScrollLine {
    pub text: String,
    pub style: LineStyle,
    pub timestamp: DateTime<Local>,
}

/// Tool call status
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ToolCallStatus {
    Pending,
    Running,
    Done,
    Failed,
    Approved,
    Rejected,
    Completed,
}

/// A tool call entry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub status: ToolCallStatus,
    pub started_at: DateTime<Local>,
    pub finished_at: Option<DateTime<Local>>,
    pub duration_ms: Option<u64>,
}

/// Risk level for approval requests
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RiskLevel {
    Low,
    Medium,
    High,
}

/// Approval request from a runner
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub id: String,
    pub tool_name: String,
    pub risk_level: RiskLevel,
    pub description: String,
    pub command_preview: String,
    pub timestamp: DateTime<Local>,
}

/// A single pane (one runner session)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pane {
    pub index: usize,
    pub session_id: String,
    pub runner: RunnerKind,
    pub status: PaneStatus,
    pub scroll_offset: usize,
    pub scrollback: usize,
    pub lines: VecDeque<ScrollLine>,
    pub tool_calls: Vec<ToolCall>,
    pub pending_approval: Option<ApprovalRequest>,
    pub created_at: DateTime<Local>,
}

impl Pane {
    pub fn new(index: usize, session_id: String, runner: RunnerKind, scrollback: usize) -> Self {
        Self {
            index,
            session_id,
            runner,
            status: PaneStatus::Working,
            scroll_offset: 0,
            scrollback,
            lines: VecDeque::with_capacity(scrollback),
            tool_calls: Vec::new(),
            pending_approval: None,
            created_at: Local::now(),
        }
    }

    /// Push a new line into scrollback
    pub fn push_line(&mut self, text: String, style: LineStyle) {
        if self.lines.len() >= self.scrollback {
            self.lines.pop_front();
        }
        self.lines.push_back(ScrollLine {
            text,
            style,
            timestamp: Local::now(),
        });
    }

    /// Scroll up (older content)
    pub fn scroll_up(&mut self, amount: usize) {
        self.scroll_offset = self.scroll_offset.saturating_add(amount);
        let max_offset = self.lines.len().saturating_sub(1);
        if self.scroll_offset > max_offset {
            self.scroll_offset = max_offset;
        }
    }

    /// Scroll down (newer content)
    pub fn scroll_down(&mut self, amount: usize) {
        self.scroll_offset = self.scroll_offset.saturating_sub(amount);
    }

    /// Scroll to bottom
    pub fn scroll_to_bottom(&mut self) {
        self.scroll_offset = 0;
    }

    /// Get active tool calls
    pub fn active_tool_calls(&self) -> Vec<&ToolCall> {
        self.tool_calls
            .iter()
            .filter(|tc| matches!(tc.status, ToolCallStatus::Running | ToolCallStatus::Pending))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pane_new_initial_state() {
        let pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        assert_eq!(pane.index, 0);
        assert_eq!(pane.session_id, "s1");
        assert_eq!(pane.runner, RunnerKind::Ga);
        assert_eq!(pane.status, PaneStatus::Working);
        assert_eq!(pane.scroll_offset, 0);
        assert_eq!(pane.scrollback, 100);
        assert!(pane.lines.is_empty());
        assert!(pane.tool_calls.is_empty());
        assert!(pane.pending_approval.is_none());
    }

    #[test]
    fn push_line_adds_to_scrollback() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        pane.push_line("hello".into(), LineStyle::Normal);
        pane.push_line("error!".into(), LineStyle::Error);
        assert_eq!(pane.lines.len(), 2);
        assert_eq!(pane.lines[0].text, "hello");
        assert_eq!(pane.lines[0].style, LineStyle::Normal);
        assert_eq!(pane.lines[1].text, "error!");
        assert_eq!(pane.lines[1].style, LineStyle::Error);
    }

    #[test]
    fn push_line_evicts_oldest_when_full() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 3);
        pane.push_line("a".into(), LineStyle::Normal);
        pane.push_line("b".into(), LineStyle::Normal);
        pane.push_line("c".into(), LineStyle::Normal);
        assert_eq!(pane.lines.len(), 3);
        // Push 4th line → oldest evicted
        pane.push_line("d".into(), LineStyle::Normal);
        assert_eq!(pane.lines.len(), 3);
        assert_eq!(pane.lines[0].text, "b");
        assert_eq!(pane.lines[2].text, "d");
    }

    #[test]
    fn scroll_up_increases_offset() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        for i in 0..10 {
            pane.push_line(format!("line{i}"), LineStyle::Normal);
        }
        pane.scroll_up(3);
        assert_eq!(pane.scroll_offset, 3);
        // Scroll beyond max → clamped
        pane.scroll_up(20);
        assert_eq!(pane.scroll_offset, 9); // max = len-1 = 9
    }

    #[test]
    fn scroll_down_decreases_offset() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        for i in 0..10 {
            pane.push_line(format!("line{i}"), LineStyle::Normal);
        }
        pane.scroll_up(5);
        assert_eq!(pane.scroll_offset, 5);
        pane.scroll_down(2);
        assert_eq!(pane.scroll_offset, 3);
        // Scroll below 0 → saturates at 0
        pane.scroll_down(100);
        assert_eq!(pane.scroll_offset, 0);
    }

    #[test]
    fn scroll_to_bottom_resets_offset() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        for i in 0..10 {
            pane.push_line(format!("line{i}"), LineStyle::Normal);
        }
        pane.scroll_up(5);
        assert_eq!(pane.scroll_offset, 5);
        pane.scroll_to_bottom();
        assert_eq!(pane.scroll_offset, 0);
    }

    #[test]
    fn active_tool_calls_filters_running_and_pending() {
        let mut pane = Pane::new(0, "s1".into(), RunnerKind::Ga, 100);
        let now = Local::now();
        pane.tool_calls.push(ToolCall {
            id: "t1".into(), name: "read".into(),
            status: ToolCallStatus::Running, started_at: now,
            finished_at: None, duration_ms: None,
        });
        pane.tool_calls.push(ToolCall {
            id: "t2".into(), name: "write".into(),
            status: ToolCallStatus::Pending, started_at: now,
            finished_at: None, duration_ms: None,
        });
        pane.tool_calls.push(ToolCall {
            id: "t3".into(), name: "done".into(),
            status: ToolCallStatus::Done, started_at: now,
            finished_at: Some(now), duration_ms: Some(100),
        });
        let active = pane.active_tool_calls();
        assert_eq!(active.len(), 2);
        assert_eq!(active[0].id, "t1");
        assert_eq!(active[1].id, "t2");
    }

    #[test]
    fn runner_kind_display() {
        assert_eq!(RunnerKind::Ga.to_string(), "ga");
        assert_eq!(RunnerKind::Codex.to_string(), "codex");
        assert_eq!(RunnerKind::Claude.to_string(), "claude");
        assert_eq!(RunnerKind::Subprocess.to_string(), "subprocess");
        assert_eq!(RunnerKind::Custom("custom-agent".into()).to_string(), "custom-agent");
    }

    #[test]
    fn runner_kind_serialize_deserialize() {
        let kinds = vec![RunnerKind::Ga, RunnerKind::Codex, RunnerKind::Claude, RunnerKind::Subprocess, RunnerKind::Custom("foo".into())];
        for kind in &kinds {
            let json = serde_json::to_string(kind).unwrap();
            let back: RunnerKind = serde_json::from_str(&json).unwrap();
            assert_eq!(*kind, back);
        }
    }

    #[test]
    fn subprocess_pane_creation() {
        let pane = Pane::new(0, "s0".into(), RunnerKind::Subprocess, 100);
        assert_eq!(pane.runner, RunnerKind::Subprocess);
        assert_eq!(pane.session_id, "s0");
    }

    #[test]
    fn pane_status_equality() {
        assert_eq!(PaneStatus::Working, PaneStatus::Working);
        assert_ne!(PaneStatus::Working, PaneStatus::Blocked);
    }
}
