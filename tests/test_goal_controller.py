#!/usr/bin/env python3
"""Tests for goal_controller - TDD style, covers all spec scenarios."""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ga_cli.goal_controller import (
    Goal, GoalState, GoalDeliverable, GoalStore, GoalController,
    InvalidTransition, ConfirmTokenError, BudgetExhausted,
    VALID_TRANSITIONS, CONFIRM_TOKEN_TTL,
)


class TestGoalState(unittest.TestCase):
    """Test the GoalState enum and state machine rules."""

    def test_terminal_states(self):
        terminals = GoalState.terminal_states()
        self.assertIn(GoalState.DONE, terminals)
        self.assertIn(GoalState.FAILED, terminals)
        self.assertIn(GoalState.BUDGET_EXHAUSTED, terminals)
        self.assertIn(GoalState.TIMEOUT, terminals)
        self.assertNotIn(GoalState.PROPOSED, terminals)
        self.assertNotIn(GoalState.RUNNING, terminals)

    def test_is_terminal(self):
        self.assertTrue(GoalState.DONE.is_terminal())
        self.assertTrue(GoalState.FAILED.is_terminal())
        self.assertFalse(GoalState.PROPOSED.is_terminal())
        self.assertFalse(GoalState.RUNNING.is_terminal())

    def test_valid_transitions_proposed(self):
        self.assertIn(GoalState.CONFIRMED, VALID_TRANSITIONS[GoalState.PROPOSED])
        self.assertNotIn(GoalState.RUNNING, VALID_TRANSITIONS[GoalState.PROPOSED])

    def test_valid_transitions_confirmed(self):
        self.assertIn(GoalState.RUNNING, VALID_TRANSITIONS[GoalState.CONFIRMED])

    def test_valid_transitions_running(self):
        running = VALID_TRANSITIONS[GoalState.RUNNING]
        self.assertIn(GoalState.DONE, running)
        self.assertIn(GoalState.FAILED, running)
        self.assertIn(GoalState.BUDGET_EXHAUSTED, running)
        self.assertIn(GoalState.TIMEOUT, running)

    def test_no_transitions_from_terminal(self):
        self.assertNotIn(GoalState.DONE, VALID_TRANSITIONS)
        self.assertNotIn(GoalState.FAILED, VALID_TRANSITIONS)


class TestGoalModel(unittest.TestCase):
    """Test the Goal dataclass and transitions."""

    def test_default_goal_is_proposed(self):
        g = Goal()
        self.assertEqual(g.state, GoalState.PROPOSED)
        self.assertEqual(g.max_concurrent_runners, 1)

    def test_can_transition_to(self):
        g = Goal()
        self.assertTrue(g.can_transition_to(GoalState.CONFIRMED))
        self.assertFalse(g.can_transition_to(GoalState.RUNNING))

    def test_transition_to_sets_started_at(self):
        g = Goal(state=GoalState.CONFIRMED)
        self.assertIsNone(g.started_at)
        g.transition_to(GoalState.RUNNING)
        self.assertIsNotNone(g.started_at)

    def test_transition_to_sets_finished_at(self):
        g = Goal(state=GoalState.RUNNING)
        self.assertIsNone(g.finished_at)
        g.transition_to(GoalState.DONE)
        self.assertIsNotNone(g.finished_at)

    def test_invalid_transition_raises(self):
        g = Goal()  # proposed
        with self.assertRaises(InvalidTransition):
            g.transition_to(GoalState.RUNNING)

    def test_no_double_transition(self):
        g = Goal(state=GoalState.RUNNING)
        g.transition_to(GoalState.DONE)
        with self.assertRaises(InvalidTransition):
            g.transition_to(GoalState.RUNNING)  # can't go back


