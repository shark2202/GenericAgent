use anyhow::Result;
use portable_pty::{native_pty_system, PtySize, CommandBuilder, MasterPty};
use std::io::Read;
use std::sync::{Arc, Mutex};

pub struct PtySession {
    #[allow(dead_code)]
    master: Arc<Mutex<Box<dyn MasterPty + Send>>>,
    reader: Arc<Mutex<Box<dyn Read + Send>>>,
}

impl PtySession {
    pub fn new(command: &str, args: &[&str], env: &[(&str, &str)], size: PtySize) -> Result<Self> {
        let pty_system = native_pty_system();

        let pair = pty_system.openpty(size)?;
        let mut cmd = CommandBuilder::new(command);
        cmd.args(args);
        for (k, v) in env {
            cmd.env(k, v);
        }

        let _child = pair.slave.spawn_command(cmd)?;

        let reader = pair.master.try_clone_reader()?;
        let master = pair.master;

        Ok(Self {
            master: Arc::new(Mutex::new(master)),
            reader: Arc::new(Mutex::new(reader)),
        })
    }

    pub fn read_output(&self, buf: &mut [u8]) -> Result<usize> {
        let mut reader = self.reader.lock().map_err(|e| anyhow::anyhow!("PTY reader lock: {}", e))?;
        let n = reader.read(buf)?;
        Ok(n)
    }

    // TODO: write_input requires MasterPty + std::io::Write trait object support
    // Will be implemented with a dedicated writer handle in a follow-up

    pub fn resize(&self, rows: u16, cols: u16) -> Result<()> {
        let master = self.master.lock().map_err(|e| anyhow::anyhow!("PTY master lock: {}", e))?;
        master.resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })?;
        Ok(())
    }
}
