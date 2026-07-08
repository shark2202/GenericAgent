//! ga-tui: herdr-style TUI multiplexer for ga-core-cli
//!
//! Multi-runner panes (blocked/working/done), detach/reattach,
//! keyboard+mouse, themes, tool-timeline+approval rendering.

mod app;
mod approval;
mod config;
mod event;
mod ipc;
mod pane;
mod theme;
mod timeline;
mod ui;

use std::io;

use crossterm::{
    event::{DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use app::App;
use config::Config;
use event::{AppEvent, EventHandler};

use tokio::sync::mpsc;

const TICK_RATE_MS: u64 = 100;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Parse config
    let config = Config::load().unwrap_or_default();

    // Set up terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // Event channel
    let (tx, rx) = mpsc::channel::<AppEvent>(100);

    // Create app
    let mut app = App::new(config);

    // Spawn event handler
    let event_handler = EventHandler::new(tx, std::time::Duration::from_millis(TICK_RATE_MS));
    tokio::spawn(async move {
        if let Err(e) = event_handler.run().await {
            eprintln!("Event handler error: {}", e);
        }
    });

    // Main loop
    let result = run_app(&mut terminal, &mut app, rx).await;

    // Restore terminal
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;

    result
}

async fn run_app(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut App,
    mut rx: mpsc::Receiver<AppEvent>,
) -> anyhow::Result<()> {
    loop {
        // Render
        terminal.draw(|f| {
            ui::render(f, app);
        })?;

        // Handle events
        if let Some(event) = rx.recv().await {
            match event {
                AppEvent::Key(key) => app.handle_key(key),
                AppEvent::Mouse(mouse) => app.handle_mouse(mouse),
                AppEvent::Resize(w, h) => app.handle_resize(w, h),
                AppEvent::Tick => app.tick(),
                AppEvent::Ipc(msg) => app.handle_ipc(msg),
            }
        }

        if app.should_quit {
            return Ok(());
        }
    }
}
