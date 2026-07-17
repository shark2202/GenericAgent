use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IpcMessage {
    pub session_id: String,
    pub kind: EventKind,
    pub payload: String,
    pub ts: String,
}

impl IpcMessage {
    pub fn kind_variant(&self) -> String {
        self.kind.as_str().to_string()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum EventKind {
    Output,
    Status,
    ApprovalRequest,
    ApprovalResponse,
    Error,
    Heartbeat,
}

impl EventKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            EventKind::Output => "Output",
            EventKind::Status => "Status",
            EventKind::ApprovalRequest => "ApprovalRequest",
            EventKind::ApprovalResponse => "ApprovalResponse",
            EventKind::Error => "Error",
            EventKind::Heartbeat => "Heartbeat",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub session_id: String,
    pub tool_name: Option<String>,
    pub command: Option<String>,
    pub risk_level: RiskLevel,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum RiskLevel {
    Low,
    Medium,
    High,
}

impl RiskLevel {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "Low" => Some(RiskLevel::Low),
            "Medium" => Some(RiskLevel::Medium),
            "High" => Some(RiskLevel::High),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            RiskLevel::Low => "Low",
            RiskLevel::Medium => "Medium",
            RiskLevel::High => "High",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalResponse {
    pub session_id: String,
    pub approved: bool,
    pub reason: Option<String>,
}
