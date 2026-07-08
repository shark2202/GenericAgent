//! Event handling for ga-tui

use crate::app::App;
use crate::pane::ApprovalRequest;
use crossterm::event::{Event as CrosstermEvent, EventStream, KeyEvent, MouseEvent};
use futures::StreamExt;
use std::time::Duration;
use tokio::sync::mpsc;

/// Application events (terminal + IPC)
#[derive(Debug)]
pub enum AppEvent {
    Key(KeyEvent),
    Mouse(MouseEvent),
    Resize(u16, u16),
    Tick,
    Ipc(IpcMessage),
}

/// Messages from the ga-core daemon
#[derive(Debug, Clone)]
pub enum IpcMessage {
    SessionNew {
        session_id: String,
        runner: String,
    },
    SessionOutput {
        session_id: String,
        line: String,
    },
    SessionStatus {
        session_id: String,
        status: String,
    },
    ApprovalNeeded {
        session_id: String,
        approval: ApprovalRequest,
    },
    ApprovalResolved {
        session_id: String,
        approved: bool,
    },
    GoalUpdate {
        goal_id: String,
        state: String,
    },
    Error {
        message: String,
    },
}

/// Event handler that reads crossterm events and forwards IPC messages
pub struct EventHandler {
    tx: mpsc::Sender<AppEvent>,
    tick_rate: Duration,
}

impl EventHandler {
    pub fn new(tx: mpsc::Sender<AppEvent>, tick_rate: Duration) -> Self {
        Self { tx, tick_rate }
    }

    /// Run the event loop (async) using tokio::select! which handles pinning
    pub async fn run(&self) -> anyhow::Result<()> {
        let mut reader = EventStream::new();
        let mut tick_interval = tokio::time::interval(self.tick_rate);

        loop {
            tokio::select! {
                _ = tick_interval.tick() => {
                    self.tx.send(AppEvent::Tick).await.ok();
                }
                maybe_event = reader.next() => {
                    match maybe_event {
                        Some(Ok(CrosstermEvent::Key(key))) => {
                            self.tx.send(AppEvent::Key(key)).await.ok();
                        }
                        Some(Ok(CrosstermEvent::Mouse(mouse))) => {
                            self.tx.send(AppEvent::Mouse(mouse)).await.ok();
                        }
                        Some(Ok(CrosstermEvent::Resize(w, h))) => {
                            self.tx.send(AppEvent::Resize(w, h)).await.ok();
                        }
                        Some(Ok(_)) => {} // Ignore other crossterm events
                        Some(Err(e)) => {
                            return Err(anyhow::anyhow!("crossterm event error: {}", e));
                        }
                        None => {
                            return Ok(()); // Stream ended
                        }
                    }
                }
            }
        }
    }
}
