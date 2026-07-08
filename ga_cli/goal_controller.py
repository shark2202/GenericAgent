#!/usr/bin/env python3
"""
ga_cli/goal_controller.py - Goal lifecycle state machine + SQLite persistence

State machine: proposed -> confirmed -> running -> done | failed | budget_exhausted | timeout | paused
Features: confirm-token, max_concurrent_runners budget, max_duration timeout,
          pause/resume, deliverable aggregation, cross-restart recovery.
"""
import sqlite3
import uuid
import time
import json
import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

log = logging.getLogger(__name__)


def _now_iso() -> str:
    """Current UTC time as ISO8601 string, matching Rust's serde serialization."""
    return datetime.now(timezone.utc).isoformat()

# ── State Machine ──────────────────────────────────────────────

class GoalState(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"  # snake_case to match Rust serde
    TIMEOUT = "timeout"

    @classmethod
    def terminal_states(cls):
        return {cls.DONE, cls.FAILED, cls.BUDGET_EXHAUSTED, cls.TIMEOUT}

    def is_terminal(self):
        return self in GoalState.terminal_states()


# Valid transitions: from_state -> set of allowed to_states
VALID_TRANSITIONS = {
    GoalState.PROPOSED: {GoalState.CONFIRMED},
    GoalState.CONFIRMED: {GoalState.RUNNING},
    GoalState.RUNNING: {GoalState.DONE, GoalState.FAILED, GoalState.BUDGET_EXHAUSTED, GoalState.TIMEOUT, GoalState.PAUSED},
    GoalState.PAUSED: {GoalState.RUNNING},
}

class InvalidTransition(Exception):
    """Raised when a state transition is not allowed."""
    pass

class ConfirmTokenError(Exception):
    """Raised when confirm-token is missing, invalid, or expired."""
    pass

class BudgetExhausted(Exception):
    """Raised when budget is exhausted and no progress is possible."""
    pass


# ── Data Model ─────────────────────────────────────────────────

@dataclass
class Goal:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: GoalState = GoalState.PROPOSED
    proposal: str = ""
    supervisor: str = ""
    reason: str = ""
    max_concurrent_runners: int = 1
    max_duration: Optional[float] = None  # seconds, None = unlimited
    confirm_token: Optional[str] = None
    confirm_token_expires: Optional[float] = None  # unix timestamp
    created_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    active_runners: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, target: GoalState) -> bool:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        return target in allowed

    def transition_to(self, target: GoalState) -> None:
        if not self.can_transition_to(target):
            raise InvalidTransition(
                f"Cannot transition from {self.state.value} to {target.value}"
            )
        self.state = target
        if target == GoalState.RUNNING:
            self.started_at = _now_iso()
        if target.is_terminal():
            self.finished_at = _now_iso()


@dataclass
class GoalDeliverable:
    goal_id: str = ""
    content: str = ""
    format: str = "text"  # "text" or "json"
    created_at: str = field(default_factory=_now_iso)
    session_summaries: List[Dict[str, Any]] = field(default_factory=list)


# ── SQLite Persistence ─────────────────────────────────────────

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'proposed',
    proposal TEXT NOT NULL DEFAULT '',
    supervisor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    max_concurrent_runners INTEGER NOT NULL DEFAULT 1,
    max_duration REAL,
    confirm_token TEXT,
    confirm_token_expires REAL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    active_runners INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS goal_deliverables (
    goal_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL,
    session_summaries TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (goal_id),
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);

