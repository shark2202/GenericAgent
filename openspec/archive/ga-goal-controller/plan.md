# Plan — ga-goal-controller

## Phase 1: Design Confirmation ✅
- Review design.md → confirmed complete
- State machine: proposed→confirmed→running→done|failed|budget_exhausted|timeout
- SQLite persistence with sessions/deliverables tables
- CLI subcommands: propose, confirm, run, status, list, done, fail

## Phase 2: Build ✅
- Implement `ga_cli/goal_controller.py` (548 lines)
  - GoalStateMachine with transitions
  - SQLite3 persistence layer
  - Confirm-token flow with TTL
  - max_concurrent_runners budget tracking
  - max_duration timeout with threading.Timer
  - Deliverable aggregation
  - shutdown() for timer cleanup
- CLI integration via argparse subcommands

## Phase 3: Verify ✅
- 44/44 unittests pass
- CLI workflow verified: propose→confirm→run→status→list→done
- Edge cases: invalid transitions, expired tokens, budget limits, timeouts

## Phase 4: Archive ✅
- Git commit: 6ed6e7f on feature/ga-core-cli
- All artifacts in worktree