class TestGoalStore(unittest.TestCase):
    """Test SQLite persistence layer."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = GoalStore(self.tmp.name)

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_init_creates_tables(self):
        conn = self.store._get_conn()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        self.assertIn("goals", tables)
        self.assertIn("goal_deliverables", tables)
        self.assertIn("goal_sessions", tables)

    def test_save_and_get_goal(self):
        g = Goal(proposal="test goal", supervisor="alice")
        self.store.save_goal(g)
        loaded = self.store.get_goal(g.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.proposal, "test goal")
        self.assertEqual(loaded.supervisor, "alice")
        self.assertEqual(loaded.state, GoalState.PROPOSED)

    def test_get_nonexistent_goal(self):
        self.assertIsNone(self.store.get_goal("nonexistent"))

    def test_list_goals(self):
        self.store.save_goal(Goal(proposal="g1"))
        self.store.save_goal(Goal(proposal="g2"))
        goals = self.store.list_goals()
        self.assertEqual(len(goals), 2)

    def test_list_goals_with_filter(self):
        self.store.save_goal(Goal(proposal="g1", state=GoalState.PROPOSED))
        self.store.save_goal(Goal(proposal="g2", state=GoalState.RUNNING))
        proposed = self.store.list_goals(state_filter=GoalState.PROPOSED)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0].proposal, "g1")

    def test_delete_goal(self):
        g = Goal(proposal="to-delete")
        self.store.save_goal(g)
        self.assertTrue(self.store.delete_goal(g.id))
        self.assertIsNone(self.store.get_goal(g.id))

    def test_delete_nonexistent(self):
        self.assertFalse(self.store.delete_goal("nope"))

    def test_metadata_roundtrip(self):
        g = Goal(metadata={"key": "value", "num": 42})
        self.store.save_goal(g)
        loaded = self.store.get_goal(g.id)
        self.assertEqual(loaded.metadata["key"], "value")
        self.assertEqual(loaded.metadata["num"], 42)

    def test_deliverable_save_and_get(self):
        # Need parent goal row for FK
        g = Goal(id="test-gid", proposal="test goal")
        self.store.save_goal(g)
        d = GoalDeliverable(
            goal_id="test-gid",
            content="result text",
            session_summaries=[{"session_id": "s1", "status": "completed"}],
        )
        self.store.save_deliverable(d)
        loaded = self.store.get_deliverable("test-gid")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.content, "result text")
        self.assertEqual(len(loaded.session_summaries), 1)

    def test_session_lifecycle(self):
        # Need parent goal row for FK
        g = Goal(id="g1", proposal="test goal")
        self.store.save_goal(g)
        self.store.add_session("g1", "s1")
        self.store.update_session("g1", "s1", "running")
        self.assertEqual(self.store.count_active_sessions("g1"), 1)
        self.store.update_session("g1", "s1", "completed", "answer")
        self.assertEqual(self.store.count_active_sessions("g1"), 0)
        sessions = self.store.get_sessions("g1")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["final_answer"], "answer")


class TestGoalController(unittest.TestCase):
    """Test the high-level controller - covers all spec scenarios."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = GoalStore(self.tmp.name)
        self.ctrl = GoalController(self.store)

    def tearDown(self):
        self.ctrl.shutdown()
        self.store.close()
        try:
            os.unlink(self.tmp.name)
        except PermissionError:
            pass  # Windows: timer thread may still hold file briefly

    # ── Scenario: Propose creates a pending goal ──

    def test_propose_creates_pending_goal(self):
        goal = self.ctrl.propose(
            proposal="Build feature X",
            supervisor="alice",
            max_concurrent_runners=3,
            max_duration=3600,
        )
        self.assertEqual(goal.state, GoalState.PROPOSED)
        self.assertEqual(goal.proposal, "Build feature X")
        self.assertIsNotNone(goal.confirm_token)
        self.assertIsNotNone(goal.confirm_token_expires)

    def test_proposed_goal_persists(self):
        goal = self.ctrl.propose(proposal="test")
        loaded = self.store.get_goal(goal.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.state, GoalState.PROPOSED)

    # ── Scenario: Confirm with valid token ──

    def test_confirm_valid_token(self):
        goal = self.ctrl.propose(proposal="test")
        confirmed = self.ctrl.confirm(goal.id, goal.confirm_token)
        self.assertEqual(confirmed.state, GoalState.CONFIRMED)
        # Token is cleared after use
        self.assertIsNone(confirmed.confirm_token)

    # ── Scenario: Confirm with wrong token ──

    def test_confirm_wrong_token(self):
        goal = self.ctrl.propose(proposal="test")
        with self.assertRaises(ConfirmTokenError):
            self.ctrl.confirm(goal.id, "wrong-token")

    # ── Scenario: Confirm with expired token ──

    def test_confirm_expired_token(self):
        goal = self.ctrl.propose(proposal="test")
        # Manually expire the token
        goal.confirm_token_expires = time.time() - 1
        self.store.save_goal(goal)
        with self.assertRaises(ConfirmTokenError):
            self.ctrl.confirm(goal.id, goal.confirm_token)

    # ── Scenario: Run transitions to running ──

    def test_run_transitions_to_running(self):
        goal = self.ctrl.propose(proposal="test", max_duration=600)
        running = self.ctrl.run(goal.id, goal.confirm_token)
        self.assertEqual(running.state, GoalState.RUNNING)
        self.assertIsNotNone(running.started_at)

    # ── Scenario: Mark done ──

    def test_mark_done(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        done = self.ctrl.mark_done(goal.id)
        self.assertEqual(done.state, GoalState.DONE)
        self.assertIsNotNone(done.finished_at)

    # ── Scenario: Mark failed ──

    def test_mark_failed(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        failed = self.ctrl.mark_failed(goal.id)
        self.assertEqual(failed.state, GoalState.FAILED)

    # ── Scenario: Mark budget-exhausted ──

    def test_mark_budget_exhausted(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        be = self.ctrl.mark_budget_exhausted(goal.id)
        self.assertEqual(be.state, GoalState.BUDGET_EXHAUSTED)

    # ── Scenario: Mark timeout ──

    def test_mark_timeout(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        to = self.ctrl.mark_timeout(goal.id)
        self.assertEqual(to.state, GoalState.TIMEOUT)

    # ── Scenario: Cannot transition from terminal state ──

    def test_no_transition_from_done(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        self.ctrl.mark_done(goal.id)
        with self.assertRaises(InvalidTransition):
            self.ctrl.mark_failed(goal.id)

    # ── Scenario: Budget enforcement ──

    def test_can_spawn_within_budget(self):
        goal = self.ctrl.propose(proposal="test", max_concurrent_runners=2)
        self.ctrl.run(goal.id, goal.confirm_token)
        self.assertTrue(self.ctrl.can_spawn_runner(goal.id))

    def test_spawn_runner(self):
        goal = self.ctrl.propose(proposal="test", max_concurrent_runners=2)
        self.ctrl.run(goal.id, goal.confirm_token)
        sid = self.ctrl.spawn_runner(goal.id)
        self.assertIsNotNone(sid)

    def test_spawn_exceeds_budget(self):
        goal = self.ctrl.propose(proposal="test", max_concurrent_runners=1)
        self.ctrl.run(goal.id, goal.confirm_token)
        self.ctrl.spawn_runner(goal.id)
        # Second spawn should fail
        with self.assertRaises(BudgetExhausted):
            self.ctrl.spawn_runner(goal.id)

    def test_spawn_after_complete_frees_budget(self):
        goal = self.ctrl.propose(proposal="test", max_concurrent_runners=1)
        self.ctrl.run(goal.id, goal.confirm_token)
        sid = self.ctrl.spawn_runner(goal.id)
        self.ctrl.complete_runner(goal.id, sid, "done")
        # Can spawn again
        self.assertTrue(self.ctrl.can_spawn_runner(goal.id))
        sid2 = self.ctrl.spawn_runner(goal.id)
        self.assertIsNotNone(sid2)

    # ── Scenario: Deliverable aggregation ──

    def test_deliverable_after_completion(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        sid1 = self.ctrl.spawn_runner(goal.id)
        self.ctrl.complete_runner(goal.id, sid1, "Feature X built successfully")
        self.ctrl.mark_done(goal.id)

        d = self.ctrl.get_deliverable(goal.id)
        self.assertIsNotNone(d)
        self.assertIn("Feature X built successfully", d.content)
        self.assertEqual(len(d.session_summaries), 1)

    def test_deliverable_no_content(self):
        goal = self.ctrl.propose(proposal="test")
        self.ctrl.run(goal.id, goal.confirm_token)
        self.ctrl.mark_failed(goal.id)

        d = self.ctrl.get_deliverable(goal.id)
        self.assertIsNotNone(d)
        self.assertIn("no deliverable content", d.content)

    # ── Scenario: Cross-restart recovery ──

    def test_recovery_after_restart(self):
        goal = self.ctrl.propose(proposal="test", max_duration=600)
        self.ctrl.run(goal.id, goal.confirm_token)
        self.ctrl.spawn_runner(goal.id)

        # Simulate restart: create new controller with same DB
        self.store.close()
        store2 = GoalStore(self.tmp.name)
        ctrl2 = GoalController(store2)

        # Recovery should find the running goal
        recovered = ctrl2.recover()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, GoalState.RUNNING)
        store2.close()

    # ── Scenario: Timeout fires automatically ──

    def test_timeout_fires(self):
        goal = self.ctrl.propose(proposal="test", max_duration=0.1)
        self.ctrl.run(goal.id, goal.confirm_token)
        time.sleep(0.3)
        loaded = self.store.get_goal(goal.id)
        self.assertEqual(loaded.state, GoalState.TIMEOUT)

    # ── Scenario: List goals ──

    def test_list_goals(self):
        self.ctrl.propose(proposal="g1")
        self.ctrl.propose(proposal="g2")
        goals = self.ctrl.list_goals()
        self.assertEqual(len(goals), 2)

    def test_list_goals_by_state(self):
        g1 = self.ctrl.propose(proposal="g1")
        self.ctrl.run(g1.id, g1.confirm_token)
        self.ctrl.propose(proposal="g2")
        running = self.ctrl.list_goals(state_filter=GoalState.RUNNING)
        self.assertEqual(len(running), 1)

    # ── Scenario: Fail runner tracks state ──

    def test_fail_runner(self):
        goal = self.ctrl.propose(proposal="test", max_concurrent_runners=2)
        self.ctrl.run(goal.id, goal.confirm_token)
        sid = self.ctrl.spawn_runner(goal.id)
        self.ctrl.fail_runner(goal.id, sid, "crashed")
        sessions = self.store.get_sessions(goal.id)
        self.assertEqual(sessions[0]["status"], "failed")
        self.assertEqual(sessions[0]["final_answer"], "crashed")
        # Budget freed
        self.assertTrue(self.ctrl.can_spawn_runner(goal.id))


if __name__ == "__main__":
    unittest.main()
