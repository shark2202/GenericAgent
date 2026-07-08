use anyhow::Result;
use tokio::sync::mpsc;
use tokio::task::spawn_blocking;

use super::protocol::IpcMessage;
use super::pty::PtySession;

pub struct EventStream {
    rx: mpsc::UnboundedReceiver<IpcMessage>,
}

impl EventStream {
    pub fn from_pty(pty: PtySession, session_id: String) -> Self {
        let (tx, rx) = mpsc::unbounded_channel();
        let sid = session_id;

        spawn_blocking(move || {
            let mut buf = [0u8; 4096];
            loop {
                match pty.read_output(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        let output = String::from_utf8_lossy(&buf[..n]).to_string();
                        let msg = IpcMessage {
                            session_id: sid.clone(),
                            kind: super::protocol::EventKind::Output,
                            payload: output,
                            ts: chrono::Utc::now().to_rfc3339(),
                        };
                        if tx.send(msg).is_err() {
                            break;
                        }
                    }
                }
            }
        });

        Self { rx }
    }

    pub async fn next(&mut self) -> Option<IpcMessage> {
        self.rx.recv().await
    }
}
