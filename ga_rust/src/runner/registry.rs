use anyhow::Result;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use super::trait_def::Runner;
use super::ga_adapter::GaRunner;
use super::subprocess_adapter::SubprocessRunner;

pub struct RunnerRegistry {
    runners: HashMap<String, Arc<dyn Runner>>,
}

impl RunnerRegistry {
    pub fn new(ga_root: PathBuf) -> Self {
        let mut runners: HashMap<String, Arc<dyn Runner>> = HashMap::new();

        // Register built-in runners
        let ga_runner = Arc::new(GaRunner::new(ga_root));
        runners.insert("ga".to_string(), ga_runner);

        // Subprocess runner — agent-agnostic, spawns any CLI command
        let subprocess_runner = Arc::new(SubprocessRunner::from_command(""));
        runners.insert("subprocess".to_string(), subprocess_runner);

        Self { runners }
    }

    pub fn get(&self, kind: &str) -> Result<Arc<dyn Runner>> {
        self.runners
            .get(kind)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!(
                "Unknown runner kind '{}'. Available: {}. Install the agent binary and register it.",
                kind,
                self.runners.keys().cloned().collect::<Vec<_>>().join(", ")
            ))
    }

    pub fn available_kinds(&self) -> Vec<&str> {
        self.runners.keys().map(|s| s.as_str()).collect()
    }

    pub fn register(&mut self, kind: String, runner: Arc<dyn Runner>) {
        self.runners.insert(kind, runner);
    }
}
