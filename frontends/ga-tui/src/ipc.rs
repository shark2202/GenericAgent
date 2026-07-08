//! IPC client for ga-core daemon communication
//! Uses named pipes on Windows, Unix sockets on Unix

use crate::event::IpcMessage;
use std::path::PathBuf;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

/// IPC command to send to core daemon
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "type")]
pub enum IpcCommand {
    /// List sessions
    SessionList,
    /// Create a new session
    SessionNew { runner: String, goal: String },
    /// Attach to a session
    SessionAttach { session_id: String },
    /// Detach from a session
    SessionDetach { session_id: String },
    /// Approve a pending tool call
    ApprovalApprove { session_id: String, approval_id: String },
    /// Reject a pending tool call
    ApprovalReject { session_id: String, approval_id: String },
    /// Get goal state
    GoalGet { goal_id: String },
}

/// IPC client connecting to ga-core daemon
pub struct IpcClient {
    socket_path: PathBuf,
    tx: mpsc::Sender<IpcMessage>,
}

impl IpcClient {
    pub fn new(socket_path: PathBuf, tx: mpsc::Sender<IpcMessage>) -> Self {
        Self { socket_path, tx }
    }

    /// Start the IPC reader task
    /// On Windows: connect via named pipe (\\.\pipe\ga-core)
    /// On Unix: connect via Unix domain socket
    pub fn start_reader(&self) -> anyhow::Result<JoinHandle<()>> {
        let tx = self.tx.clone();
        let socket_path = self.socket_path.clone();

        let handle = tokio::spawn(async move {
            // Try to connect; if daemon not running, just wait and retry
            loop {
                match try_connect_and_read(&socket_path, &tx).await {
                    Ok(()) => {
                        // Connection closed, retry after delay
                        tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                    }
                    Err(_) => {
                        // Connection failed, retry after delay
                        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
                    }
                }
            }
        });

        Ok(handle)
    }

    /// Send a command to the core daemon
    pub async fn send_command(&self, cmd: IpcCommand) -> anyhow::Result<()> {
        let json = serde_json::to_string(&cmd)?;
        // For now, write to stdout as stub; real impl would write to socket
        let _ = json;
        // TODO: actual socket write
        Ok(())
    }
}

async fn try_connect_and_read(
    socket_path: &PathBuf,
    tx: &mpsc::Sender<IpcMessage>,
) -> anyhow::Result<()> {
    // Stub: in real impl, connect to daemon and read messages
    // For now, just sleep to avoid busy loop
    tokio::time::sleep(tokio::time::Duration::from_secs(60)).await;
    Ok(())
}
