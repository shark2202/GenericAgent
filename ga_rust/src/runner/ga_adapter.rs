use anyhow::{Result, Context};
use async_trait::async_trait;
use std::path::PathBuf;
use std::process::Command;

use super::trait_def::{Runner, RunnerContext, RunnerOutput, RunnerStatus};

/// GA Python engine runner adapter
pub struct GaRunner {
    ga_root: PathBuf,
}

impl GaRunner {
    pub fn new(ga_root: PathBuf) -> Self {
        Self { ga_root }
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

        Ok(RunnerOutput {
            session_id: ctx.session_id.clone(),
            status: RunnerStatus::Running,
            pid: Some(pid),
        })
    }

    async fn stop(&self, _session_id: &str) -> Result<()> {
        // TODO: Send SIGTERM / taskkill
        Ok(())
    }

    async fn status(&self, _session_id: &str) -> Result<RunnerStatus> {
        // TODO: Check process status via PID from store
        Ok(RunnerStatus::Running)
    }
}
