//! Compatibility bridge between ga-tui's IPC protocol and ga_rust daemon's protocol.
//!
//! ## Problem
//! ga-tui expects a streaming JSON-lines event protocol (`IpcMessage`),
//! but ga_rust's daemon uses a request-response protocol (`DaemonCommand`/`DaemonResponse`)
//! with raw JSON over Unix sockets (read-until-EOF).
//!
//! ## Solution
//! This module provides:
//! - **Translation functions**: IpcCommand ↔ DaemonCommand, DaemonResponse → IpcMessage
//! - **DaemonCompatBridge**: A polling-based adapter that connects to ga_rust daemon,
//!   translates commands, and emits IpcMessage events to the TUI event loop.
//!
//! ## Limitations
//! ga_rust daemon only supports: Status, Shutdown, ListSessions, Run, Watch.
//! Many ga-tui commands (SessionInput, Approval*, Goal*) have no daemon equivalent
//! and will return `IpcMessage::Error` at runtime.

use crate::event::IpcMessage;
use crate::ipc::IpcCommand;
use std::path::PathBuf;
#[cfg(unix)]
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::mpsc;

// ─── Daemon-side protocol types (mirrors ga_rust/src/ipc/server.rs) ──────

/// Commands understood by ga_rust daemon.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum DaemonCommand {
    Status,
    Shutdown,
    ListSessions,
    Run { command: String },
    Watch { session_id: String },
}

/// Responses from ga_rust daemon.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum DaemonResponse {
    Ok(String),
    Error(String),
    Sessions(Vec<SessionInfo>),
}

/// Session info from daemon (mirrors ga_rust struct).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SessionInfo {
    pub id: String,
    pub status: String,
    #[serde(default)]
    pub command: Option<String>,
}

// ─── Translation: IpcCommand → DaemonCommand ─────────────────────────────

/// Translate a ga-tui IpcCommand to a ga_rust DaemonCommand.
/// Returns None if no mapping exists (the command is unsupported by daemon).
pub fn translate_command(cmd: &IpcCommand) -> Option<DaemonCommand> {
    match cmd {
        IpcCommand::SessionList => Some(DaemonCommand::ListSessions),
        IpcCommand::SessionNew { runner, goal } => {
            // Best-effort: compose a run command from runner + goal
            let command = if goal.is_empty() {
                runner.clone()
            } else {
                format!("{} {}", runner, goal)
            };
            Some(DaemonCommand::Run { command })
        }
        IpcCommand::SessionAttach { session_id } => {
            // Attach is semantically similar to Watch
            Some(DaemonCommand::Watch {
                session_id: session_id.clone(),
            })
        }
        IpcCommand::SessionReattach { session_id } => {
            // Reattach maps to Watch as well
            Some(DaemonCommand::Watch {
                session_id: session_id.clone(),
            })
        }
        // No daemon equivalents for these:
        IpcCommand::SessionDetach { .. }
        | IpcCommand::ApprovalApprove { .. }
        | IpcCommand::ApprovalReject { .. }
        | IpcCommand::GoalList
        | IpcCommand::GoalPropose { .. }
        | IpcCommand::GoalGet { .. }
        | IpcCommand::GoalConfirm { .. }
        | IpcCommand::GoalRun { .. }
        | IpcCommand::GoalPause { .. }
        | IpcCommand::SessionInput { .. } => None,
    }
}

/// Return the unsupported command name for error reporting.
pub fn unsupported_command_name(cmd: &IpcCommand) -> &'static str {
    match cmd {
        IpcCommand::SessionDetach { .. } => "SessionDetach",
        IpcCommand::ApprovalApprove { .. } => "ApprovalApprove",
        IpcCommand::ApprovalReject { .. } => "ApprovalReject",
        IpcCommand::GoalList => "GoalList",
        IpcCommand::GoalPropose { .. } => "GoalPropose",
        IpcCommand::GoalGet { .. } => "GoalGet",
        IpcCommand::GoalConfirm { .. } => "GoalConfirm",
        IpcCommand::GoalRun { .. } => "GoalRun",
        IpcCommand::GoalPause { .. } => "GoalPause",
        IpcCommand::SessionInput { .. } => "SessionInput",
        _ => "Unknown",
    }
}

