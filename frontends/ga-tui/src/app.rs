//! Main application state and event handling

use crate::config::Config;
use crate::event::IpcMessage;
use crate::ipc::{IpcClient, IpcCommand};
use crate::pane::{LineStyle, Pane, PaneStatus, RunnerKind};
use crate::theme::Theme;
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, MouseButton, MouseEvent, MouseEventKind};

/// Input mode for the TUI (matches herdr-style prefix/input/normal)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InputMode {
    Normal,
    Prefix,
    Input,
}

/// Application state
pub struct App {
    pub config: Config,
    pub theme: Theme,
    pub panes: Vec<Pane>,
    pub active_pane: usize,
    pub mode: InputMode,
    pub input_buffer: String,
    pub command_text: String,
    pub show_help: bool,
    pub should_quit: bool,
    pub last_error: Option<String>,
    pub size: (u16, u16),
    pub ipc_client: Option<IpcClient>,
    pub goal_summary: Option<String>,
    pub goals: Vec<crate::event::GoalSummary>,
    /// Pending IPC commands to be sent by the async event loop
    pub pending_commands: Vec<IpcCommand>,
}

impl App {
    /// Create a new App instance with the given configuration
    pub fn new(config: Config) -> Self {
        let theme = Theme::by_name(&config.theme);
        Self {
            config,
            theme,
            panes: Vec::new(),
            active_pane: 0,
            mode: InputMode::Normal,
            input_buffer: String::new(),
            command_text: String::new(),
            show_help: false,
            should_quit: false,
            last_error: None,
            size: (80, 24),
            ipc_client: None,
            goal_summary: None,
            goals: Vec::new(),
            pending_commands: Vec::new(),
        }
    }

    /// Create with IPC channel
    pub fn with_ipc(config: Config, ipc_tx: tokio::sync::mpsc::Sender<IpcMessage>) -> Self {
        let mut app = Self::new(config);
        let socket_path = std::path::PathBuf::from(&app.config.socket_path);
        app.ipc_client = Some(IpcClient::new(socket_path, ipc_tx));
        app
    }

    /// Handle a key event
    pub fn handle_key(&mut self, key: KeyEvent) {
        if key.kind != KeyEventKind::Press {
            return;
        }

        match self.mode {
            InputMode::Normal => self.handle_normal_key(key),
            InputMode::Input => self.handle_input_key(key),
            InputMode::Prefix => self.handle_prefix_key(key),
        }
    }

