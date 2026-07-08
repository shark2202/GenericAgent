use anyhow::Result;
use clap::{Parser, Subcommand};

use crate::core::Orchestrator;
use crate::core::Daemon;
use crate::store::Origin;

/// GenericAgent Core Daemon + CLI
#[derive(Parser, Debug)]
#[command(name = "ga", version, about = "GenericAgent orchestration core")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<Commands>,

    /// Path to GA root directory
    #[arg(long, env = "GA_ROOT")]
    pub root: Option<String>,

    /// Internal: run daemon in foreground (used by spawned daemon process)
    #[arg(long, hide = true)]
    pub daemon_fg: bool,
}

#[derive(Subcommand, Debug, Clone)]
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
        /// Detach and run as background daemon
        #[arg(short, long)]
        detach: bool,
    },
    /// Stop the running daemon
    DaemonStop,
    /// Show daemon status
    DaemonStatus,
    /// Approval management
    #[command(subcommand)]
    Approval(ApprovalCommands),
}

#[derive(Subcommand, Debug, Clone)]
pub enum SessionCommands {
    /// List all sessions
    List,
    /// Create a new session
    New {
        /// Runner kind (ga, opencode, claude-code, codex, subprocess)
        #[arg(long, default_value = "ga")]
        runner: String,
        /// Project to associate
        #[arg(long)]
        project: Option<String>,
        /// Command for subprocess runner (e.g. "python -i" or "bash")
        /// Only used when --runner=subprocess
        #[arg(long)]
        command: Option<String>,
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

#[derive(Subcommand, Debug, Clone)]
pub enum ProjectCommands {
    /// List all projects
    List,
    /// Create a new project
    Create {
        /// Project name
        name: String,
        /// Project path
        path: Option<String>,
    },
    /// Follow a project's active session
    Follow {
        /// Project name
        name: String,
    },
}

#[derive(Subcommand, Debug, Clone)]
pub enum LlmCommands {
    /// Set the active LLM model
    Set {
        /// Model identifier (e.g. "gpt-4o", "claude-3.5-sonnet")
        model: String,
    },
}

#[derive(Subcommand, Debug, Clone)]
pub enum ApprovalCommands {
    /// List pending approvals
    List {
        /// Filter by session ID
        #[arg(long)]
        session: Option<String>,
    },
    /// Approve a pending request
    Approve {
        /// Approval ID
        id: i64,
        /// Resolver origin (cli, daemon, web)
        #[arg(long, default_value = "cli")]
        origin: String,
    },
    /// Reject a pending request
    Reject {
        /// Approval ID
        id: i64,
        /// Resolver origin (cli, daemon, web)
        #[arg(long, default_value = "cli")]
        origin: String,
    },
    /// View approval details
    View {
        /// Approval ID
        id: i64,
    },
    /// Enable or disable YOLO mode (auto-approve all)
    Yolo {
        /// Enable YOLO mode
        #[arg(long)]
        enable: bool,
        /// Disable YOLO mode
        #[arg(long, conflicts_with = "enable")]
        disable: bool,
    },
    /// Manage allowlist (tools that bypass approval)
    Allowlist {
        /// Set allowlist (comma-separated tool names)
        #[arg(long)]
        set: Option<String>,
        /// Show current allowlist
        #[arg(long, conflicts_with = "set")]
        show: bool,
    },
}

impl Cli {
    pub fn execute(&self) -> Result<()> {
        let root = self.root.clone().unwrap_or_else(|| ".".to_string());

        // Internal flag: spawned daemon process runs in foreground
        if self.daemon_fg {
            let daemon = Daemon::new(&root)?;
            return daemon.start(true);
        }

        let core = Orchestrator::new(&root)?;

        match self.command.clone().unwrap_or(Commands::Status) {
            Commands::Status => {
                let output = core.status()?;
                println!("{}", output);
            }
            Commands::Sessions(cmd) | Commands::Session(cmd) => match cmd {
                SessionCommands::List => {
                    let output = core.sessions_list()?;
                    println!("{}", output);
                }
                SessionCommands::New { runner, project, command } => {
                    let output = core.session_new(&runner, project.as_deref(), command.as_deref())?;
                    println!("{}", output);
                }
                SessionCommands::Watch { session_id } => {
                    let output = core.session_watch(&session_id)?;
                    println!("{}", output);
                }
                SessionCommands::Archive { session_id } => {
                    let output = core.session_archive(&session_id)?;
                    println!("{}", output);
                }
            },
            Commands::Projects(cmd) | Commands::Project(cmd) => match cmd {
                ProjectCommands::List => {
                    let output = core.projects_list()?;
                    println!("{}", output);
                }
                ProjectCommands::Create { name, path } => {
                    let output = core.project_create(&name, path.as_deref())?;
                    println!("{}", output);
                }
                ProjectCommands::Follow { name } => {
                    let output = core.project_follow(&name)?;
                    println!("{}", output);
                }
            },
            Commands::Llm(LlmCommands::Set { model }) => {
                let output = core.llm_set(&model)?;
                println!("{}", output);
            }
            Commands::Daemon { foreground, detach } => {
                let daemon = Daemon::new(&root)?;
                // --detach means background (not foreground); default is foreground
                let fg = foreground || !detach;
                daemon.start(fg)?;
            }
            Commands::DaemonStop => {
                let daemon = Daemon::new(&root)?;
                daemon.stop()?;
            }
            Commands::DaemonStatus => {
                let daemon = Daemon::new(&root)?;
                daemon.print_status()?;
            }
            Commands::Approval(cmd) => match cmd {
                ApprovalCommands::List { session } => {
                    let output = core.approval_list(session.as_deref())?;
                    println!("{}", output);
                }
                ApprovalCommands::Approve { id, origin } => {
                    let resolver = match origin.as_str() {
                        "api" => Origin::Api,
                        "tui" => Origin::Tui,
                        "yolo" => Origin::Yolo,
                        "auto" => Origin::Auto,
                        _ => Origin::Cli,
                    };
                    let output = core.approval_approve(id, &resolver)?;
                    println!("{}", output);
                }
                ApprovalCommands::Reject { id, origin } => {
                    let resolver = match origin.as_str() {
                        "api" => Origin::Api,
                        "tui" => Origin::Tui,
                        "yolo" => Origin::Yolo,
                        "auto" => Origin::Auto,
                        _ => Origin::Cli,
                    };
                    let output = core.approval_reject(id, &resolver)?;
                    println!("{}", output);
                }
                ApprovalCommands::View { id } => {
                    let output = core.approval_view(id)?;
                    println!("{}", output);
                }
                ApprovalCommands::Yolo { enable, disable } => {
                    let enabled = enable && !disable;
                    let output = core.approval_set_yolo(enabled)?;
                    println!("{}", output);
                }
                ApprovalCommands::Allowlist { set, show } => {
                    if show {
                        let output = core.approval_show_allowlist()?;
                        println!("{}", output);
                    } else if let Some(tools_str) = set {
                        let tools: Vec<String> = tools_str.split(',')
                            .map(|s| s.trim().to_string())
                            .filter(|s| !s.is_empty())
                            .collect();
                        let output = core.approval_set_allowlist(&tools)?;
                        println!("{}", output);
                    } else {
                        println!("Usage: --set <tools> or --show");
                    }
                }
            },
        };

        Ok(())
    }
}