// ─── Translation: DaemonResponse → IpcMessage ────────────────────────────

/// Translate a daemon response into zero or more IpcMessage events.
pub fn translate_response(
    original_cmd: &IpcCommand,
    resp: DaemonResponse,
) -> Vec<IpcMessage> {
    match resp {
        DaemonResponse::Ok(msg) => {
            // Map Ok responses to the most appropriate IpcMessage
            match original_cmd {
                IpcCommand::SessionNew { runner, .. } => {
                    // Daemon says "run: <cmd>" — synthesize a SessionNew event
                    // Extract session_id from the response if possible, else generate one
                    let session_id = extract_session_id(&msg).unwrap_or_else(|| format!("s-{}", &msg[..8.min(msg.len())]));
                    vec![IpcMessage::SessionNew {
                        session_id,
                        runner: runner.clone(),
                    }]
                }
                IpcCommand::SessionAttach { session_id }
                | IpcCommand::SessionReattach { session_id } => {
                    vec![IpcMessage::SessionReattach {
                        session_id: session_id.clone(),
                    }]
                }
                _ => {
                    // Generic status update
                    vec![IpcMessage::SessionStatus {
                        session_id: "daemon".to_string(),
                        status: msg,
                    }]
                }
            }
        }
        DaemonResponse::Error(err) => {
            vec![IpcMessage::Error {
                message: format!("Daemon error: {}", err),
            }]
        }
        DaemonResponse::Sessions(sessions) => {
            // Translate session list into individual status events
            sessions
                .into_iter()
                .map(|s| IpcMessage::SessionStatus {
                    session_id: s.id,
                    status: s.status,
                })
                .collect()
        }
    }
}

/// Try to extract a session ID from a daemon Ok response like "run: <cmd>".
fn extract_session_id(msg: &str) -> Option<String> {
    // Daemon returns "run: <command>" — not a real session ID.
    // For now, return None; the caller will generate a placeholder.
    let _ = msg;
    None
}

// ─── Bridge: connects to ga_rust daemon and provides IpcMessage stream ───

/// Polling-based bridge that connects ga-tui to ga_rust daemon.
///
/// Since ga_rust daemon uses request-response (not streaming), this bridge:
/// 1. Sends IpcCommands by translating them to DaemonCommands
/// 2. Polls the daemon periodically for session updates
/// 3. Translates DaemonResponses back to IpcMessage events
pub struct DaemonCompatBridge {
    #[allow(dead_code)] // Used on Unix via send_daemon_command
    socket_path: PathBuf,
    tx: mpsc::Sender<IpcMessage>,
    poll_interval: std::time::Duration,
}

impl DaemonCompatBridge {
    /// Create a new bridge.
    pub fn new(socket_path: PathBuf, tx: mpsc::Sender<IpcMessage>) -> Self {
        Self {
            socket_path,
            tx,
            poll_interval: std::time::Duration::from_secs(5),
        }
    }

    /// Set the polling interval for session status updates.
    pub fn with_poll_interval(mut self, interval: std::time::Duration) -> Self {
        self.poll_interval = interval;
        self
    }

    /// Start the bridge: spawn a background task that polls the daemon.
    pub fn start(self) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            // Initial status check
            if let Err(e) = self.send_daemon_command(DaemonCommand::Status).await {
                tracing::warn!("DaemonCompatBridge: initial status check failed: {}", e);
            }

