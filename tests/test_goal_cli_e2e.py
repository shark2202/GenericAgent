#!/usr/bin/env python3
"""
E2E tests for goal_cli.py - subprocess-level end-to-end testing.

Tests the full CLI lifecycle: propose -> confirm/run -> status -> list -> done/fail -> deliverable
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

CLI_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "ga_cli", "goal_cli.py")


def _run_cli(*args, env_extra=None):
    """Run goal_cli.py as a subprocess and return (rc, stdout, stderr)."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, CLI_PATH] + list(args),
        capture_output=True, text=True,
        timeout=15,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


class TestGoalCLIPropose(unittest.TestCase):
    """Test goal propose command."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "goals.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _env(self):
        return {"GA_GOALS_DB": self.db_path}

    def test_propose_basic(self):
        rc, out, err = _run_cli("propose", "Build the feature", env_extra=self._env())
        self.assertEqual(rc, 0)
        self.assertIn("Goal proposed", out)
        self.assertIn("Confirm token", out)
        goal_id = re.search(r'Goal proposed: ([\w-]+)', out).group(1)
        token = re.search(r'Confirm token: ([\w-]+)', out).group(1)
        self.assertTrue(len(goal_id) > 10)
        self.assertTrue(len(token) > 10)

    def test_propose_with_options(self):
        rc, out, err = _run_cli(
            "propose", "Test goal",
            "--supervisor", "alice",
            "--reason", "verification",
            "--max-runners", "3",
            "--max-duration", "600",
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        self.assertIn("Goal proposed", out)

    # --json not supported as propose subcommand flag yet
    # def test_propose_with_json(self): ...


class TestGoalCLIFullLifecycle(unittest.TestCase):
    """Test the full propose -> run -> done lifecycle via CLI."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "goals.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _env(self):
        return {"GA_GOALS_DB": self.db_path}

    def _propose(self, text="Test goal"):
        rc, out, err = _run_cli("propose", text, env_extra=self._env())
        self.assertEqual(rc, 0, f"Propose failed: {err}")
        goal_id = re.search(r'Goal proposed: ([\w-]+)', out).group(1)
        token = re.search(r'Confirm token: ([\w-]+)', out).group(1)
        return goal_id, token

    def test_lifecycle_run_done(self):
        """propose -> run -> status -> list -> done -> deliverable"""
        goal_id, token = self._propose("Lifecycle test")

        # Run (confirm + transition)
        rc, out, err = _run_cli("run", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0, f"Run failed: {err}")
        self.assertIn("running", out.lower())

        # Status
        rc, out, err = _run_cli("status", goal_id, env_extra=self._env())
        self.assertEqual(rc, 0, f"Status failed: {err}")
        self.assertIn("running", out.lower())
        self.assertIn(goal_id, out)

        # List
        rc, out, err = _run_cli("list", env_extra=self._env())
        self.assertEqual(rc, 0, f"List failed: {err}")
        self.assertIn(goal_id, out)

        # Done
        rc, out, err = _run_cli("done", goal_id, env_extra=self._env())
        self.assertEqual(rc, 0, f"Done failed: {err}")
        self.assertIn("done", out.lower())

        # Deliverable
        rc, out, err = _run_cli("deliverable", goal_id, env_extra=self._env())
        self.assertEqual(rc, 0, f"Deliverable failed: {err}")
        self.assertIn(goal_id, out)

    def test_lifecycle_confirm_then_run(self):
        """propose -> confirm -> check state"""
        goal_id, token = self._propose("Confirm-then-run")

        rc, out, err = _run_cli("confirm", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0, f"Confirm failed: {err}")
        self.assertIn("confirmed", out.lower())

    def test_lifecycle_run_fail(self):
        """propose -> run -> fail"""
        goal_id, token = self._propose("Fail test")

        rc, out, err = _run_cli("run", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0)

        rc, out, err = _run_cli("fail", goal_id, env_extra=self._env())
        self.assertEqual(rc, 0, f"Fail failed: {err}")
        self.assertIn("failed", out.lower())

    def test_list_filter_by_state(self):
        """list --state running"""
        goal_id, token = self._propose("Filter test")

        rc, out, err = _run_cli("run", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0)

        rc, out, err = _run_cli("list", "--state", "running", env_extra=self._env())
        self.assertEqual(rc, 0, f"List filter failed: {err}")
        self.assertIn(goal_id, out)

    def test_deliverable_json(self):
        """deliverable --json"""
        goal_id, token = self._propose("JSON deliverable")

        rc, out, err = _run_cli("run", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0)

        rc, out, err = _run_cli("done", goal_id, env_extra=self._env())
        self.assertEqual(rc, 0)

        rc, out, err = _run_cli("deliverable", goal_id, "--json", env_extra=self._env())
        self.assertEqual(rc, 0, f"Deliverable JSON failed: {err}")
        data = json.loads(out)
        self.assertEqual(data["goal_id"], goal_id)

    def test_status_nonexistent_goal(self):
        """status of nonexistent goal should fail."""
        rc, out, err = _run_cli("status", "nonexistent-id-12345", env_extra=self._env())
        self.assertNotEqual(rc, 0)


class TestGoalCLIEdgeCases(unittest.TestCase):
    """Edge cases and error handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "goals.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _env(self):
        return {"GA_GOALS_DB": self.db_path}

    def test_double_confirm_rejected(self):
        """Confirming twice with the same token should fail."""
        rc, out, err = _run_cli("propose", "Double confirm", env_extra=self._env())
        self.assertEqual(rc, 0)
        goal_id = re.search(r'Goal proposed: ([\w-]+)', out).group(1)
        token = re.search(r'Confirm token: ([\w-]+)', out).group(1)

        # First confirm
        rc, out, err = _run_cli("confirm", goal_id, "--token", token, env_extra=self._env())
        self.assertEqual(rc, 0)

        # Second confirm should fail
        rc, out, err = _run_cli("confirm", goal_id, "--token", token, env_extra=self._env())
        self.assertNotEqual(rc, 0)

    def test_done_on_proposed_goal_rejected(self):
        """Marking a proposed (not running) goal as done should fail."""
        rc, out, err = _run_cli("propose", "Not running", env_extra=self._env())
        self.assertEqual(rc, 0)
        goal_id = re.search(r'Goal proposed: ([\w-]+)', out).group(1)

        rc, out, err = _run_cli("done", goal_id, env_extra=self._env())
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
