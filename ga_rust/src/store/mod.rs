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

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    /// Helper: create a fresh Store backed by a temp directory
    fn make_store() -> (Store, TempDir) {
        let dir = TempDir::new().expect("tempdir");
        let store = Store::new(dir.path().to_str().unwrap()).expect("store new");
        (store, dir)
    }

    // ── Session tests ──────────────────────────────────────────────

    #[test]
    fn session_create_and_list() {
        let (store, _dir) = make_store();
        let s1 = store.session_create("ga", None).unwrap();
        let s2 = store.session_create("subprocess", Some("/tmp/proj")).unwrap();

        assert_eq!(s1.runner_kind, "ga");
        assert_eq!(s1.status, "active");
        assert!(s1.project.is_none());
        assert!(s1.pid.is_none());

        assert_eq!(s2.runner_kind, "subprocess");
        assert_eq!(s2.project.as_deref(), Some("/tmp/proj"));

        let list = store.sessions_list().unwrap();
        assert_eq!(list.len(), 2);
        // DESC order → s2 first
        assert_eq!(list[0].id, s2.id);
        assert_eq!(list[1].id, s1.id);
    }

    #[test]
    fn session_update_status() {
        let (store, _dir) = make_store();
        let s = store.session_create("ga", None).unwrap();
        store.session_update_status(&s.id, "stopped").unwrap();

        let list = store.sessions_list().unwrap();
        assert_eq!(list[0].status, "stopped");
    }

    #[test]
    fn session_set_pid() {
        let (store, _dir) = make_store();
        let s = store.session_create("subprocess", None).unwrap();
        store.session_set_pid(&s.id, 12345).unwrap();

        let list = store.sessions_list().unwrap();
        assert_eq!(list[0].pid, Some(12345));
    }

    #[test]
    fn session_set_exit_code() {
        let (store, _dir) = make_store();
        let s = store.session_create("ga", None).unwrap();
        store.session_set_exit_code(&s.id, 0).unwrap();

        let list = store.sessions_list().unwrap();
        assert_eq!(list[0].exit_code, Some(0));
    }

    // ── Project tests ──────────────────────────────────────────────

    #[test]
    fn project_create_and_list() {
        let (store, _dir) = make_store();
        let p1 = store.project_create("my-app", Some("/code/app")).unwrap();
        let p2 = store.project_create("lib-x", None).unwrap();

        assert_eq!(p1.name, "my-app");
        assert!(!p1.active);

        let list = store.projects_list().unwrap();
        assert_eq!(list.len(), 2);
    }

    #[test]
    fn project_follow_sets_active() {
        let (store, _dir) = make_store();
        store.project_create("alpha", None).unwrap();
        store.project_create("beta", None).unwrap();

        store.project_follow("beta").unwrap();
        let list = store.projects_list().unwrap();
        let beta = list.iter().find(|p| p.name == "beta").unwrap();
        let alpha = list.iter().find(|p| p.name == "alpha").unwrap();
        assert!(beta.active);
        assert!(!alpha.active);

        // Switch to alpha → beta deactivates
        store.project_follow("alpha").unwrap();
        let list2 = store.projects_list().unwrap();
        let alpha2 = list2.iter().find(|p| p.name == "alpha").unwrap();
        let beta2 = list2.iter().find(|p| p.name == "beta").unwrap();
        assert!(alpha2.active);
        assert!(!beta2.active);
    }

    #[test]
    fn project_duplicate_name_fails() {
        let (store, _dir) = make_store();
        store.project_create("dup", None).unwrap();
        // name is UNIQUE → second insert must fail
        let result = store.project_create("dup", None);
        assert!(result.is_err());
    }

    // ── Config tests ───────────────────────────────────────────────

    #[test]
    fn config_set_and_get() {
        let (store, _dir) = make_store();
        assert!(store.config_get("theme").unwrap().is_none());

        store.config_set("theme", "dark").unwrap();
        assert_eq!(store.config_get("theme").unwrap().as_deref(), Some("dark"));

        // Upsert: overwrite existing
        store.config_set("theme", "light").unwrap();
        assert_eq!(store.config_get("theme").unwrap().as_deref(), Some("light"));
    }

    #[test]
    fn config_multiple_keys() {
        let (store, _dir) = make_store();
        store.config_set("a", "1").unwrap();
        store.config_set("b", "2").unwrap();
        store.config_set("c", "3").unwrap();

        assert_eq!(store.config_get("a").unwrap().as_deref(), Some("1"));
        assert_eq!(store.config_get("b").unwrap().as_deref(), Some("2"));
        assert_eq!(store.config_get("c").unwrap().as_deref(), Some("3"));
        assert!(store.config_get("d").unwrap().is_none());
    }

    // ── Store init / root ──────────────────────────────────────────

    #[test]
    fn store_new_creates_data_dir() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().to_str().unwrap().to_string();
        let store = Store::new(&path).unwrap();
        assert!(dir.path().join(".ga_data").exists());
        assert_eq!(store.root().to_str().unwrap(), path);
    }
}
