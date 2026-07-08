use anyhow::Result;
use std::path::PathBuf;

use crate::runner::{Runner, RunnerContext, RunnerRegistry};
use crate::store::Store;
use crate::store::{Approval, Origin, RiskLevel};
use crate::core::approval::{ApprovalPolicy, CallInfo, CallDecision};

pub struct Orchestrator {
    store: Store,
    registry: RunnerRegistry,
    approval_policy: ApprovalPolicy,
}

impl Orchestrator {
    pub fn new(ga_root: &str) -> Result<Self> {
        let store = Store::new(ga_root)?;
        let registry = RunnerRegistry::new(PathBuf::from(ga_root));

        // Load approval policy from config
        let yolo = store.config_get("approval_yolo")
            .ok().flatten()
            .map(|v| v == "true" || v == "1")
            .unwrap_or(false);

        let allowlist_str = store.config_get("approval_allowlist")
            .ok().flatten()
            .unwrap_or_default();
        let allowlist: Vec<String> = if allowlist_str.is_empty() {
            vec![]
        } else {
            allowlist_str.split(',').map(|s| s.trim().to_string()).collect()
        };

        let approval_policy = ApprovalPolicy::from_config(yolo, allowlist, std::collections::HashMap::new());

        Ok(Self { store, registry, approval_policy })
    }

    // === Status ===
    pub fn status(&self) -> Result<String> {
        let sessions = self.store.sessions_list()?;
        let active: Vec<_> = sessions.iter().filter(|s| s.status == "active").collect();
        let projects = self.store.projects_list()?;
        let config_model = self.store.config_get("llm_model")?;

        Ok(format!(
            "ga-core v0.1.0\n\
             Sessions: {} active / {} total\n\
             Projects: {}\n\
             Runners: {}\n\
             LLM Model: {}",
            active.len(),
            sessions.len(),
            projects.len(),
            self.registry.available_kinds().join(", "),
            config_model.unwrap_or_else(|| "(not set)".to_string()),
        ))
    }

    // === Sessions ===
    pub fn session_new(&self, runner: &str, project: Option<&str>, command: Option<&str>) -> Result<String> {
        let session = self.store.session_create(runner, project)?;

        // Start the runner
        let runner_impl = self.registry.get(runner)?;
        let ctx = RunnerContext {
            session_id: session.id.clone(),
            project_path: project.map(String::from),
            env: vec![],
            command: command.map(String::from),
        };

        let rt = tokio::runtime::Runtime::new()?;
        let output = rt.block_on(runner_impl.start(&ctx))?;

        // Update PID in store
        if let Some(pid) = output.pid {
            let conn = self.store.conn()?;
            conn.execute(
                "UPDATE sessions SET pid=?1, updated_at=?2 WHERE id=?3",
                rusqlite::params![pid as i64, chrono::Utc::now().to_rfc3339(), session.id],
            )?;
        }

        Ok(format!("Session {} started (runner: {}, pid: {:?})",
            session.id, runner, output.pid))
    }

    pub fn sessions_list(&self) -> Result<String> {
        let sessions = self.store.sessions_list()?;
        if sessions.is_empty() {
            return Ok("No sessions.".to_string());
        }
        let mut out = String::new();
        for s in &sessions {
            out.push_str(&format!(
                "{:<36} {:<10} {:<10} {}\n",
                s.id, s.runner_kind, s.status, s.created_at
            ));
        }
        Ok(out)
    }

    pub fn session_archive(&self, session_id: &str) -> Result<String> {
        self.store.session_archive(session_id)?;
        Ok(format!("Session {} archived.", session_id))
    }

    pub fn session_watch(&self, session_id: &str) -> Result<String> {
        // TODO: Stream events from PTY
        Ok(format!("Watching session {} (streaming not yet implemented)", session_id))
    }

    // === Projects ===
    pub fn projects_list(&self) -> Result<String> {
        let projects = self.store.projects_list()?;
        if projects.is_empty() {
            return Ok("No projects.".to_string());
        }
        let mut out = String::new();
        for p in &projects {
            out.push_str(&format!("{:<36} {:<20} {}\n", p.id, p.name, p.path.as_deref().unwrap_or("-")));
        }
        Ok(out)
    }

    pub fn project_create(&self, name: &str, path: Option<&str>) -> Result<String> {
        let project = self.store.project_create(name, path)?;
        Ok(format!("Project '{}' created (id: {})", project.name, project.id))
    }

    pub fn project_follow(&self, name: &str) -> Result<String> {
        self.store.project_follow(name)?;
        Ok(format!("Now following project '{}'", name))
    }

    // === LLM ===
    pub fn llm_set(&self, model: &str) -> Result<String> {
        self.store.config_set("llm_model", model)?;
        Ok(format!("LLM model set to '{}'", model))
    }

    // === Approval (§8.3) ===

    /// Pre-call check: classify the call, decide if it needs approval.
    /// If approval is needed, create a pending approval record and return CallDecision::NeedsApproval.
    /// If YOLO mode or auto-approved, return CallDecision::Approved.
    pub fn pre_call_check(&self, session_id: &str, call_info: &CallInfo) -> Result<CallDecision> {
        let decision = self.approval_policy.classify(call_info);

        match &decision {
            CallDecision::RequiresApproval { risk, .. } => {
                let origin = match (&call_info.tool_name, &call_info.command) {
                    (Some(_), _) => Origin::Api,
                    (None, Some(_)) => Origin::Cli,
                    _ => Origin::Auto,
                };
                let _approval = self.store.approval_create(
                    session_id,
                    call_info.tool_name.as_deref(),
                    call_info.command.as_deref(),
                    risk,
                    &origin,
                )?;
            }
            CallDecision::AutoApproved { .. } => {}
        }

        Ok(decision)
    }

