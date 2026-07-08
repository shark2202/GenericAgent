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
pub enum RiskLevel {
    Low,
    Medium,
    High,
}

impl RiskLevel {
    pub fn as_str(&self) -> &'static str {
        match self {
            RiskLevel::Low => "low",
            RiskLevel::Medium => "medium",
            RiskLevel::High => "high",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "low" => Some(RiskLevel::Low),
            "medium" => Some(RiskLevel::Medium),
            "high" => Some(RiskLevel::High),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Origin {
    #[serde(rename = "auto")]
    Auto,
    #[serde(rename = "cli")]
    Cli,
    #[serde(rename = "tui")]
    Tui,
    #[serde(rename = "api")]
    Api,
    #[serde(rename = "yolo")]
    Yolo,
}

impl Origin {
    pub fn as_str(&self) -> &'static str {
        match self {
            Origin::Auto => "auto",
            Origin::Cli => "cli",
            Origin::Tui => "tui",
            Origin::Api => "api",
            Origin::Yolo => "yolo",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "auto" => Some(Origin::Auto),
            "cli" => Some(Origin::Cli),
            "tui" => Some(Origin::Tui),
            "api" => Some(Origin::Api),
            "yolo" => Some(Origin::Yolo),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Approval {
    pub id: i64,
    pub session_id: String,
    pub tool_name: Option<String>,
    pub command: Option<String>,
    pub risk_level: RiskLevel,
    pub status: String,
    pub origin: Origin,
    pub created_at: String,
    pub resolved_at: Option<String>,
    pub resolver_origin: Option<Origin>,
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

    pub fn session_update_status(&self, session_id: &str, status: &str) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        conn.execute(
            "UPDATE sessions SET status=?1, updated_at=?2 WHERE id=?3",
            rusqlite::params![status, now, session_id],
        )?;
        Ok(())
    }

    pub fn session_set_pid(&self, session_id: &str, pid: i64) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        conn.execute(
            "UPDATE sessions SET pid=?1, updated_at=?2 WHERE id=?3",
            rusqlite::params![pid, now, session_id],
        )?;
        Ok(())
    }

    pub fn session_set_exit_code(&self, session_id: &str, code: i64) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        conn.execute(
            "UPDATE sessions SET exit_code=?1, updated_at=?2 WHERE id=?3",
            rusqlite::params![code, now, session_id],
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

    // === Event methods ===

    pub fn event_create(&self, session_id: &str, kind: &str, data: Option<&str>, ts: &str) -> Result<i64> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO events (session_id, kind, data, ts) VALUES (?1, ?2, ?3, ?4)",
            rusqlite::params![session_id, kind, data, ts],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn events_recent(&self, session_id: Option<&str>, limit: i64) -> Result<Vec<(i64, String, String, Option<String>, String)>> {
        let conn = self.conn()?;
        let mut stmt = if let Some(sid) = session_id {
            conn.prepare(
                "SELECT id, session_id, kind, data, ts FROM events WHERE session_id=?1 ORDER BY id DESC LIMIT ?2",
            )?
        } else {
            conn.prepare(
                "SELECT id, session_id, kind, data, ts FROM events ORDER BY id DESC LIMIT ?1",
            )?
        };
        let rows: Vec<(i64, String, String, Option<String>, String)> = if let Some(sid) = session_id {
            stmt.query_map(rusqlite::params![sid, limit], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?))
            })?.collect::<Result<Vec<_>, _>>()?
        } else {
            stmt.query_map(rusqlite::params![limit], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?))
            })?.collect::<Result<Vec<_>, _>>()?
        };
        Ok(rows)
    }

    pub fn sessions_by_status(&self, status: &str) -> Result<Vec<Session>> {
        let conn = self.conn()?;
        let mut stmt = conn.prepare(
            "SELECT id, runner_kind, project, status, created_at, updated_at, pid, exit_code FROM sessions WHERE status=?1 ORDER BY created_at DESC"
        )?;
        let rows = stmt.query_map(rusqlite::params![status], |row| {
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

    // === Approval methods ===

    pub fn approval_create(
        &self,
        session_id: &str,
        tool_name: Option<&str>,
        command: Option<&str>,
        risk_level: &RiskLevel,
        origin: &Origin,
    ) -> Result<Approval> {
        let conn = self.conn()?;
        conn.execute(
            "INSERT INTO approvals (session_id, tool_name, command, risk_level, status, origin) VALUES (?1,?2,?3,?4,'pending',?5)",
            rusqlite::params![session_id, tool_name, command, risk_level.as_str(), origin.as_str()],
        )?;
        let id = conn.last_insert_rowid();
        let created_at: String = conn.query_row(
            "SELECT created_at FROM approvals WHERE id=?1",
            rusqlite::params![id],
            |row| row.get(0),
        )?;
        Ok(Approval {
            id,
            session_id: session_id.to_string(),
            tool_name: tool_name.map(String::from),
            command: command.map(String::from),
            risk_level: risk_level.clone(),
            status: "pending".to_string(),
            origin: origin.clone(),
            created_at,
            resolved_at: None,
            resolver_origin: None,
        })
    }

    pub fn approvals_pending(&self, session_id: Option<&str>) -> Result<Vec<Approval>> {
        let conn = self.conn()?;
        let mut stmt = if let Some(sid) = session_id {
            conn.prepare(
                "SELECT id, session_id, tool_name, command, risk_level, status, origin, created_at, resolved_at, resolver_origin FROM approvals WHERE status='pending' AND session_id=?1 ORDER BY created_at ASC",
            )?
        } else {
            conn.prepare(
                "SELECT id, session_id, tool_name, command, risk_level, status, origin, created_at, resolved_at, resolver_origin FROM approvals WHERE status='pending' ORDER BY created_at ASC",
            )?
        };
        let rows = if let Some(sid) = session_id {
            stmt.query_map(rusqlite::params![sid], Self::row_to_approval)?
        } else {
            stmt.query_map([], Self::row_to_approval)?
        };
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn approval_resolve(
        &self,
        id: i64,
        status: &str,
        resolver_origin: &Origin,
    ) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let conn = self.conn()?;
        let affected = conn.execute(
            "UPDATE approvals SET status=?1, resolved_at=?2, resolver_origin=?3 WHERE id=?4",
            rusqlite::params![status, now, resolver_origin.as_str(), id],
        )?;
        if affected == 0 {
            anyhow::bail!("Approval #{} not found", id);
        }
        Ok(())
    }

    pub fn approval_get(&self, id: i64) -> Result<Approval> {
        let conn = self.conn()?;
        conn.query_row(
            "SELECT id, session_id, tool_name, command, risk_level, status, origin, created_at, resolved_at, resolver_origin FROM approvals WHERE id=?1",
            rusqlite::params![id],
            Self::row_to_approval,
        ).map_err(Into::into)
    }

    fn row_to_approval(row: &rusqlite::Row) -> rusqlite::Result<Approval> {
        let risk_str: String = row.get(4)?;
        let origin_str: String = row.get(6)?;
        let resolver_str: Option<String> = row.get(9)?;
        Ok(Approval {
            id: row.get(0)?,
            session_id: row.get(1)?,
            tool_name: row.get(2)?,
            command: row.get(3)?,
            risk_level: RiskLevel::from_str(&risk_str)
                .ok_or_else(|| rusqlite::Error::FromSqlConversionFailure(4, rusqlite::types::Type::Text, Box::new(std::io::Error::other("invalid risk_level"))))?,
            status: row.get(5)?,
            origin: Origin::from_str(&origin_str)
                .ok_or_else(|| rusqlite::Error::FromSqlConversionFailure(6, rusqlite::types::Type::Text, Box::new(std::io::Error::other("invalid origin"))))?,
            created_at: row.get(7)?,
            resolved_at: row.get(8)?,
            resolver_origin: resolver_str.and_then(|s| Origin::from_str(&s)),
        })
    }
}
