#!/usr/bin/env python3
"""
E2E tests for goal_controller - CLI-level integration tests covering §6 scenarios:
  6.1 propose → run → multi-session spawn → status/deliverable
  6.2 budget exhaustion & timeout termination semantics
  6.3 Core restart recovery (goal persists across controller instances)

Note: run(goal_id, confirm_token) internally calls confirm(), so the correct
lifecycle is: propose → run (not propose → confirm → run).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

GA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOAL_CLI = os.path.join(GA_ROOT, "ga_cli", "goal_cli.py")


def _run_cli(*args, db_path=None):
    """Run goal_cli.py with given subcommand args, return (stdout, stderr, returncode)."""
    env = os.environ.copy()
    if db_path:
        env["GA_GOALS_DB"] = db_path
    result = subprocess.run(
        [sys.executable, GOAL_CLI] + list(args),
        capture_output=True, text=True, timeout=15,
        cwd=GA_ROOT, env=env,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _parse_goal_id(output):
    """Extract goal ID from CLI output like '✅ Goal proposed: abc123'."""
    m = re.search(r"Goal\s+\w+:\s+([\w-]+)", output)
    return m.group(1) if m else None


def _parse_confirm_token(output):
    """Extract confirm token from CLI output."""
    m = re.search(r"Confirm token:\s+([\w-]+)", output)
    return m.group(1) if m else None


class TestE2EFullLifecycle(unittest.TestCase):
    """§6.1: propose → run → status → done → deliverable (CLI form)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except PermissionError:
            pass

    def test_full_lifecycle_cli(self):
        """E2E: propose → run → status → done → deliverable via CLI."""
        # 1. Propose
        out, err, rc = _run_cli("propose", "Build feature X",
                                "--supervisor", "alice",
                                "--max-runners", "3",
                                "--max-duration", "600",
                                db_path=self.db_path)
        self.assertEqual(rc, 0, f"propose failed: {err}")
        goal_id = _parse_goal_id(out)
        self.assertIsNotNone(goal_id, f"no goal_id in output: {out}")
        token = _parse_confirm_token(out)
        self.assertIsNotNone(token, f"no token in output: {out}")

        # 2. Run (does confirm + run internally, one-time token)
        out, err, rc = _run_cli("run", goal_id, "--token", token,
                                db_path=self.db_path)
        self.assertEqual(rc, 0, f"run failed: {err}")
        self.assertIn("running", out.lower())

        # 3. Status
        out, err, rc = _run_cli("status", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0, f"status failed: {err}")
        self.assertIn("running", out.lower())
        self.assertIn(goal_id, out)

        # 4. List (should find the goal)
        out, err, rc = _run_cli("list", "--state", "running", db_path=self.db_path)
        self.assertEqual(rc, 0, f"list failed: {err}")
        self.assertIn(goal_id, out)

        # 5. Mark done
        out, err, rc = _run_cli("done", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0, f"done failed: {err}")
        self.assertIn("done", out.lower())

        # 6. Deliverable (should exist after terminal state)
        out, err, rc = _run_cli("deliverable", goal_id, "--json",
                                db_path=self.db_path)
        self.assertEqual(rc, 0, f"deliverable failed: {err}")
        data = json.loads(out)
        self.assertEqual(data["goal_id"], goal_id)
        self.assertIn("content", data)

    def test_confirm_then_cannot_run(self):
        """E2E: confirm consumes token, so run fails afterwards (one-time token)."""
        out, err, rc = _run_cli("propose", "Token test",
                                "--max-runners", "1",
                                db_path=self.db_path)
        self.assertEqual(rc, 0)
        goal_id = _parse_goal_id(out)
        token = _parse_confirm_token(out)

        # Confirm first (consumes token)
        out, _, rc = _run_cli("confirm", goal_id, "--token", token,
                              db_path=self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("confirmed", out.lower())

        # Run with same token should fail (token already consumed)
        out, err, rc = _run_cli("run", goal_id, "--token", token,
                                db_path=self.db_path)
        self.assertNotEqual(rc, 0, "run should fail when token already consumed")

    def test_multi_session_spawn_via_api(self):
        """E2E: propose → run → spawn multiple sessions → verify budget."""
        from ga_cli.goal_controller import GoalController, GoalStore, GoalState

        store = GoalStore(self.db_path)
        ctrl = GoalController(store)
        try:
            goal = ctrl.propose(proposal="Multi-session test",
                                max_concurrent_runners=2, max_duration=300)
            goal = ctrl.run(goal.id, goal.confirm_token)

            # Spawn 2 sessions (budget = 2)
            s1 = ctrl.spawn_runner(goal.id)
            s2 = ctrl.spawn_runner(goal.id)
            self.assertIsNotNone(s1)
            self.assertIsNotNone(s2)

            # 3rd spawn should fail (budget exhausted)
            self.assertFalse(ctrl.can_spawn_runner(goal.id))

            # Complete one session, freeing a slot
            ctrl.complete_runner(goal.id, s1, "Result 1")
            self.assertTrue(ctrl.can_spawn_runner(goal.id))

            # Verify sessions via store
            sessions = store.get_sessions(goal.id)
            self.assertEqual(len(sessions), 2)
            statuses = {s["session_id"]: s["status"] for s in sessions}
            self.assertEqual(statuses[s1], "completed")
            self.assertEqual(statuses[s2], "running")

            # Finish goal
            ctrl.complete_runner(goal.id, s2, "Result 2")
            goal = ctrl.mark_done(goal.id)
            self.assertEqual(goal.state, GoalState.DONE)

            # Deliverable should aggregate both sessions
            d = store.get_deliverable(goal.id)
            self.assertIsNotNone(d)
            self.assertIn("Result 1", d.content)
            self.assertIn("Result 2", d.content)
        finally:
            ctrl.shutdown()
            store.close()


class TestE2EBudgetAndTimeout(unittest.TestCase):
    """§6.2: Budget exhaustion & timeout termination semantics."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except PermissionError:
            pass

    def test_budget_exhausted_transition(self):
        """When all runners are active and goal is marked budget_exhausted, state is correct."""
        from ga_cli.goal_controller import GoalController, GoalStore, GoalState

        store = GoalStore(self.db_path)
        ctrl = GoalController(store)
        try:
            goal = ctrl.propose(proposal="Budget test", max_concurrent_runners=1)
            goal = ctrl.run(goal.id, goal.confirm_token)

            # Spawn 1 runner (budget=1, now exhausted)
            sid = ctrl.spawn_runner(goal.id)
            self.assertFalse(ctrl.can_spawn_runner(goal.id))

            # Mark budget_exhausted
            goal = ctrl.mark_budget_exhausted(goal.id)
            self.assertEqual(goal.state, GoalState.BUDGET_EXHAUSTED)
            self.assertIsNotNone(goal.finished_at)

            # Deliverable should exist
            d = store.get_deliverable(goal.id)
            self.assertIsNotNone(d)
        finally:
            ctrl.shutdown()
            store.close()

    def test_timeout_termination(self):
        """When max_duration is reached, goal transitions to timeout state."""
        from ga_cli.goal_controller import GoalController, GoalStore, GoalState

        store = GoalStore(self.db_path)
        ctrl = GoalController(store)
        try:
            # Set very short timeout (2 seconds)
            goal = ctrl.propose(proposal="Timeout test",
                                max_concurrent_runners=1, max_duration=2)
            goal = ctrl.run(goal.id, goal.confirm_token)
            self.assertEqual(goal.state, GoalState.RUNNING)

            # Wait for timeout to fire (2s duration + 1s margin)
            time.sleep(3.5)

            # Goal should now be in timeout state
            goal = ctrl.status(goal.id)
            self.assertEqual(goal.state, GoalState.TIMEOUT)
            self.assertIsNotNone(goal.finished_at)

            # Deliverable should exist
            d = store.get_deliverable(goal.id)
            self.assertIsNotNone(d)
        finally:
            ctrl.shutdown()
            store.close()

    def test_budget_exhausted_cli(self):
        """E2E CLI: propose → run → done → verify deliverable (budget path)."""
        out, _, rc = _run_cli("propose", "Budget CLI test",
                              "--max-runners", "1",
                              db_path=self.db_path)
        self.assertEqual(rc, 0)
        goal_id = _parse_goal_id(out)
        token = _parse_confirm_token(out)

        out, _, rc = _run_cli("run", goal_id, "--token", token,
                              db_path=self.db_path)
        self.assertEqual(rc, 0)

        # Mark done (budget not exhausted but goal completed)
        out, _, rc = _run_cli("done", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0)

        # Verify deliverable exists
        out, _, rc = _run_cli("deliverable", goal_id, "--json",
                              db_path=self.db_path)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["goal_id"], goal_id)


class TestE2ERestartRecovery(unittest.TestCase):
    """§6.3: Core restart recovery — goal persists across controller instances."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except PermissionError:
            pass

    def test_running_goal_survives_restart(self):
        """After controller shutdown + restart, running goal state is recovered."""
        from ga_cli.goal_controller import GoalController, GoalStore, GoalState

        # Phase 1: Create and run a goal
        store1 = GoalStore(self.db_path)
        ctrl1 = GoalController(store1)
        goal = ctrl1.propose(proposal="Recovery test", max_concurrent_runners=2)
        goal_id = goal.id
        token = goal.confirm_token
        goal = ctrl1.run(goal_id, token)
        # Spawn a runner
        sid = ctrl1.spawn_runner(goal_id)
        ctrl1.shutdown()
        store1.close()

        # Phase 2: Restart controller with same DB
        store2 = GoalStore(self.db_path)
        ctrl2 = GoalController(store2)
        try:
            # Goal should still be running
            goal = ctrl2.status(goal_id)
            self.assertIsNotNone(goal)
            self.assertEqual(goal.state, GoalState.RUNNING)
            self.assertEqual(goal.active_runners, 1)

            # Can still complete the runner
            ctrl2.complete_runner(goal_id, sid, "Final answer after restart")
            goal = ctrl2.mark_done(goal_id)
            self.assertEqual(goal.state, GoalState.DONE)

            # Deliverable should contain the answer
            d = store2.get_deliverable(goal_id)
            self.assertIsNotNone(d)
            self.assertIn("Final answer after restart", d.content)
        finally:
            ctrl2.shutdown()
            store2.close()

    def test_proposed_goal_recoverable_after_restart(self):
        """A proposed goal survives restart; can be run with original token."""
        from ga_cli.goal_controller import GoalController, GoalStore, GoalState

        store1 = GoalStore(self.db_path)
        ctrl1 = GoalController(store1)
        goal = ctrl1.propose(proposal="Pending recovery", max_concurrent_runners=1)
        goal_id = goal.id
        token = goal.confirm_token
        ctrl1.shutdown()
        store1.close()

        # Restart
        store2 = GoalStore(self.db_path)
        ctrl2 = GoalController(store2)
        try:
            goal = ctrl2.status(goal_id)
            self.assertIsNotNone(goal)
            self.assertEqual(goal.state, GoalState.PROPOSED)

            # Can run directly (confirm+run in one step)
            goal = ctrl2.run(goal_id, token)
            self.assertEqual(goal.state, GoalState.RUNNING)

            # Complete the goal
            goal = ctrl2.mark_done(goal_id)
            self.assertEqual(goal.state, GoalState.DONE)
        finally:
            ctrl2.shutdown()
            store2.close()

    def test_cli_lifecycle_across_restarts(self):
        """CLI-level: status persists across separate CLI invocations (same DB)."""
        # 1. Propose via CLI
        out, err, rc = _run_cli("propose", "CLI restart test",
                                "--max-runners", "1",
                                db_path=self.db_path)
        self.assertEqual(rc, 0, f"propose failed: {err}")
        goal_id = _parse_goal_id(out)
        token = _parse_confirm_token(out)

        # 2. Run via CLI (confirm+run)
        out, err, rc = _run_cli("run", goal_id, "--token", token,
                                db_path=self.db_path)
        self.assertEqual(rc, 0, f"run failed: {err}")

        # 3. Status via CLI (separate process = "restart")
        out, err, rc = _run_cli("status", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0, f"status failed: {err}")
        self.assertIn("running", out.lower())

        # 4. Done via CLI
        out, _, rc = _run_cli("done", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0)

        # 5. Status again (another "restart")
        out, _, rc = _run_cli("status", goal_id, db_path=self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("done", out.lower())

        # 6. Deliverable via CLI
        out, _, rc = _run_cli("deliverable", goal_id, "--json",
                              db_path=self.db_path)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["goal_id"], goal_id)


if __name__ == "__main__":
    unittest.main()
