use anyhow::Result;
use clap::{Parser, Subcommand};

use crate::core::Orchestrator;

/// GenericAgent Core Daemon + CLI
#[derive(Parser, Debug)]
#[command(name = "ga", version, about = "GenericAgent orchestration core")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<Commands>,

    /// Path to GA root directory
    #[arg(long, env = "GA_ROOT")]
    pub root: Option<String>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Show system status
    Status,
    /// Session management
    #[command(subcommand)]
    Sessions(SessionCommands),
    /// Session operations (alias for sessions)
    #[command(subcommand)]
    Session(SessionCommands),
    /// Project management
    #[command(subcommand)]
    Projects(ProjectCommands),
    /// Project operations (alias for projects)
    #[command(subcommand)]
    Project(ProjectCommands),
    /// LLM configuration
    #[command(subcommand)]
    Llm(LlmCommands),
    /// Start the core daemon
    Daemon {
        /// Run in foreground (don't detach)
        #[arg(short, long)]
        foreground: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum SessionCommands {
    /// List all sessions
    List,
    /// Create a new session
    New {
        /// Runner kind (ga, opencode, claude-code, codex)
        #[arg(long, default_value = "ga")]
        runner: String,
        /// Project to associate
        #[arg(long)]
        project: Option<String>,
    },
    /// Watch a session's output
    Watch {
        /// Session ID
        session_id: String,
    },
    /// Archive a session
    Archive {
        /// Session ID
        session_id: String,
    },
}

#[derive(Subcommand, Debug)]
pub enum ProjectCommands {
    /// List all projects
    List,
    /// Create a new project
    Create {
        /// Project name
        name: String,
        /// Project path
        #[arg(long)]
        path: Option<String>,
    },
    /// Follow a project (set active)
    Follow {
        /// Project name
        name: String,
    },
}

#[derive(Subcommand, Debug)]
pub enum LlmCommands {
    /// Set the active LLM model
    Set {
        /// Model identifier
        model: String,
    },
}

impl Cli {
    pub fn execute(self) -> Result<()> {
        let root = self.root.unwrap_or_else(|| {
            dirs::home_dir()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|| ".".to_string())
        });

        let core = Orchestrator::new(&root)?;

        let output = match self.command.unwrap_or(Commands::Status) {
            Commands::Status => core.status(),
            Commands::Sessions(cmd) | Commands::Session(cmd) => match cmd {
                SessionCommands::List => core.sessions_list(),
                SessionCommands::New { runner, project } => core.session_new(&runner, project.as_deref()),
                SessionCommands::Watch { session_id } => core.session_watch(&session_id),
                SessionCommands::Archive { session_id } => core.session_archive(&session_id),
            },
            Commands::Projects(cmd) | Commands::Project(cmd) => match cmd {
                ProjectCommands::List => core.projects_list(),
                ProjectCommands::Create { name, path } => core.project_create(&name, path.as_deref()),
                ProjectCommands::Follow { name } => core.project_follow(&name),
            },
            Commands::Llm(LlmCommands::Set { model }) => core.llm_set(&model),
            Commands::Daemon { foreground } => core.daemon(foreground),
        }?;

        println!("{}", output);
        Ok(())
    }
}
