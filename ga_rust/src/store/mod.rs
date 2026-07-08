mod schema;
mod models;

pub use models::*;
pub use schema::Schema;

use anyhow::Result;
use rusqlite::Connection;
use std::path::PathBuf;
use std::sync::Mutex;

pub struct Store {
    conn: Mutex<Connection>,
    root: PathBuf,
}

impl Store {
    pub fn new(root: &str) -> Result<Self> {
        let root_path = PathBuf::from(root);
        let data_dir = root_path.join(".ga_data");
        std::fs::create_dir_all(&data_dir)?;

        let db_path = data_dir.join("ga.db");
        let conn = Connection::open(&db_path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;

        let schema = Schema::new(&conn)?;
        schema.migrate()?;

        Ok(Self {
            conn: Mutex::new(conn),
            root: root_path,
        })
    }

    pub fn root(&self) -> &PathBuf {
        &self.root
    }

    pub fn conn(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.conn.lock().map_err(|e| anyhow::anyhow!("DB lock: {}", e))
    }
}
