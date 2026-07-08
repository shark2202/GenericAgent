//! ga-tui: herdr-style TUI multiplexer for ga-core-cli
//!
//! Multi-runner panes (blocked/working/done), detach/reattach,
//! keyboard+mouse, themes, tool-timeline+approval rendering.

use ga_tui::app::App;
use ga_tui::config::Config;
use ga_tui::event::{AppEvent, EventHandler};
use ga_tui::ipc::IpcClient;
use ga_tui::ui;

use std::io;
use std::time::Duration;

use crossterm::{
    event::{DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use tokio::sync::mpsc;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing (P1b)
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .init();

    // Parse config
    let config = Config::load().unwrap_or_default();

    // Set up terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    if config.mouse {
        execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    } else {
        execute!(stdout, EnterAlternateScreen)?;
    }
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // P1a: Install panic hook to restore terminal before unwinding
    let original_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // Best-effort terminal restore (ignore errors in panic context)
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture);
        original_hook(info);
    }));

    // Event channel
    let (tx, rx) = mpsc::channel::<AppEvent>(100);

    // Create app
    let mut app = App::new(config.clone());

    // P0: Wire up IPC client
    let (ipc_tx, mut ipc_rx) = mpsc::channel::<ga_tui::event::IpcMessage>(100);
    let socket_path = std::path::PathBuf::from(&config.socket_path);
    let ipc_client = IpcClient::new(socket_path, ipc_tx);

    // Spawn IPC reader task (connects to daemon, forwards IpcMessage)
    if let Err(e) = ipc_client.start_reader() {
        tracing::warn!("Failed to start IPC reader: {e}");
    } else {
        tracing::info!("IPC reader started, connecting to {}", config.socket_path);
    }

    // Spawn bridge task: IpcMessage → AppEvent::Ipc
    let event_tx = tx.clone();
    tokio::spawn(async move {
        while let Some(msg) = ipc_rx.recv().await {
            if event_tx.send(AppEvent::Ipc(msg)).await.is_err() {
                break; // Main loop exited
            }
        }
    });

    // Spawn crossterm event handler (Key/Mouse/Resize/Tick → tx)
    let tick_rate = Duration::from_millis(config.tick_rate);
    let event_handler = EventHandler::new(tx.clone(), tick_rate);
    tokio::spawn(async move {
        if let Err(e) = event_handler.run().await {
            tracing::error!("Event handler error: {e}");
        }
    });

    // Store IPC client in app for sending commands
    app.ipc_client = Some(ipc_client);

    // Run
    let result = run_app(&mut terminal, &mut app, rx).await;

    // Restore terminal
    disable_raw_mode()?;
    if app.config.mouse {
        execute!(
            terminal.backend_mut(),
            LeaveAlternateScreen,
            DisableMouseCapture
        )?;
    } else {
        execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    }

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

            // Drain and send pending IPC commands
            for cmd in app.drain_commands() {
                if let Some(ref ipc) = app.ipc_client
                    && let Err(e) = ipc.send_command(cmd).await
                {
                    tracing::warn!("IPC send failed: {e}");
                }
            }
        }

        if app.should_quit {
            return Ok(());
        }
    }
}