    /// List pending approvals, optionally filtered by session.
    pub fn approval_list(&self, session_id: Option<&str>) -> Result<String> {
        let approvals = self.store.approvals_pending(session_id)?;
        if approvals.is_empty() {
            return Ok("No pending approvals.".to_string());
        }
        let mut out = String::new();
        for a in &approvals {
            let kind = match (&a.tool_name, &a.command) {
                (Some(t), _) => format!("tool:{}", t),
                (None, Some(c)) => format!("cmd:{}", c),
                (None, None) => "unknown".to_string(),
            };
            out.push_str(&format!(
                "#{:<4} session:{:<8} risk:{:<6} {} [{}]\n",
                a.id, a.session_id, a.risk_level.as_str(), kind, a.origin.as_str()
            ));
        }
        Ok(out)
    }

    /// Approve a pending approval.
    pub fn approval_approve(&self, approval_id: i64, resolver_origin: &Origin) -> Result<String> {
        self.store.approval_resolve(approval_id, "approved", resolver_origin)?;
        Ok(format!("Approval #{} approved.", approval_id))
    }

    /// Reject a pending approval.
    pub fn approval_reject(&self, approval_id: i64, resolver_origin: &Origin) -> Result<String> {
        self.store.approval_resolve(approval_id, "rejected", resolver_origin)?;
        Ok(format!("Approval #{} rejected.", approval_id))
    }

    /// View details of a specific approval.
    pub fn approval_view(&self, approval_id: i64) -> Result<String> {
        let approvals = self.store.approvals_pending(None)?;
        let a = approvals.iter().find(|a| a.id == approval_id)
            .ok_or_else(|| anyhow::anyhow!("Approval #{} not found or not pending", approval_id))?;

        Ok(format!(
            "Approval #{}\n  Session: {}\n  Tool: {}\n  Command: {}\n  Risk: {}\n  Status: {}\n  Origin: {}\n  Created: {}",
            a.id,
            a.session_id,
            a.tool_name.as_deref().unwrap_or("-"),
            a.command.as_deref().unwrap_or("-"),
            a.risk_level.as_str(),
            a.status,
            a.origin.as_str(),
            a.created_at,
        ))
    }

    // === Approval Policy (§8.5) ===

    /// Set YOLO mode (auto-approve all).
    pub fn approval_set_yolo(&self, enabled: bool) -> Result<String> {
        self.store.config_set("approval_yolo", if enabled { "true" } else { "false" })?;
        Ok(format!("YOLO mode {}", if enabled { "enabled" } else { "disabled" }))
    }

    /// Set allowlist (comma-separated tool names).
    pub fn approval_set_allowlist(&self, tools: &[String]) -> Result<String> {
        self.store.config_set("approval_allowlist", &tools.join(","))?;
        Ok(format!("Allowlist set: {}", tools.join(", ")))
    }

    /// Show current allowlist.
    pub fn approval_show_allowlist(&self) -> Result<String> {
        let allowlist = self.store.config_get("approval_allowlist")
            .ok().flatten()
            .unwrap_or_default();
        if allowlist.is_empty() {
            Ok("Allowlist is empty.".to_string())
        } else {
            Ok(format!("Allowlist: {}", allowlist))
        }
    }

    /// Expose store for daemon module
    pub fn store(&self) -> &Store {
        &self.store
    }

    /// Expose registry for daemon module
    pub fn registry(&self) -> &RunnerRegistry {
        &self.registry
    }

    /// Expose approval policy for daemon module
    pub fn approval_policy(&self) -> &ApprovalPolicy {
        &self.approval_policy
    }

    // === Daemon ===
    pub fn daemon_start(&self, foreground: bool) -> Result<String> {
        use crate::core::Daemon;
        let daemon = Daemon::new(&self.store.root().to_string_lossy())?;
        daemon.start(foreground)?;
        Ok(if foreground {
            "Daemon running in foreground".to_string()
        } else {
            "Daemon started in background".to_string()
        })
    }

    pub fn daemon_stop(&self) -> Result<String> {
        use crate::core::Daemon;
        let daemon = Daemon::new(&self.store.root().to_string_lossy())?;
        daemon.stop()?;
        Ok("Daemon stopped".to_string())
    }

    pub fn daemon_status(&self) -> Result<String> {
        use crate::core::PidFile;
        let data_dir = self.store.root().join(".ga").join("data");
        let Some(pid_file) = PidFile::read(&data_dir) else {
            return Ok("Daemon: not running".to_string());
        };
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let uptime = now.saturating_sub(pid_file.started_at);
        Ok(format!("Daemon: PID={}, socket={}, uptime={}s",
            pid_file.pid, pid_file.socket_name, uptime))
    }

    /// Check approval for a runner call; returns (approved, reason)
    pub fn check_approval(&self, info: &CallInfo) -> (bool, Option<String>) {
        match self.approval_policy.evaluate(info) {
            CallDecision::AutoApproved { risk, .. } => {
                (true, Some(format!("auto-approved ({:?})", risk)))
            }
            CallDecision::RequiresApproval { risk, description, .. } => {
                (false, Some(format!("requires approval ({:?}): {}", risk,
                    description.as_deref().unwrap_or("(no description)"))))
            }
        }
    }
}
