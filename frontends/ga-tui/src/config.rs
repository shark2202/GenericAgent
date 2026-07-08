//! Configuration for ga-tui

use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    /// Core daemon socket path (default: $XDG_RUNTIME_DIR/ga/core.sock)
    #[serde(default = "default_socket_path")]
    pub socket_path: String,

    /// Default theme name
    #[serde(default = "default_theme")]
    pub theme: String,

    /// Tick rate in ms for UI updates
    #[serde(default = "default_tick_rate")]
    pub tick_rate: u64,

    /// Prefix key for tmux-style commands (default: Ctrl+B)
    #[serde(default = "default_prefix")]
    pub prefix_key: String,

    /// Mouse support
    #[serde(default = "default_true")]
    pub mouse: bool,

    /// Max scrollback per pane
    #[serde(default = "default_scrollback")]
    pub scrollback: usize,

    /// Show status bar
    #[serde(default = "default_true")]
    pub status_bar: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            socket_path: default_socket_path(),
            theme: default_theme(),
            tick_rate: default_tick_rate(),
            prefix_key: default_prefix(),
            mouse: true,
            scrollback: 10000,
            status_bar: true,
        }
    }
}

impl Config {
    pub fn load() -> Result<Self, Box<dyn std::error::Error>> {
        let config_path = Self::config_file_path();
        if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            let config: Config = toml::from_str(&content)?;
            Ok(config)
        } else {
            Ok(Self::default())
        }
    }

    fn config_file_path() -> PathBuf {
        dirs::config_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("ga")
            .join("tui.toml")
    }
}

fn default_socket_path() -> String {
    dirs::runtime_dir()
        .map(|p| p.join("ga").join("core.sock").to_string_lossy().into_owned())
        .unwrap_or_else(|| "/tmp/ga/core.sock".into())
}

fn default_theme() -> String { "default".into() }
fn default_tick_rate() -> u64 { 100 }
fn default_prefix() -> String { "Ctrl+B".into() }
fn default_true() -> bool { true }
fn default_scrollback() -> usize { 10000 }
