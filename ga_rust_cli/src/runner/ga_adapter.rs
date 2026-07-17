use anyhow::{Result, Context};
use async_trait::async_trait;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;

use super::trait_def::{Runner, RunnerContext, RunnerOutput, RunnerStatus};

/// GA Python engine runner adapter
pub struct GaRunner {
    ga_root: PathBuf,
    /// Track session_id -> PID for stop/status operations
    sessions: Mutex<HashMap<String, u32>>,
}

impl GaRunner {
    pub fn new(ga_root: PathBuf) -> Self {
        Self {
            ga_root,
            sessions: Mutex::new(HashMap::new()),
        }
    }

    fn find_python(&self) -> Result<String> {
        // Try .venv first, then system python
        let venv_python = self.ga_root.join(".venv").join("Scripts").join("python.exe");
        if venv_python.exists() {
            return Ok(venv_python.to_string_lossy().to_string());
        }
        let venv_python_unix = self.ga_root.join(".venv").join("bin").join("python");
        if venv_python_unix.exists() {
            return Ok(venv_python_unix.to_string_lossy().to_string());
        }
        // System python
        Ok("python".to_string())
    }

    fn find_agentmain(&self) -> Result<String> {
        let agentmain = self.ga_root.join("agentmain.py");
        if agentmain.exists() {
            return Ok(agentmain.to_string_lossy().to_string());
        }
        anyhow::bail!("agentmain.py not found in GA root: {:?}", self.ga_root)
    }
}

#[async_trait]
impl Runner for GaRunner {
    fn kind(&self) -> &str {
        "ga"
    }

    async fn start(&self, ctx: &RunnerContext) -> Result<RunnerOutput> {
        let python = self.find_python().context("Python not found - run GA setup first")?;
        let agentmain = self.find_agentmain().context("GA runtime not found")?;

        let mut cmd = Command::new(&python);
        cmd.arg(&agentmain)
            .arg("--session-id").arg(&ctx.session_id)
            .current_dir(&self.ga_root);

        for (k, v) in &ctx.env {
            cmd.env(k, v);
        }

        let child = cmd.spawn().context("Failed to start GA runner")?;
        let pid = child.id();

        // Track session PID for stop/status
        if let Ok(mut sessions) = self.sessions.lock() {
            sessions.insert(ctx.session_id.clone(), pid);
        }

        Ok(RunnerOutput {
            session_id: ctx.session_id.clone(),
            status: RunnerStatus::Running,
            pid: Some(pid),
        })
    }

    async fn stop(&self, session_id: &str) -> Result<()> {
        let pid = {
            let mut sessions = self.sessions.lock().map_err(|e| anyhow::anyhow!("lock poisoned: {}", e))?;
            sessions.remove(session_id)
        };

        if let Some(pid) = pid {
            #[cfg(windows)]
            {
                let exit = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/F"])
                    .output()
                    .context("Failed to run taskkill")?;
                if !exit.status.success() {
                    tracing::warn!("taskkill for PID {} returned non-zero: {}", pid, String::from_utf8_lossy(&exit.stderr));
                }
            }
            #[cfg(not(windows))]
            {
                unsafe {
                    libc::kill(pid as i32, libc::SIGTERM);
                }
            }
        } else {
            tracing::warn!("No PID found for session {}, may have already stopped", session_id);
        }
        Ok(())
    }

    async fn status(&self, session_id: &str) -> Result<RunnerStatus> {
        let pid = {
            let sessions = self.sessions.lock().map_err(|e| anyhow::anyhow!("lock poisoned: {}", e))?;
            sessions.get(session_id).copied()
        };

        if let Some(pid) = pid {
            // Check if process is still alive
            #[cfg(windows)]
            {
                let output = Command::new("tasklist")
                    .args(["/FI", &format!("PID eq {}", pid), "/NH"])
                    .output()
                    .context("Failed to run tasklist")?;
                let stdout = String::from_utf8_lossy(&output.stdout);
                if stdout.contains(&pid.to_string()) {
                    Ok(RunnerStatus::Running)
                } else {
                    // Process exited, clean up
                    let mut sessions = self.sessions.lock().map_err(|e| anyhow::anyhow!("lock poisoned: {}", e))?;
                    sessions.remove(session_id);
                    Ok(RunnerStatus::Stopped)
                }
            }
            #[cfg(not(windows))]
            {
                // On Unix, check with kill(pid, 0)
                let alive = unsafe { libc::kill(pid as i32, 0) == 0 };
                if alive {
                    Ok(RunnerStatus::Running)
                } else {
                    let mut sessions = self.sessions.lock().map_err(|e| anyhow::anyhow!("lock poisoned: {}", e))?;
                    sessions.remove(session_id);
                    Ok(RunnerStatus::Stopped)
                }
            }
        } else {
            Ok(RunnerStatus::Stopped)
        }
    }
}
