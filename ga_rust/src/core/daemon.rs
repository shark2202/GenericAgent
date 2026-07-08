//! Daemon mode: background process with IPC control plane.
//!
//! §7.1 – Daemon lifecycle (start/stop/status/find-running)
//! §7.2 – Event consumption loop
//! §7.3 – Reattach support

use anyhow::{bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::ipc::server::{DaemonCommand, DaemonResponse, IpcClient, IpcServer};
use crate::store::Store;

// ─── PID file ──────────────────────────────────────────────────────

/// PID file stored at `<data_dir>/daemon.pid`.
/// Format: 3 lines – PID, start_epoch_secs, ipc_socket_name
#[derive(Debug)]
pub struct PidFile {
    pub pid: u32,
    pub started_at: u64,
    pub socket_name: String,
}

impl PidFile {
    fn path(data_dir: &Path) -> PathBuf {
        data_dir.join("daemon.pid")
    }

    pub fn read(data_dir: &Path) -> Option<Self> {
        let raw = fs::read_to_string(Self::path(data_dir)).ok()?;
        let mut lines = raw.lines();
        let pid: u32 = lines.next()?.trim().parse().ok()?;
        let started_at: u64 = lines.next()?.trim().parse().ok()?;
        let socket_name = lines.next()?.trim().to_string();
        Some(Self {
            pid,
            started_at,
            socket_name,
        })
    }

    fn write(data_dir: &Path, pid: u32, socket_name: &str) -> Result<()> {
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let content = format!("{}\n{}\n{}\n", pid, ts, socket_name);
        fs::write(Self::path(data_dir), content)?;
        Ok(())
    }

    fn remove(data_dir: &Path) {
        let _ = fs::remove_file(Self::path(data_dir));
    }
}

// ─── Process liveness (cross-platform) ─────────────────────────────

pub fn is_process_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        // SIGKILL=9, but signal 0 just checks existence
        unsafe { libc::kill(pid as i32, 0) == 0 }
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{BOOL, FALSE};
        use windows_sys::Win32::System::Threading::{OpenProcess, INFINITE, WaitForSingleObject};
        use windows_sys::Win32::Foundation::CloseHandle;
        const SYNCHRONIZE: u32 = 0x100000;
        let handle = unsafe { OpenProcess(SYNCHRONIZE, FALSE, pid) };
        if handle == 0 as *mut _ {
            return false;
        }
        let wait = unsafe { WaitForSingleObject(handle, 0) };
        unsafe { CloseHandle(handle) };
        // WAIT_OBJECT_0 = 0 means process still running? Actually WAIT_TIMEOUT=258 means still running
        // WAIT_OBJECT_0 means signaled (process exited), WAIT_TIMEOUT means still alive
        wait == 258 // WAIT_TIMEOUT
    }
}

// ─── Daemon info returned to caller ────────────────────────────────

#[derive(Debug)]
pub struct DaemonInfo {
    pub pid: u32,
    pub started_at: u64,
    pub socket_path: PathBuf,
}

// ─── Daemon struct ─────────────────────────────────────────────────

pub struct Daemon {
    pub ga_root: PathBuf,
    pub data_dir: PathBuf,
}

impl Daemon {
    pub fn new(ga_root: &str) -> Result<Self> {
        let root = PathBuf::from(ga_root).canonicalize().unwrap_or_else(|_| PathBuf::from(ga_root));
        let data_dir = root.join(".ga").join("data");
        fs::create_dir_all(&data_dir)?;
        Ok(Self {
            ga_root: root,
            data_dir,
        })
    }

    fn socket_path(&self) -> PathBuf {
        self.data_dir.join("ga-daemon.sock")
    }

    // ── §7.1 Start ────────────────────────────────────────────────

