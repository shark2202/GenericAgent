use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub ga_root: PathBuf,
    pub data_dir: PathBuf,
    pub default_runner: String,
    pub llm_model: Option<String>,
}

impl AppConfig {
    pub fn from_ga_root(ga_root: &str) -> Result<Self> {
        let root = PathBuf::from(ga_root);
        if !root.exists() {
            anyhow::bail!("GA root directory does not exist: {:?}", root);
        }
        let data_dir = root.join(".ga_data");
        Ok(Self {
            ga_root: root,
            data_dir,
            default_runner: "ga".to_string(),
            llm_model: None,
        })
    }

    pub fn discover_ga_root() -> Result<PathBuf> {
        // Try current directory first
        let cwd = std::env::current_dir()?;
        if cwd.join("agentmain.py").exists() {
            return Ok(cwd);
        }
        // Try GA_ROOT env var
        if let Ok(root) = std::env::var("GA_ROOT") {
            let p = PathBuf::from(&root);
            if p.join("agentmain.py").exists() {
                return Ok(p);
            }
        }
        // Try parent directories
        let mut dir = cwd;
        while let Some(parent) = dir.parent() {
            if parent.join("agentmain.py").exists() {
                return Ok(parent.to_path_buf());
            }
            dir = parent.to_path_buf();
        }
        anyhow::bail!(
            "Cannot locate GA runtime. Set GA_ROOT env var or run from GA project directory.\n\
             Hint: install GA from https://github.com/user/genericagent or point to an existing install."
        )
    }
}