            // Polling loop
            let mut interval = tokio::time::interval(self.poll_interval);
            loop {
                interval.tick().await;

                // Poll session list
                match self.send_daemon_command(DaemonCommand::ListSessions).await {
                    Ok(resp) => {
                        let msgs = self.translate_list_sessions(resp);
                        for msg in msgs {
                            if self.tx.send(msg).await.is_err() {
                                // Receiver dropped, exit
                                return;
                            }
                        }
                    }
                    Err(e) => {
                        tracing::debug!("DaemonCompatBridge: poll error: {}", e);
                    }
                }
            }
        })
    }

    /// Send an IpcCommand through the bridge (translates to daemon protocol).
    pub async fn send_ipc_command(&self, cmd: IpcCommand) -> anyhow::Result<()> {
        match translate_command(&cmd) {
            Some(daemon_cmd) => {
                let resp = self.send_daemon_command(daemon_cmd).await?;
                let msgs = translate_response(&cmd, resp);
                for msg in msgs {
                    self.tx.send(msg).await.map_err(|e| anyhow::anyhow!("tx send: {}", e))?;
                }
                Ok(())
            }
            None => {
                let name = unsupported_command_name(&cmd);
                self.tx
                    .send(IpcMessage::Error {
                        message: format!(
                            "Command '{}' not supported by ga_rust daemon protocol",
                            name
                        ),
                    })
                    .await
                    .map_err(|e| anyhow::anyhow!("tx send: {}", e))?;
                Ok(())
            }
        }
    }

    /// Send a raw DaemonCommand to the daemon and receive response.
    /// Uses ga_rust's wire format: raw JSON, read until EOF.
    async fn send_daemon_command(
        &self,
        cmd: DaemonCommand,
    ) -> anyhow::Result<DaemonResponse> {
        #[cfg(unix)]
        {
            use tokio::net::UnixStream;

            let mut stream = UnixStream::connect(&self.socket_path)
                .await
                .map_err(|e| anyhow::anyhow!("connect to {:?}: {}", self.socket_path, e))?;

            let data = serde_json::to_vec(&cmd)?;
            stream.write_all(&data).await?;
            stream.shutdown().await?; // Signal EOF so daemon processes the command

            let mut buf = Vec::new();
            let mut tmp = [0u8; 4096];
            loop {
                let n = stream.read(&mut tmp).await?;
                if n == 0 {
                    break;
                }
                buf.extend_from_slice(&tmp[..n]);
            }

            let resp: DaemonResponse = serde_json::from_slice(&buf)?;
            Ok(resp)
        }

        #[cfg(windows)]
        {
            let _ = cmd;
            anyhow::bail!("Windows IPC bridge not yet implemented")
        }
    }

    /// Translate ListSessions response into IpcMessage events.
    fn translate_list_sessions(&self, resp: DaemonResponse) -> Vec<IpcMessage> {
        match resp {
            DaemonResponse::Sessions(sessions) => sessions
                .into_iter()
                .map(|s| IpcMessage::SessionStatus {
                    session_id: s.id,
                    status: s.status,
                })
                .collect(),
            DaemonResponse::Error(err) => vec![IpcMessage::Error {
                message: format!("ListSessions failed: {}", err),
            }],
            DaemonResponse::Ok(msg) => {
                tracing::debug!("Unexpected Ok from ListSessions: {}", msg);
                vec![]
            }
        }
    }
}