CREATE TABLE IF NOT EXISTS goal_sessions (
    goal_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    finished_at TEXT,
    final_answer TEXT,
    PRIMARY KEY (goal_id, session_id),
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);
"""

class GoalStore:
    """SQLite persistence layer for goals."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(SCHEMA_V1)
        conn.commit()

    def close(self):
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    # ── Goal CRUD ──

    def save_goal(self, goal: Goal) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO goals
               (id, state, proposal, supervisor, reason, max_concurrent_runners,
                max_duration, confirm_token, confirm_token_expires,
                created_at, started_at, finished_at, active_runners, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (goal.id, goal.state.value, goal.proposal, goal.supervisor, goal.reason,
             goal.max_concurrent_runners, goal.max_duration,
             goal.confirm_token, goal.confirm_token_expires,
             goal.created_at, goal.started_at, goal.finished_at,
             goal.active_runners, json.dumps(goal.metadata))
        )
        conn.commit()

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_goal(row)

    def list_goals(self, state_filter: Optional[GoalState] = None) -> List[Goal]:
        conn = self._get_conn()
        if state_filter:
            rows = conn.execute(
                "SELECT * FROM goals WHERE state = ? ORDER BY created_at DESC",
                (state_filter.value,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def delete_goal(self, goal_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        conn.commit()
        return cursor.rowcount > 0

    def _row_to_goal(self, row) -> Goal:
        return Goal(
            id=row['id'],
            state=GoalState(row['state']),
            proposal=row['proposal'],
            supervisor=row['supervisor'],
            reason=row['reason'],
            max_concurrent_runners=row['max_concurrent_runners'],
            max_duration=row['max_duration'],
            confirm_token=row['confirm_token'],
            confirm_token_expires=row['confirm_token_expires'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            finished_at=row['finished_at'],
            active_runners=row['active_runners'],
            metadata=json.loads(row['metadata']),
        )

    # ── Deliverable ──

    def save_deliverable(self, d: GoalDeliverable) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO goal_deliverables
               (goal_id, content, format, created_at, session_summaries)
               VALUES (?, ?, ?, ?, ?)""",
            (d.goal_id, d.content, d.format, d.created_at,
             json.dumps(d.session_summaries))
        )
        conn.commit()

    def get_deliverable(self, goal_id: str) -> Optional[GoalDeliverable]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM goal_deliverables WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        if row is None:
            return None
        return GoalDeliverable(
            goal_id=row['goal_id'],
            content=row['content'],
            format=row['format'],
            created_at=row['created_at'],
            session_summaries=json.loads(row['session_summaries']),
        )

    # ── Sessions ──

    def add_session(self, goal_id: str, session_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR IGNORE INTO goal_sessions
               (goal_id, session_id, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (goal_id, session_id, _now_iso())
        )
        conn.commit()

    def update_session(self, goal_id: str, session_id: str,
                       status: str, final_answer: Optional[str] = None) -> None:
        conn = self._get_conn()
        finished_at = _now_iso() if status in ('completed', 'failed', 'interrupted') else None
        conn.execute(
            """UPDATE goal_sessions SET status=?, finished_at=?, final_answer=?
               WHERE goal_id=? AND session_id=?""",
            (status, finished_at, final_answer, goal_id, session_id)
        )
        conn.commit()

    def get_sessions(self, goal_id: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM goal_sessions WHERE goal_id = ? ORDER BY created_at",
            (goal_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_active_sessions(self, goal_id: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM goal_sessions WHERE goal_id=? AND status='running'",
            (goal_id,)
        ).fetchone()
        return row['cnt'] if row else 0

    # ── Recovery ──

    def get_running_goals(self) -> List[Goal]:
        """Get all goals in running state (for recovery after restart)."""
        return self.list_goals(state_filter=GoalState.RUNNING)

    def get_paused_goals(self) -> List[Goal]:
        """Get all goals in paused state (for recovery after restart)."""
        return self.list_goals(state_filter=GoalState.PAUSED)


# ── Goal Controller ────────────────────────────────────────────

CONFIRM_TOKEN_TTL = 3600  # 1 hour default

class GoalController:
    """High-level goal lifecycle controller."""

    def __init__(self, store: GoalStore):
        self.store = store
        self._timers: Dict[str, threading.Timer] = {}
        self._spawn_lock = threading.Lock()

    def propose(self, proposal: str, supervisor: str = "",
                reason: str = "", max_concurrent_runners: int = 1,
                max_duration: Optional[float] = None,
                metadata: Optional[Dict] = None) -> Goal:
        """Create a proposed goal with a confirm-token. Does NOT start execution."""
        token = str(uuid.uuid4())
        token_expires = time.time() + CONFIRM_TOKEN_TTL

        goal = Goal(
            proposal=proposal,
            supervisor=supervisor,
            reason=reason,
            max_concurrent_runners=max_concurrent_runners,
            max_duration=max_duration,
            confirm_token=token,
            confirm_token_expires=token_expires,
            metadata=metadata or {},
        )
        self.store.save_goal(goal)
        log.info(f"Goal proposed: {goal.id} (token expires in {CONFIRM_TOKEN_TTL}s)")
        return goal

    def confirm(self, goal_id: str, confirm_token: str) -> Goal:
        """Confirm a proposed goal using the confirm-token."""
        goal = self.store.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        if goal.state != GoalState.PROPOSED:
            raise InvalidTransition(f"Goal is {goal.state.value}, not proposed")
        if goal.confirm_token != confirm_token:
            raise ConfirmTokenError("Invalid confirm-token")
        if goal.confirm_token_expires and time.time() > goal.confirm_token_expires:
            raise ConfirmTokenError("Confirm-token has expired")

        goal.transition_to(GoalState.CONFIRMED)
        goal.confirm_token = None  # one-time use
        goal.confirm_token_expires = None
        self.store.save_goal(goal)
        log.info(f"Goal confirmed: {goal_id}")
        return goal

    def run(self, goal_id: str, confirm_token: str) -> Goal:
        """Confirm + start a goal. Validates token then transitions to running."""
        goal = self.confirm(goal_id, confirm_token)
        goal.transition_to(GoalState.RUNNING)
        self.store.save_goal(goal)

        # Set up duration timer if max_duration is set
        if goal.max_duration is not None:
            self._start_timeout_timer(goal_id, goal.max_duration)

        log.info(f"Goal running: {goal_id}")
        return goal

    def status(self, goal_id: str) -> Optional[Goal]:
        """Get current goal status."""
        return self.store.get_goal(goal_id)

    def list_goals(self, state_filter: Optional[GoalState] = None) -> List[Goal]:
        """List goals, optionally filtered by state."""
        return self.store.list_goals(state_filter=state_filter)

    def mark_done(self, goal_id: str) -> Goal:
        """Mark a running goal as done."""
        goal = self._get_running_goal(goal_id)
        goal.transition_to(GoalState.DONE)
        self.store.save_goal(goal)
        self._cancel_timer(goal_id)
        self._finalize_deliverable(goal_id)
        return goal

    def mark_failed(self, goal_id: str) -> Goal:
        """Mark a running goal as failed."""
        goal = self._get_running_goal(goal_id)
        goal.transition_to(GoalState.FAILED)
        self.store.save_goal(goal)
        self._cancel_timer(goal_id)
        self._finalize_deliverable(goal_id)
        return goal

    def mark_budget_exhausted(self, goal_id: str) -> Goal:
        """Mark a running goal as budget_exhausted."""
        goal = self._get_running_goal(goal_id)
        goal.transition_to(GoalState.BUDGET_EXHAUSTED)
        self.store.save_goal(goal)
        self._cancel_timer(goal_id)
        self._finalize_deliverable(goal_id)
        return goal

    def mark_timeout(self, goal_id: str) -> Goal:
        """Mark a running goal as timed out."""
        goal = self._get_running_goal(goal_id)
        goal.transition_to(GoalState.TIMEOUT)
        self.store.save_goal(goal)
        self._cancel_timer(goal_id)
        self._finalize_deliverable(goal_id)
        return goal

    def pause(self, goal_id: str) -> Goal:
        """Pause a running goal. Cancels timeout timer; resume to continue."""
        goal = self._get_running_goal(goal_id)
        goal.transition_to(GoalState.PAUSED)
        self.store.save_goal(goal)
        self._cancel_timer(goal_id)
        log.info(f"Goal paused: {goal_id}")
        return goal

    def resume(self, goal_id: str) -> Goal:
        """Resume a paused goal. Restarts timeout timer with remaining duration."""
        goal = self._get_paused_goal(goal_id)
        goal.transition_to(GoalState.RUNNING)
        self.store.save_goal(goal)
        if goal.max_duration and goal.started_at:
            started = datetime.fromisoformat(goal.started_at)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = goal.max_duration - elapsed
            if remaining > 0:
                self._start_timeout_timer(goal_id, remaining)
        log.info(f"Goal resumed: {goal_id}")
        return goal

    # ── Budget ──

    def can_spawn_runner(self, goal_id: str) -> bool:
        """Check if a new runner can be spawned within budget."""
        goal = self.store.get_goal(goal_id)
        if goal is None:
            return False
        active = self.store.count_active_sessions(goal_id)
        return active < goal.max_concurrent_runners

    def spawn_runner(self, goal_id: str, session_id: Optional[str] = None) -> str:
        """Spawn a runner session for a goal. Returns session_id."""
        with self._spawn_lock:
            if not self.can_spawn_runner(goal_id):
                goal = self.store.get_goal(goal_id)
                active = self.store.count_active_sessions(goal_id)
                if active == 0:
                    self.mark_budget_exhausted(goal_id)
                    raise BudgetExhausted(f"Budget exhausted for goal {goal_id}")
                raise BudgetExhausted(
                    f"Budget full for goal {goal_id} ({active}/{goal.max_concurrent_runners})"
                )

            if session_id is None:
                session_id = str(uuid.uuid4())
            self.store.add_session(goal_id, session_id)
            self.store.update_session(goal_id, session_id, 'running')

            goal = self.store.get_goal(goal_id)
            goal.active_runners = self.store.count_active_sessions(goal_id)
            self.store.save_goal(goal)

        return session_id

    def complete_runner(self, goal_id: str, session_id: str,
                        final_answer: Optional[str] = None) -> None:
        """Mark a runner session as completed."""
        self.store.update_session(goal_id, session_id, 'completed', final_answer)
        goal = self.store.get_goal(goal_id)
        goal.active_runners = self.store.count_active_sessions(goal_id)
        self.store.save_goal(goal)

    def fail_runner(self, goal_id: str, session_id: str,
                    final_answer: Optional[str] = None) -> None:
        """Mark a runner session as failed."""
        self.store.update_session(goal_id, session_id, 'failed', final_answer)
        goal = self.store.get_goal(goal_id)
        goal.active_runners = self.store.count_active_sessions(goal_id)
        self.store.save_goal(goal)

    # ── Deliverable ──

    def get_deliverable(self, goal_id: str) -> Optional[GoalDeliverable]:
        """Get the deliverable for a goal."""
        return self.store.get_deliverable(goal_id)

    def _finalize_deliverable(self, goal_id: str) -> None:
        """Aggregate deliverable from sessions and persist."""
        sessions = self.store.get_sessions(goal_id)
        summaries = []
        text_parts = []
        for s in sessions:
            summary = {
                'session_id': s['session_id'],
                'status': s['status'],
                'final_answer': s.get('final_answer'),
            }
            summaries.append(summary)
            if s.get('final_answer'):
                text_parts.append(
                    f"[{s['session_id'][:8]}] {s['status']}: {s['final_answer']}"
                )

        content = "\n".join(text_parts) if text_parts else "(no deliverable content)"
        d = GoalDeliverable(
            goal_id=goal_id,
            content=content,
            session_summaries=summaries,
        )
        self.store.save_deliverable(d)

    # ── Recovery ──

    def recover(self) -> List[Goal]:
        """Recover running and paused goals after a restart. Re-applies timeout timers for running goals."""
        recovered = []
        # Recover running goals - re-apply timeout timers
        for goal in self.store.get_running_goals():
            if goal.max_duration and goal.started_at:
                started = datetime.fromisoformat(goal.started_at)
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                remaining = goal.max_duration - elapsed
                if remaining <= 0:
                    self.mark_timeout(goal.id)
                    continue
                else:
                    self._start_timeout_timer(goal.id, remaining)
            recovered.append(goal)
            log.info(f"Recovered running goal: {goal.id}")
        # Recover paused goals - no timer needed, they stay paused
        for goal in self.store.get_paused_goals():
            recovered.append(goal)
            log.info(f"Recovered paused goal: {goal.id}")
        return recovered

    # ── Internal ──

    def _get_running_goal(self, goal_id: str) -> Goal:
        goal = self.store.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        if goal.state != GoalState.RUNNING:
            raise InvalidTransition(f"Goal is {goal.state.value}, not running")
        return goal

    def _get_paused_goal(self, goal_id: str) -> Goal:
        goal = self.store.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id}")
        if goal.state != GoalState.PAUSED:
            raise InvalidTransition(f"Goal is {goal.state.value}, not paused")
        return goal

    def _start_timeout_timer(self, goal_id: str, duration: float) -> None:
        self._cancel_timer(goal_id)
        timer = threading.Timer(duration, self._on_timeout, args=(goal_id,))
        timer.daemon = True
        timer.start()
        self._timers[goal_id] = timer

    def _cancel_timer(self, goal_id: str) -> None:
        timer = self._timers.pop(goal_id, None)
        if timer:
            timer.cancel()

    def shutdown(self) -> None:
        """Cancel all timers. Call before closing the store."""
        for goal_id in list(self._timers.keys()):
            self._cancel_timer(goal_id)

    def _on_timeout(self, goal_id: str) -> None:
        """Callback when a goal's max_duration is reached."""
        try:
            self.mark_timeout(goal_id)
            log.info(f"Goal timed out: {goal_id}")
        except (InvalidTransition, ValueError):
            pass  # already in terminal state