    fn handle_normal_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Char('q') => {
                self.should_quit = true;
            }
            KeyCode::Esc => {
                self.should_quit = true;
            }
            KeyCode::Tab => {
                if !self.panes.is_empty() {
                    self.active_pane = (self.active_pane + 1) % self.panes.len();
                }
            }
            KeyCode::BackTab => {
                if !self.panes.is_empty() {
                    self.active_pane = (self.active_pane + self.panes.len() - 1) % self.panes.len();
                }
            }
            KeyCode::Char('n') => {
                self.new_session();
            }
            KeyCode::Char('c') => {
                self.close_active_pane();
            }
            KeyCode::Char('d') => {
                self.detach_session();
            }
            KeyCode::Char('a') => {
                self.attach_session();
            }
            KeyCode::Char('i') => {
                self.mode = InputMode::Input;
            }
            KeyCode::Char(':') => {
                self.mode = InputMode::Prefix;
                self.command_text.clear();
            }
            KeyCode::Char('?') => {
                self.show_help = !self.show_help;
            }
            KeyCode::Char('t') | KeyCode::Char('T') => {
                self.theme = self.theme.cycle();
            }
            KeyCode::Left => {
                if self.active_pane > 0 {
                    self.active_pane -= 1;
                }
            }
            KeyCode::Right => {
                if !self.panes.is_empty() && self.active_pane < self.panes.len() - 1 {
                    self.active_pane += 1;
                }
            }
            KeyCode::Up => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_up(3);
                }
            }
            KeyCode::Down => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_down(3);
                }
            }
            KeyCode::PageUp => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_up(20);
                }
            }
            KeyCode::PageDown => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_down(20);
                }
            }
            KeyCode::Char(c) if ('1'..='9').contains(&c) => {
                let idx = (c as usize) - '1' as usize;
                if idx < self.panes.len() {
                    self.active_pane = idx;
                }
            }
            _ => {}
        }
    }

    fn handle_input_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => {
                self.mode = InputMode::Normal;
            }
            KeyCode::Enter => {
                self.submit_input();
            }
            KeyCode::Backspace => {
                self.input_buffer.pop();
            }
            KeyCode::Char(c) => {
                self.input_buffer.push(c);
            }
            _ => {}
        }
    }

    fn handle_prefix_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => {
                self.mode = InputMode::Normal;
            }
            KeyCode::Enter => {
                self.execute_command();
                self.mode = InputMode::Normal;
            }
            KeyCode::Backspace => {
                self.command_text.pop();
            }
            KeyCode::Char(c) => {
                self.command_text.push(c);
            }
            _ => {}
        }
    }

    /// Handle mouse event (scroll, click-to-switch-pane)
    pub fn handle_mouse(&mut self, mouse: MouseEvent) {
        match mouse.kind {
            MouseEventKind::ScrollUp => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_up(3);
                }
            }
            MouseEventKind::ScrollDown => {
                if let Some(pane) = self.panes.get_mut(self.active_pane) {
                    pane.scroll_down(3);
                }
            }
            MouseEventKind::Down(MouseButton::Left) if mouse.row == 0 && !self.panes.is_empty() => {
                let tab_width = (self.size.0 as usize) / self.panes.len().max(1);
                let idx = (mouse.column as usize) / tab_width.max(1);
                if idx < self.panes.len() {
                    self.active_pane = idx;
                }
            }
            _ => {}
        }
    }

    /// Handle terminal resize
    pub fn handle_resize(&mut self, width: u16, height: u16) {
        self.size = (width, height);
    }

    /// Tick handler (periodic updates)
    pub fn tick(&mut self) {
        // Check for timed-out tool calls, update status indicators, etc.
    }

    /// Render the UI to a testable buffer (for E2E tests)
    pub fn render(&self, f: &mut ratatui::Frame) {
        crate::ui::render(f, self);
    }

    /// Handle IPC message from core daemon
    pub fn handle_ipc(&mut self, msg: IpcMessage) {
        match msg {
            IpcMessage::SessionNew { session_id, runner } => {
                let kind = match runner.as_str() {
                    "ga" => RunnerKind::Ga,
                    "codex" => RunnerKind::Codex,
                    "claude" => RunnerKind::Claude,
                    "subprocess" => RunnerKind::Subprocess,
                    other => RunnerKind::Custom(other.to_string()),
                };
                let pane = Pane::new(self.panes.len(), session_id, kind, self.config.scrollback);
                self.panes.push(pane);
                self.active_pane = self.panes.len() - 1;
            }
            IpcMessage::SessionOutput { session_id, line } => {
                if let Some(pane) = self.panes.iter_mut().find(|p| p.session_id == session_id) {
                    pane.push_line(line, LineStyle::Normal);
                }
            }
            IpcMessage::SessionStatus { session_id, status } => {
                if let Some(pane) = self.panes.iter_mut().find(|p| p.session_id == session_id) {
                    pane.status = match status.as_str() {
                        "working" => PaneStatus::Working,
                        "blocked" => PaneStatus::Blocked,
                        "done" => PaneStatus::Done,
                        "error" => PaneStatus::Error,
                        "detached" => PaneStatus::Detached,
                        _ => PaneStatus::Working,
                    };
                }
            }
            IpcMessage::SessionReattach { session_id } => {
                // Daemon requests reattach of a detached session (openspec §3.3)
                if let Some(pane) = self.panes.iter_mut().find(|p| p.session_id == session_id) {
                    pane.status = PaneStatus::Working;
                    self.active_pane = self.panes.iter().position(|p| p.session_id == session_id).unwrap_or(self.active_pane);
                }
            }
            IpcMessage::ApprovalNeeded { session_id, approval } => {
                if let Some(pane) = self.panes.iter_mut().find(|p| p.session_id == session_id) {
                    pane.pending_approval = Some(approval);
                    pane.status = PaneStatus::Blocked;
                }
            }
            IpcMessage::ApprovalResolved { session_id, approved } => {
                if let Some(pane) = self.panes.iter_mut().find(|p| p.session_id == session_id) {
                    pane.pending_approval = None;
                    pane.status = if approved { PaneStatus::Working } else { PaneStatus::Done };
                }
            }
            IpcMessage::GoalUpdate { goal_id: _, state } => {
                self.goal_summary = Some(state);
            }
            IpcMessage::GoalListResult { goals } => {
                self.goals = goals;
            }
            IpcMessage::GoalError { goal_id: _, message } => {
                self.last_error = Some(message);
            }
            IpcMessage::Error { message } => {
                self.last_error = Some(message);
            }
        }
    }

    fn new_session(&mut self) {
        let session_id = format!("s{}", self.panes.len());
        let pane = Pane::new(
            self.panes.len(),
            session_id.clone(),
            RunnerKind::Ga,
            self.config.scrollback,
        );
        self.panes.push(pane);
        self.active_pane = self.panes.len() - 1;
    }

    /// Create a new subprocess runner pane
    fn new_subprocess_session(&mut self) {
        let session_id = format!("s{}", self.panes.len());
        let pane = Pane::new(
            self.panes.len(),
            session_id.clone(),
            RunnerKind::Subprocess,
            self.config.scrollback,
        );
        self.panes.push(pane);
        self.active_pane = self.panes.len() - 1;
    }

    fn close_active_pane(&mut self) {
        if !self.panes.is_empty() {
            self.panes.remove(self.active_pane);
            if self.active_pane >= self.panes.len() && !self.panes.is_empty() {
                self.active_pane = self.panes.len() - 1;
            }
        }
    }

    fn detach_session(&mut self) {
        if let Some(pane) = self.panes.get_mut(self.active_pane) {
            pane.status = PaneStatus::Detached;
        }
    }

    fn attach_session(&mut self) {
        if let Some(pane) = self.panes.get_mut(self.active_pane)
            && pane.status == PaneStatus::Detached
        {
            pane.status = PaneStatus::Working;
        }
    }

    fn submit_input(&mut self) {
        if self.input_buffer.is_empty() {
            return;
        }
        if let Some(pane) = self.panes.get_mut(self.active_pane) {
            pane.push_line(format!("> {}", self.input_buffer), LineStyle::Normal);
            // Queue IPC command for the async event loop to send
            let session_id = pane.session_id.clone();
            let text = self.input_buffer.clone();
            self.pending_commands.push(IpcCommand::SessionInput { session_id, text });
        }
        self.input_buffer.clear();
        self.mode = InputMode::Normal;
    }

    fn execute_command(&mut self) {
        let cmd = self.command_text.trim();
        match cmd {
            "q" | "quit" => self.should_quit = true,
            "help" => self.show_help = true,
            "theme" => self.theme = self.theme.cycle(),
            "sub" | "subprocess" => self.new_subprocess_session(),
            _ => {
                self.last_error = Some(format!("Unknown command: {}", cmd));
            }
        }
        self.command_text.clear();
    }

    /// Drain pending IPC commands, returning them for the async event loop to send.
    /// Called after each event handling cycle in main.rs.
    pub fn drain_commands(&mut self) -> Vec<IpcCommand> {
        std::mem::take(&mut self.pending_commands)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_app() -> App {
        App::new(Config::default())
    }

    #[test]
    fn test_sub_command_creates_subprocess_pane() {
        let mut app = make_app();
        assert!(app.panes.is_empty());
        app.command_text = "sub".to_string();
        app.execute_command();
        assert_eq!(app.panes.len(), 1);
        assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
        assert_eq!(app.active_pane, 0);
    }

    #[test]
    fn test_subprocess_alias_works() {
        let mut app = make_app();
        app.command_text = "subprocess".to_string();
        app.execute_command();
        assert_eq!(app.panes.len(), 1);
        assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
    }

    #[test]
    fn test_ipc_session_new_subprocess() {
        let mut app = make_app();
        let msg = IpcMessage::SessionNew {
            session_id: "sub-1".into(),
            runner: "subprocess".into(),
        };
        app.handle_ipc(msg);
        assert_eq!(app.panes.len(), 1);
        assert_eq!(app.panes[0].runner, RunnerKind::Subprocess);
        assert_eq!(app.panes[0].session_id, "sub-1");
    }

    #[test]
    fn test_detach_sets_status_not_remove() {
        let mut app = make_app();
        app.command_text = "sub".to_string();
        app.execute_command();
        assert_eq!(app.panes.len(), 1);
        assert_eq!(app.panes[0].status, PaneStatus::Working);

        // detach should set status, NOT remove the pane
        app.detach_session();
        assert_eq!(app.panes.len(), 1); // pane still exists
        assert_eq!(app.panes[0].status, PaneStatus::Detached);
    }

    #[test]
    fn test_attach_restores_working_status() {
        let mut app = make_app();
        app.command_text = "sub".to_string();
        app.execute_command();
        app.detach_session();
        assert_eq!(app.panes[0].status, PaneStatus::Detached);

        // attach should restore Working status
        app.attach_session();
        assert_eq!(app.panes[0].status, PaneStatus::Working);
    }

    #[test]
    fn test_close_actually_removes_pane() {
        let mut app = make_app();
        app.command_text = "sub".to_string();
        app.execute_command();
        assert_eq!(app.panes.len(), 1);

        // close should remove the pane (unlike detach)
        app.close_active_pane();
        assert!(app.panes.is_empty());
    }

    #[test]
    fn test_detach_then_close_removes() {
        let mut app = make_app();
        app.command_text = "sub".to_string();
        app.execute_command();
        app.detach_session();
        assert_eq!(app.panes.len(), 1);
        assert_eq!(app.panes[0].status, PaneStatus::Detached);

        // After detach, close should still work and remove the pane
        app.close_active_pane();
        assert!(app.panes.is_empty());
    }
}
