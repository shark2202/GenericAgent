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
    Custom(String),
}

impl std::fmt::Display for RunnerKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunnerKind::Ga => write!(f, "ga"),
            RunnerKind::Codex => write!(f, "codex"),
            RunnerKind::Claude => write!(f, "claude"),
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
