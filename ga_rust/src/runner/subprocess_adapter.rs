use anyhow::{Context, Result};
use async_trait::async_trait;
use std::collections::HashMap;
use std::process::Command;

use super::trait_def::{Runner, RunnerContext, RunnerOutput, RunnerStatus};

/// Generic subprocess runner — spawns any CLI command as an agent.
/// This proves the Runner trait is agent-agnostic: any executable
/// (opencode, claude-code, codex, or a plain script) can be a runner.
pub struct SubprocessRunner {
    command: String,
    args: Vec<String>,
    working_dir: Option<String>,
}

impl SubprocessRunner {
    /// Create from a raw command string (e.g. "opencode --agent")
    pub fn from_command(command: &str) -> Self {
        let parts: Vec<&str> = command.split_whitespace().collect();
        if parts.is_empty() {
            return Self {
                command: String::new(),
                args: Vec::new(),
                working_dir: None,
            };
        }
        Self {
            command: parts[0].to_string(),
            args: parts[1..].iter().map(|s| s.to_string()).collect(),
            working_dir: None,
        }
    }

    /// Create with explicit command, args, and optional working directory
    pub fn new(command: String, args: Vec<String>, working_dir: Option<String>) -> Self {
        Self {
            command,
            args,
            working_dir,
        }
    }
}

#[async_trait]
impl Runner for SubprocessRunner {
    fn kind(&self) -> &str {
        "subprocess"
    }

    async fn start(&self, ctx: &RunnerContext) -> Result<RunnerOutput> {
        // Resolve effective command: self.command > ctx.command > error
        let (effective_cmd, effective_args) = if !self.command.is_empty() {
            (self.command.clone(), self.args.clone())
        } else if let Some(ref cmd) = ctx.command {
            let parts: Vec<&str> = cmd.split_whitespace().collect();
            if parts.is_empty() {
                anyhow::bail!("SubprocessRunner: no command configured (neither at construction nor via --command)");
            }
            let bin = parts[0].to_string();
            let args = parts[1..].iter().map(|s| s.to_string()).collect();
            (bin, args)
        } else {
            anyhow::bail!("SubprocessRunner: no command configured (neither at construction nor via --command)");
        };

        let mut cmd = Command::new(&effective_cmd);
        cmd.args(&effective_args);

        // Set working directory: explicit > context project_path > current dir
        if let Some(ref wd) = self.working_dir {
            cmd.current_dir(wd);
        } else if let Some(ref pp) = ctx.project_path {
            cmd.current_dir(pp);
        }

        // Inject session-id as environment variable so the child can identify itself
        cmd.env("GA_SESSION_ID", &ctx.session_id);

        // Pass through context environment
        for (k, v) in &ctx.env {
            cmd.env(k, v);
        }

        let child = cmd
            .spawn()
            .with_context(|| format!("Failed to spawn subprocess: {}", effective_cmd))?;
        let pid = child.id();

        Ok(RunnerOutput {
            session_id: ctx.session_id.clone(),
            status: RunnerStatus::Running,
            pid: Some(pid),
        })
    }

    async fn stop(&self, _session_id: &str) -> Result<()> {
        // TODO: Send SIGTERM / taskkill via stored PID
        Ok(())
    }

    async fn status(&self, _session_id: &str) -> Result<RunnerStatus> {
        // TODO: Check process status via PID from store
        Ok(RunnerStatus::Running)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_from_command_single() {
        let r = SubprocessRunner::from_command("echo");
        assert_eq!(r.command, "echo");
        assert!(r.args.is_empty());
    }

    #[test]
    fn test_from_command_with_args() {
        let r = SubprocessRunner::from_command("opencode --agent --model gpt4");
        assert_eq!(r.command, "opencode");
        assert_eq!(r.args, vec!["--agent", "--model", "gpt4"]);
    }

    #[test]
    fn test_from_command_empty() {
        let r = SubprocessRunner::from_command("");
        assert!(r.command.is_empty());
        assert!(r.args.is_empty());
    }

    #[test]
    fn test_kind() {
        let r = SubprocessRunner::from_command("echo");
        assert_eq!(r.kind(), "subprocess");
    }

    #[tokio::test]
    async fn test_start_empty_command_fails() {
        let r = SubprocessRunner::from_command("");
        let ctx = RunnerContext {
            session_id: "test".to_string(),
            project_path: None,
            env: Vec::new(),
            command: None,
        };
        let result = r.start(&ctx).await;
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("no command"));
    }

    #[tokio::test]
    async fn test_start_echo_succeeds() {
        let r = SubprocessRunner::from_command("cmd");
        let r = SubprocessRunner::new(
            "cmd".to_string(),
            vec!["/C".to_string(), "echo".to_string(), "hello".to_string()],
            None,
        );
        let ctx = RunnerContext {
            session_id: "test-echo".to_string(),
            project_path: None,
            env: Vec::new(),
            command: None,
        };
        let result = r.start(&ctx).await;
        assert!(result.is_ok());
        let output = result.unwrap();
        assert_eq!(output.session_id, "test-echo");
        assert_eq!(output.status, RunnerStatus::Running);
        assert!(output.pid.is_some());
    }

    #[tokio::test]
    async fn test_start_via_ctx_command() {
        // Empty runner but ctx.command provided → should work
        let r = SubprocessRunner::from_command("");
        let ctx = RunnerContext {
            session_id: "test-ctx-cmd".to_string(),
            project_path: None,
            env: Vec::new(),
            command: Some("cmd /C echo hello".to_string()),
        };
        let result = r.start(&ctx).await;
        assert!(result.is_ok());
        let output = result.unwrap();
        assert_eq!(output.session_id, "test-ctx-cmd");
    }
}
