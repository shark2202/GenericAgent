//! IPC client for ga-core daemon communication
//! Uses named pipes on Windows, Unix sockets on Unix

use crate::event::IpcMessage;
use std::path::PathBuf;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
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
                        // Connection closed gracefully, retry after delay
                        tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                    }
                    Err(e) => {
                        // Connection failed, retry after delay
                        tracing::debug!("IPC connection error: {e}, retrying...");
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
        let mut conn = connect_socket(&self.socket_path).await?;
        // Protocol: each message is a JSON line terminated by newline
        conn.write_all(json.as_bytes()).await?;
        conn.write_all(b"\n").await?;
        conn.flush().await?;
        Ok(())
    }
}

/// Connect to the IPC socket (named pipe on Windows, Unix socket on Unix)
async fn connect_socket(socket_path: &PathBuf) -> anyhow::Result<tokio::io::BufWriter<IpcStream>> {
    let stream = IpcStream::connect(socket_path).await?;
    Ok(tokio::io::BufWriter::new(stream))
}

/// Platform-specific IPC stream abstraction
enum IpcStream {
    #[cfg(windows)]
    Windows(tokio::net::windows::named_pipe::NamedPipeClient),
    #[cfg(unix)]
    Unix(tokio::net::UnixStream),
}

impl IpcStream {
    async fn connect(path: &PathBuf) -> anyhow::Result<Self> {
        #[cfg(windows)]
        {
            // Named pipe path format: \\.\pipe\ga-core
            let pipe_path = path.to_string_lossy().to_string();
            let client = tokio::net::windows::named_pipe::ClientOptions::new()
                .open(&pipe_path)?;
            Ok(IpcStream::Windows(client))
        }
        #[cfg(unix)]
        {
            let stream = tokio::net::UnixStream::connect(path).await?;
            Ok(IpcStream::Unix(stream))
        }
        #[cfg(not(any(windows, unix)))]
        {
            anyhow::bail!("IPC not supported on this platform")
        }
    }
}

impl tokio::io::AsyncRead for IpcStream {
    fn poll_read(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
        buf: &mut tokio::io::ReadBuf<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            #[cfg(windows)]
            IpcStream::Windows(s) => std::pin::Pin::new(s).poll_read(cx, buf),
            #[cfg(unix)]
            IpcStream::Unix(s) => std::pin::Pin::new(s).poll_read(cx, buf),
        }
    }
}

impl tokio::io::AsyncWrite for IpcStream {
    fn poll_write(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
        buf: &[u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        match self.get_mut() {
            #[cfg(windows)]
            IpcStream::Windows(s) => std::pin::Pin::new(s).poll_write(cx, buf),
            #[cfg(unix)]
            IpcStream::Unix(s) => std::pin::Pin::new(s).poll_write(cx, buf),
        }
    }

    fn poll_flush(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            #[cfg(windows)]
            IpcStream::Windows(s) => std::pin::Pin::new(s).poll_flush(cx),
            #[cfg(unix)]
            IpcStream::Unix(s) => std::pin::Pin::new(s).poll_flush(cx),
        }
    }

    fn poll_shutdown(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        match self.get_mut() {
            #[cfg(windows)]
            IpcStream::Windows(s) => std::pin::Pin::new(s).poll_shutdown(cx),
            #[cfg(unix)]
            IpcStream::Unix(s) => std::pin::Pin::new(s).poll_shutdown(cx),
        }
    }
}

async fn try_connect_and_read(
    socket_path: &PathBuf,
    tx: &mpsc::Sender<IpcMessage>,
) -> anyhow::Result<()> {
    let stream = IpcStream::connect(socket_path).await?;
    let reader = BufReader::new(stream);

    // Read JSON lines from the daemon
    let mut lines = reader.lines();
    while let Some(line) = lines.next_line().await? {
        match serde_json::from_str::<IpcMessage>(&line) {
            Ok(msg) => {
                if tx.send(msg).await.is_err() {
                    // Channel closed, app is shutting down
                    return Ok(());
                }
            }
            Err(e) => {
                tracing::warn!("Failed to parse IPC message: {e}");
            }
        }
    }
    // Stream ended (daemon disconnected)
    Ok(())
}
