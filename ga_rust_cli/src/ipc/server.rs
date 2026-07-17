use anyhow::{bail, Context, Result};
use std::path::{Path, PathBuf};

// ─── IPC Protocol types ────────────────────────────────────────────

/// Commands sent from CLI to daemon over IPC
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum DaemonCommand {
    /// Request daemon status info
    Status,
    /// Request graceful shutdown
    Shutdown,
    /// List active sessions
    ListSessions,
    /// Run a command in a new session
    Run { command: String },
    /// Watch a session's output
    Watch { session_id: String },
}

/// Responses from daemon to CLI
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum DaemonResponse {
    Ok(String),
    Sessions(Vec<SessionInfo>),
    Error(String),
}

/// Info about a session managed by the daemon
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SessionInfo {
    pub id: String,
    pub name: String,
    pub runner_kind: String,
    pub status: String,
    pub pid: u32,
}

// ─── IPC Server ────────────────────────────────────────────────────

/// IPC server that listens for CLI commands.
/// Uses Unix domain socket on Unix, named pipe on Windows.
pub struct IpcServer {
    socket_path: PathBuf,
}

impl IpcServer {
    /// Create a new IPC server bound to the given socket path.
    pub fn new(socket_path: PathBuf) -> Self {
        Self { socket_path }
    }

    /// Get the socket path.
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    /// Start listening and handling connections (async).
    /// Takes a handler closure that processes each DaemonCommand.
    pub async fn run<H>(&self, handler: H, mut shutdown_rx: tokio::sync::broadcast::Receiver<()>)
    where
        H: Fn(DaemonCommand) -> DaemonResponse + Send + Sync + 'static,
    {
        // Clean up stale socket
        if self.socket_path.exists() {
            let _ = std::fs::remove_file(&self.socket_path);
        }

        #[cfg(unix)]
        {
            use tokio::net::UnixListener;
            let listener = match UnixListener::bind(&self.socket_path) {
                Ok(l) => l,
                Err(e) => {
                    eprintln!("IPC: failed to bind socket {:?}: {}", self.socket_path, e);
                    return;
                }
            };

            eprintln!("IPC: listening on {:?}", self.socket_path);

            loop {
                tokio::select! {
                    accept_result = listener.accept() => {
                        match accept_result {
                            Ok((stream, _addr)) => {
                                let handler = &handler;
                                Self::handle_connection_unix(stream, handler).await;
                            }
                            Err(e) => {
                                eprintln!("IPC: accept error: {}", e);
                            }
                        }
                    }
                    _ = shutdown_rx.recv() => {
                        eprintln!("IPC: shutdown signal received");
                        break;
                    }
                }
            }
        }

        #[cfg(windows)]
        {
            // Windows named pipe implementation
            // Simplified: use TCP localhost as IPC transport on Windows
            eprintln!("IPC: Windows named pipe IPC not yet implemented, using placeholder");
            // Wait for shutdown
            let _ = shutdown_rx.recv().await;
        }

        // Cleanup socket on exit
        if self.socket_path.exists() {
            let _ = std::fs::remove_file(&self.socket_path);
        }
    }

    #[cfg(unix)]
    async fn handle_connection_unix<H>(
        stream: tokio::net::UnixStream,
        handler: &H,
    ) where
        H: Fn(DaemonCommand) -> DaemonResponse + Send + Sync + 'static,
    {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let (mut read_half, mut write_half) = stream.into_split();

        let mut buf = Vec::new();
        let mut tmp = [0u8; 4096];

        match read_half.read(&mut tmp).await {
            Ok(0) => return,
            Ok(n) => buf.extend_from_slice(&tmp[..n]),
            Err(e) => {
                eprintln!("IPC: read error: {}", e);
                return;
            }
        };

        let cmd: DaemonCommand = match serde_json::from_slice(&buf) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("IPC: invalid command: {}", e);
                let err_resp = DaemonResponse::Error(format!("Invalid command: {}", e));
                let _ = write_half
                    .write_all(&serde_json::to_vec(&err_resp).unwrap_or_default())
                    .await;
                return;
            }
        };

        let resp = handler(cmd);
        if let Ok(data) = serde_json::to_vec(&resp) {
            let _ = write_half.write_all(&data).await;
        }
    }
}

// ─── IPC Client ────────────────────────────────────────────────────

/// Client for connecting to the daemon's IPC socket.
pub struct IpcClient {
    socket_path: PathBuf,
}

impl IpcClient {
    pub fn new(socket_path: PathBuf) -> Self {
        Self { socket_path }
    }

    /// Send a command to the daemon and receive response.
    pub async fn send(&self, cmd: DaemonCommand) -> Result<DaemonResponse> {
        #[cfg(unix)]
        {
            use tokio::io::{AsyncReadExt, AsyncWriteExt};
            use tokio::net::UnixStream;

            let mut stream = UnixStream::connect(&self.socket_path)
                .await
                .with_context(|| format!("Cannot connect to daemon at {:?}", self.socket_path))?;

            let data = serde_json::to_vec(&cmd)?;
            stream.write_all(&data).await?;

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
            bail!("Windows IPC client not yet implemented");
        }
    }
}

// ─── Handler trait ─────────────────────────────────────────────────

/// Trait for handling IPC commands (for testability).
pub trait IpcHandler: Send + Sync + 'static {
    fn handle(&self, cmd: DaemonCommand) -> DaemonResponse;
}

/// Default handler that returns not-implemented responses.
pub struct DefaultIpcHandler;

impl IpcHandler for DefaultIpcHandler {
    fn handle(&self, cmd: DaemonCommand) -> DaemonResponse {
        match cmd {
            DaemonCommand::Status => DaemonResponse::Ok("daemon running".to_string()),
            DaemonCommand::Shutdown => DaemonResponse::Ok("shutting down".to_string()),
            DaemonCommand::ListSessions => DaemonResponse::Sessions(vec![]),
            DaemonCommand::Run { command } => DaemonResponse::Ok(format!("run: {}", command)),
            DaemonCommand::Watch { session_id } => DaemonResponse::Ok(format!("watch: {}", session_id)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_command_serialization() {
        let cmd = DaemonCommand::Status;
        let json = serde_json::to_string(&cmd).unwrap();
        assert!(json.contains("Status"));

        let cmd2: DaemonCommand = serde_json::from_str(&json).unwrap();
        assert!(matches!(cmd2, DaemonCommand::Status));
    }

    #[test]
    fn test_response_serialization() {
        let resp = DaemonResponse::Ok("hello".to_string());
        let json = serde_json::to_string(&resp).unwrap();
        let resp2: DaemonResponse = serde_json::from_str(&json).unwrap();
        assert!(matches!(resp2, DaemonResponse::Ok(_)));
    }
}