// ─── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_translate_session_list() {
        let cmd = IpcCommand::SessionList;
        let result = translate_command(&cmd);
        assert!(matches!(result, Some(DaemonCommand::ListSessions)));
    }

    #[test]
    fn test_translate_session_new() {
        let cmd = IpcCommand::SessionNew {
            runner: "claude".to_string(),
            goal: "fix bug".to_string(),
        };
        let result = translate_command(&cmd);
        assert!(matches!(result, Some(DaemonCommand::Run { .. })));
        if let Some(DaemonCommand::Run { command }) = result {
            assert_eq!(command, "claude fix bug");
        }
    }

    #[test]
    fn test_translate_session_attach() {
        let cmd = IpcCommand::SessionAttach {
            session_id: "s1".to_string(),
        };
        let result = translate_command(&cmd);
        assert!(matches!(
            result,
            Some(DaemonCommand::Watch { session_id }) if session_id == "s1"
        ));
    }

    #[test]
    fn test_translate_unsupported() {
        let cmd = IpcCommand::SessionInput {
            session_id: "s1".to_string(),
            text: "hello".to_string(),
        };
        assert!(translate_command(&cmd).is_none());
        assert_eq!(unsupported_command_name(&cmd), "SessionInput");
    }

    #[test]
    fn test_translate_response_sessions() {
        let cmd = IpcCommand::SessionList;
        let resp = DaemonResponse::Sessions(vec![
            SessionInfo {
                id: "s1".into(),
                status: "running".into(),
                command: None,
            },
            SessionInfo {
                id: "s2".into(),
                status: "idle".into(),
                command: Some("test".into()),
            },
        ]);
        let msgs = translate_response(&cmd, resp);
        assert_eq!(msgs.len(), 2);
        assert!(matches!(&msgs[0], IpcMessage::SessionStatus { session_id, status }
            if session_id == "s1" && status == "running"));
        assert!(matches!(&msgs[1], IpcMessage::SessionStatus { session_id, status }
            if session_id == "s2" && status == "idle"));
    }

    #[test]
    fn test_translate_response_ok_new_session() {
        let cmd = IpcCommand::SessionNew {
            runner: "claude".to_string(),
            goal: "test".to_string(),
        };
        let resp = DaemonResponse::Ok("run: claude test".to_string());
        let msgs = translate_response(&cmd, resp);
        assert_eq!(msgs.len(), 1);
        assert!(matches!(&msgs[0], IpcMessage::SessionNew { runner, .. } if runner == "claude"));
    }

    #[test]
    fn test_translate_response_error() {
        let cmd = IpcCommand::SessionList;
        let resp = DaemonResponse::Error("connection refused".to_string());
        let msgs = translate_response(&cmd, resp);
        assert_eq!(msgs.len(), 1);
        assert!(matches!(&msgs[0], IpcMessage::Error { message } if message.contains("connection refused")));
    }

    #[test]
    fn test_daemon_command_serialization() {
        let cmd = DaemonCommand::ListSessions;
        let json = serde_json::to_string(&cmd).unwrap();
        assert!(json.contains("ListSessions"));
        let parsed: DaemonCommand = serde_json::from_str(&json).unwrap();
        assert!(matches!(parsed, DaemonCommand::ListSessions));
    }

    #[test]
    fn test_daemon_response_serialization() {
        let resp = DaemonResponse::Sessions(vec![SessionInfo {
            id: "s1".into(),
            status: "running".into(),
            command: None,
        }]);
        let json = serde_json::to_string(&resp).unwrap();
        let parsed: DaemonResponse = serde_json::from_str(&json).unwrap();
        assert!(matches!(parsed, DaemonResponse::Sessions(s) if s.len() == 1));
    }

    #[test]
    fn test_all_unsupported_commands() {
        let unsupported = vec![
            IpcCommand::SessionDetach { session_id: "s1".into() },
            IpcCommand::ApprovalApprove { session_id: "s1".into(), approval_id: "a1".into() },
            IpcCommand::ApprovalReject { session_id: "s1".into(), approval_id: "a1".into() },
            IpcCommand::GoalList,
            IpcCommand::GoalPropose { title: "t".into(), description: None, priority: None, budget: None, timeout: None },
            IpcCommand::GoalGet { goal_id: "g1".into() },
            IpcCommand::GoalConfirm { goal_id: "g1".into() },
            IpcCommand::GoalRun { goal_id: "g1".into() },
            IpcCommand::GoalPause { goal_id: "g1".into() },
            IpcCommand::SessionInput { session_id: "s1".into(), text: "hi".into() },
        ];
        for cmd in &unsupported {
            assert!(translate_command(cmd).is_none(), "{:?} should be unsupported", cmd);
        }
    }
}
