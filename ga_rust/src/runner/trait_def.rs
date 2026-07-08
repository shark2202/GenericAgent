use anyhow::Result;
use async_trait::async_trait;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunnerContext {
    pub session_id: String,
    pub project_path: Option<String>,
    pub env: Vec<(String, String)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunnerOutput {
    pub session_id: String,
    pub status: RunnerStatus,
    pub pid: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum RunnerStatus {
    Starting,
    Running,
    Stopped,
    Failed(String),
}

#[async_trait]
pub trait Runner: Send + Sync {
    fn kind(&self) -> &str;
    async fn start(&self, ctx: &RunnerContext) -> Result<RunnerOutput>;
    async fn stop(&self, session_id: &str) -> Result<()>;
    async fn status(&self, session_id: &str) -> Result<RunnerStatus>;
}
