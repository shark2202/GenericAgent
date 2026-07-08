use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IpcMessage {
    pub session_id: String,
    pub kind: EventKind,
    pub payload: String,
    pub ts: String,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub session_id: String,
    pub command: String,
    pub risk_level: RiskLevel,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum RiskLevel {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalResponse {
    pub session_id: String,
    pub approved: bool,
    pub reason: Option<String>,
}