    /// Start daemon. If `foreground` is false, spawn a background child that
    /// re-invokes the binary with `--daemon-fg`, then return.
    pub fn start(&self, foreground: bool) -> Result<()> {
        if foreground {
            return self.run_foreground();
        }

        // Check if already running
        if let Some(info) = Self::find_running_inner(&self.data_dir)? {
            bail!("Daemon already running (PID {})", info.pid);
        }

        // Spawn background process: re-invoke self with --daemon-fg
        let exe = std::env::current_exe().context("Cannot find current executable")?;
        let child = std::process::Command::new(exe)
            .arg("--daemon-fg")
            .arg("--root")
            .arg(&self.ga_root)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .context("Failed to spawn daemon process")?;

        let pid = child.id();
        // Brief wait for daemon to write PID file
        std::thread::sleep(Duration::from_millis(500));

        if Self::find_running_inner(&self.data_dir)?.is_some() {
            println!("Daemon started (PID {})", pid);
        } else {
            // Daemon may still be starting; PID file might not exist yet
            println!("Daemon process spawned (PID {}). Use `ga daemon-status` to verify.", pid);
        }

        Ok(())
    }

    /// Foreground daemon loop. Called when `--daemon-fg` is passed.
    fn run_foreground(&self) -> Result<()> {
        // Double-check: if another daemon is running, refuse
        if let Some(info) = Self::find_running_inner(&self.data_dir)? {
            bail!("Another daemon is already running (PID {})", info.pid);
        }

        // Init tracing for daemon process (try_init to avoid panic if already set by main)
        let _ = tracing_subscriber::fmt()
            .with_env_filter(
                tracing_subscriber::EnvFilter::try_from_default_env()
                    .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
            )
            .try_init();

        let pid = std::process::id();
        let socket_path = self.socket_path();
        let data_dir = self.data_dir.clone();

        tracing::info!(pid, "Daemon starting in foreground mode");

        // Write PID file
        let sock_name = socket_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "ga-daemon.sock".into());
        PidFile::write(&data_dir, pid, &sock_name)?;

        // Open store (Arc for sharing across threads)
        let store = Arc::new(Store::new(&data_dir.join("ga.db").to_string_lossy())?);

        // Create IPC server
        let ipc = IpcServer::new(socket_path.clone());

        // Shutdown signal
        let (shutdown_tx, shutdown_rx) = tokio::sync::broadcast::channel::<()>(1);
        let running = Arc::new(AtomicBool::new(true));

        // §7.2 – Event consumption loop: poll store for active sessions
        let store_evt = Arc::clone(&store);
        let running_evt = running.clone();
        let event_handle = std::thread::spawn(move || {
            let mut last_event_id: i64 = 0;
            while running_evt.load(Ordering::Relaxed) {
                // Poll recent events
                if let Ok(events) = store_evt.events_recent(None, 100) {
                    for (id, session_id, kind, data, ts) in events.iter().rev() {
                        if *id > last_event_id {
                            last_event_id = *id;
                            tracing::debug!(id, %session_id, %kind, "Event consumed");
                        }
                    }
                }
                std::thread::sleep(Duration::from_millis(250));
            }
        });

        // Build handler for IPC commands
        let store_hdl = Arc::clone(&store);
        let data_dir_hdl = data_dir.clone();
        let running_hdl = running.clone();
        let handler = move |cmd: DaemonCommand| -> DaemonResponse {
            match cmd {
                DaemonCommand::Status => {
                    let uptime = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs();
                    DaemonResponse::Ok(format!(
                        "pid={pid} uptime={}s socket={sock_name}",
                        uptime
                    ))
                }
                DaemonCommand::Shutdown => {
                    tracing::info!("Shutdown requested via IPC");
                    running_hdl.store(false, Ordering::Relaxed);
                    let _ = shutdown_tx.send(());
                    DaemonResponse::Ok("Shutting down".into())
                }
                DaemonCommand::ListSessions => {
                    match store_hdl.sessions_list() {
                        Ok(sessions) => {
                            let summary: Vec<String> = sessions
                                .iter()
                                .map(|s| format!("{} [{}] {}", s.id, s.status, s.runner_kind))
                                .collect();
                            DaemonResponse::Ok(summary.join("\n"))
                        }
                        Err(e) => DaemonResponse::Error(format!("DB error: {}", e)),
                    }
                }
                DaemonCommand::Run { command } => {
                    // §7.2 – Create a new session for the command
                    match store_hdl.session_create(&command, None::<&str>) {
                        Ok(session) => {
                            let sid = session.id.clone();
                            // Record start event
                            let now = chrono::Utc::now().to_rfc3339();
                            let _ = store_hdl.event_create(&sid, "started", Some(&command), &now);
                            DaemonResponse::Ok(format!("Session {} created for: {}", sid, command))
                        }
                        Err(e) => DaemonResponse::Error(format!("Cannot create session: {}", e)),
                    }
                }
                DaemonCommand::Watch { session_id } => {
                    // §7.3 – Reattach: show recent events for session
                    match store_hdl.events_recent(Some(&session_id), 50) {
                        Ok(events) => {
                            if events.is_empty() {
                                DaemonResponse::Ok("No events for this session".into())
                            } else {
                                let lines: Vec<String> = events
                                    .iter()
                                    .rev()
                                    .map(|(id, _sid, kind, data, ts)| {
                                        format!(
                                            "[{}] {} {}",
                                            ts,
                                            kind,
                                            data.as_deref().unwrap_or("")
                                        )
                                    })
                                    .collect();
                                DaemonResponse::Ok(lines.join("\n"))
                            }
                        }
                        Err(e) => DaemonResponse::Error(format!("DB error: {}", e)),
                    }
                }
            }
        };

