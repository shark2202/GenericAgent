use anyhow::Result;
use std::path::PathBuf;

use crate::runner::{Runner, RunnerContext, RunnerRegistry};
use crate::store::Store;

pub struct Orchestrator {
    store: Store,
    registry: RunnerRegistry,
}

impl Orchestrator {
    pub fn new(ga_root: &str) -> Result<Self> {
        let store = Store::new(ga_root)?;
        let registry = RunnerRegistry::new(PathBuf::from(ga_root));
        Ok(Self { store, registry })
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

    // === Daemon ===
    pub fn daemon(&self, foreground: bool) -> Result<String> {
        if foreground {
            Ok("Daemon running in foreground (not yet implemented)".to_string())
        } else {
            Ok("Daemon started in background (not yet implemented)".to_string())
        }
    }
}
