use anyhow::Result;
use rusqlite::Connection;

pub struct Schema<'a> {
    conn: &'a Connection,
}

impl<'a> Schema<'a> {
    pub fn new(conn: &'a Connection) -> Result<Self> {
        Ok(Self { conn })
    }

    pub fn migrate(&self) -> Result<()> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                runner_kind TEXT NOT NULL DEFAULT 'ga',
                project TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                pid INTEGER,
                exit_code INTEGER
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                path TEXT,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                data TEXT,
                ts TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                tool_name TEXT,
                command TEXT,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                origin TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT,
                resolver_origin TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_pending ON approvals(status, session_id);

            CREATE TABLE IF NOT EXISTS runner_kinds (
                kind TEXT PRIMARY KEY,
                adapter TEXT NOT NULL,
                config TEXT,
                builtin INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            "
        )?;
        Ok(())
    }
}