        // Run the async IPC server loop
        let rt = tokio::runtime::Runtime::new()?;
        rt.block_on(async {
            ipc.run(handler, shutdown_rx).await;
        });

        // Cleanup
        running.store(false, Ordering::Relaxed);
        let _ = event_handle.join();
        PidFile::remove(&data_dir_hdl);
        tracing::info!("Daemon shut down cleanly");

        Ok(())
    }

    // ── §7.1 Stop ─────────────────────────────────────────────────

    /// Stop daemon via IPC Shutdown command, or kill as fallback.
    pub fn stop(&self) -> Result<()> {
        let Some(pid_file) = PidFile::read(&self.data_dir) else {
            bail!("No daemon PID file found — is the daemon running?");
        };

        let socket_path = self.socket_path();

        // Try graceful shutdown via IPC
        let rt = tokio::runtime::Runtime::new()?;
        let result = rt.block_on(async {
            let client = IpcClient::new(socket_path);
            client.send(DaemonCommand::Shutdown).await
        });

        match result {
            Ok(DaemonResponse::Ok(msg)) => {
                println!("Daemon shutting down: {}", msg);
                // Wait for process to exit
                for _ in 0..20 {
                    if !is_process_alive(pid_file.pid) {
                        println!("Daemon stopped (PID {})", pid_file.pid);
                        return Ok(());
                    }
                    std::thread::sleep(Duration::from_millis(250));
                }
                // Fallback: force kill
                force_kill(pid_file.pid);
                PidFile::remove(&self.data_dir);
                println!("Daemon force-killed (PID {})", pid_file.pid);
                Ok(())
            }
            Ok(other) => {
                bail!("Unexpected response from daemon: {:?}", other);
            }
            Err(_) => {
                // IPC failed — try signal kill
                if is_process_alive(pid_file.pid) {
                    tracing::warn!("IPC failed, sending termination signal to PID {}", pid_file.pid);
                    force_kill(pid_file.pid);
                    PidFile::remove(&self.data_dir);
                    println!("Daemon killed (PID {})", pid_file.pid);
                } else {
                    PidFile::remove(&self.data_dir);
                    bail!("Daemon process (PID {}) is not alive; cleaned up stale PID file", pid_file.pid);
                }
                Ok(())
            }
        }
    }

    // ── §7.1 Status ───────────────────────────────────────────────

    /// Print daemon status to stdout.
    pub fn print_status(&self) -> Result<()> {
        let Some(pid_file) = PidFile::read(&self.data_dir) else {
            println!("Daemon is not running (no PID file)");
            return Ok(());
        };

        if !is_process_alive(pid_file.pid) {
            PidFile::remove(&self.data_dir);
            println!(
                "Daemon is not running (stale PID file for PID {} removed)",
                pid_file.pid
            );
            return Ok(());
        }

        // Try to get status via IPC
        let socket_path = self.socket_path();
        let rt = tokio::runtime::Runtime::new()?;
        match rt.block_on(async {
            let client = IpcClient::new(socket_path);
            client.send(DaemonCommand::Status).await
        }) {
            Ok(DaemonResponse::Ok(info)) => {
                let started = chrono::DateTime::from_timestamp(pid_file.started_at as i64, 0)
                    .map(|dt| dt.format("%Y-%m-%d %H:%M:%S UTC").to_string())
                    .unwrap_or_else(|| "?".into());
                println!("Daemon is running (PID {})", pid_file.pid);
                println!("  Started: {}", started);
                println!("  Socket:  {}", pid_file.socket_name);
                println!("  Info:    {}", info);
            }
            Ok(other) => {
                println!(
                    "Daemon is running (PID {}) but returned unexpected response: {:?}",
                    pid_file.pid, other
                );
            }
            Err(e) => {
                println!(
                    "Daemon process (PID {}) is alive but not responding to IPC: {}",
                    pid_file.pid, e
                );
            }
        }

        Ok(())
    }

    // ── §7.3 Find running daemon ──────────────────────────────────

    /// Find a running daemon by reading PID file and checking liveness.
    pub fn find_running(data_dir: &Path) -> Option<DaemonInfo> {
        Self::find_running_inner(data_dir).ok().flatten()
    }

    fn find_running_inner(data_dir: &Path) -> Result<Option<DaemonInfo>> {
        let Some(pf) = PidFile::read(data_dir) else {
            return Ok(None);
        };

        if !is_process_alive(pf.pid) {
            PidFile::remove(data_dir);
            return Ok(None);
        }

        Ok(Some(DaemonInfo {
            pid: pf.pid,
            started_at: pf.started_at,
            socket_path: data_dir.join(&pf.socket_name),
        }))
    }
}

