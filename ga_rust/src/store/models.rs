use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use super::Store;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    pub runner_kind: String,
    pub project: Option<String>,
    pub status: String,
    pub created_at: String,
    pub updated_at: String,
    pub pid: Option<i64>,
    pub exit_code: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub path: Option<String>,
    pub active: bool,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: i64,
    pub session_id: String,
    pub kind: String,
    pub data: Option<String>,
    pub ts: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Approval {
    pub id: i64,
    pub session_id: String,
    pub command: String,
    pub status: String,
    pub created_at: String,
    pub resolved_at: Option<String>,
}

impl Store {
    pub fn session_create(&self, runner_kind: &str, project: Option<&str>) -> Result<Session> {
        let now = Utc::now().to_rfc3339();
        let id = Uuid::new_v4().to_string();
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO sessions (id, runner_kind, project, status, created_at, updated_at) VALUES (?1,?2,?3,'active',?4,?4)",
            rusqlite::params![id, runner_kind, project, now],
        )?;
        Ok(Session {
            id,
            runner_kind: runner_kind.to_string(),
            project: project.map(String::from),
            status: "active".into(),
            created_at: now.clone(),
            updated_at: now,
            pid: None,
            exit_code: None,
        })
    }

    pub fn sessions_list(&self) -> Result<Vec<Session>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT id, runner_kind, project, status, created_at, updated_at, pid, exit_code FROM sessions ORDER BY created_at DESC"
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(Session {
                id: row.get(0)?,
                runner_kind: row.get(1)?,
                project: row.get(2)?,
                status: row.get(3)?,
                created_at: row.get(4)?,
                updated_at: row.get(5)?,
                pid: row.get(6)?,
                exit_code: row.get(7)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn session_archive(&self, session_id: &str) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        conn.execute(
            "UPDATE sessions SET status='archived', updated_at=?1 WHERE id=?2",
            rusqlite::params![now, session_id],
        )?;
        Ok(())
    }

    pub fn project_create(&self, name: &str, path: Option<&str>) -> Result<Project> {
        let now = Utc::now().to_rfc3339();
        let id = Uuid::new_v4().to_string();
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO projects (id, name, path, active, created_at, updated_at) VALUES (?1,?2,?3,0,?4,?4)",
            rusqlite::params![id, name, path, now],
        )?;
        Ok(Project {
            id,
            name: name.to_string(),
            path: path.map(String::from),
            active: false,
            created_at: now.clone(),
            updated_at: now,
        })
    }

    pub fn projects_list(&self) -> Result<Vec<Project>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT id, name, path, active, created_at, updated_at FROM projects ORDER BY name"
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(Project {
                id: row.get(0)?,
                name: row.get(1)?,
                path: row.get(2)?,
                active: row.get::<_, i64>(3)? != 0,
                created_at: row.get(4)?,
                updated_at: row.get(5)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn project_follow(&self, name: &str) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        conn.execute("UPDATE projects SET active=0", [])?;
        conn.execute(
            "UPDATE projects SET active=1, updated_at=?1 WHERE name=?2",
            rusqlite::params![now, name],
        )?;
        Ok(())
    }

    pub fn config_set(&self, key: &str, value: &str) -> Result<()> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?1, ?2)",
            rusqlite::params![key, value],
        )?;
        Ok(())
    }

    pub fn config_get(&self, key: &str) -> Result<Option<String>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare("SELECT value FROM config WHERE key=?1")?;
        let mut rows = stmt.query(rusqlite::params![key])?;
        match rows.next()? {
            Some(row) => Ok(Some(row.get(0)?)),
            None => Ok(None),
        }
    }
}