// ─── Force kill (cross-platform) ──────────────────────────────────

fn force_kill(pid: u32) {
    #[cfg(unix)]
    {
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{BOOL, FALSE};
        use windows_sys::Win32::System::Threading::{OpenProcess, TerminateProcess};
        const PROCESS_TERMINATE: u32 = 0x0001;
        let handle = unsafe { OpenProcess(PROCESS_TERMINATE, FALSE, pid) };
        if handle != 0 as *mut _ {
            unsafe { TerminateProcess(handle, 1) };
            unsafe { windows_sys::Win32::Foundation::CloseHandle(handle) };
        }
    }
}

// ─── Tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn pid_file_round_trip() {
        let dir = TempDir::new().unwrap();
        PidFile::write(dir.path(), 12345, "ga-daemon.sock").unwrap();
        let pf = PidFile::read(dir.path()).unwrap();
        assert_eq!(pf.pid, 12345);
        assert_eq!(pf.socket_name, "ga-daemon.sock");
    }

    #[test]
    fn pid_file_remove() {
        let dir = TempDir::new().unwrap();
        PidFile::write(dir.path(), 12345, "ga-daemon.sock").unwrap();
        PidFile::remove(dir.path());
        assert!(PidFile::read(dir.path()).is_none());
    }

    #[test]
    fn is_process_alive_dead() {
        // Use a PID that definitely doesn't exist
        assert!(!is_process_alive(999999999));
    }

    #[test]
    fn find_running_no_pid_file() {
        let dir = TempDir::new().unwrap();
        assert!(Daemon::find_running(dir.path()).is_none());
    }

    #[test]
    fn daemon_new_creates_dirs() {
        let dir = TempDir::new().unwrap();
        let root = dir.path().join("myroot");
        let daemon = Daemon::new(root.to_str().unwrap()).unwrap();
        assert!(daemon.data_dir.exists());
    }

    #[test]
    fn print_status_no_daemon() {
        let dir = TempDir::new().unwrap();
        let daemon = Daemon {
            ga_root: dir.path().to_path_buf(),
            data_dir: dir.path().join(".ga").join("data"),
        };
        fs::create_dir_all(&daemon.data_dir).unwrap();
        assert!(daemon.print_status().is_ok());
    }
}
