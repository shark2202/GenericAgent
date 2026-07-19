#!/usr/bin/env python3
"""
ga_cli/goal_cli.py - Goal lifecycle CLI subcommands

Usage: ga goal propose <text> [--supervisor S] [--reason R] [--max-runners N] [--max-duration SECONDS]
       ga goal confirm <id> --token <TOKEN>
       ga goal run <id> --token <TOKEN>
       ga goal status <id>
       ga goal list [--state STATE]
       ga goal done <id>
       ga goal fail <id>
       ga goal deliverable <id> [--json]
"""
import argparse
import json
import os
import sys
import tempfile
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ga_cli.goal_controller import (
    GoalController, GoalStore, GoalState, GoalDeliverable,
    InvalidTransition, ConfirmTokenError, BudgetExhausted,
)

# Default DB path - per-user, in project dir
_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "goals.db")


def _get_db_path(args):
    """Resolve DB path from args or default."""
    return getattr(args, 'db', None) or os.environ.get("GA_GOALS_DB", _DEFAULT_DB)


def _make_controller(args):
    """Create a GoalController with the appropriate DB."""
    db_path = _get_db_path(args)
    store = GoalStore(db_path)
    return GoalController(store), store


def _fmt_goal(g, verbose=False):
    """Format a single goal for display."""
    lines = [
        f"ID:       {g.id}",
        f"State:    {g.state.value}",
        f"Proposal: {g.proposal[:120]}{'…' if len(g.proposal) > 120 else ''}",
    ]
    if verbose or g.supervisor:
        lines.append(f"Supervisor: {g.supervisor or '-'}")
    if verbose or g.reason:
        lines.append(f"Reason:    {g.reason or '-'}")
    if verbose:
        lines.extend([
            f"Max runners: {g.max_concurrent_runners}",
            f"Max duration: {g.max_duration or '∞'}s",
            f"Active runners: {g.active_runners}",
            f"Created:  {g.created_at}" if g.created_at else "Created:  -",
            f"Started:  {g.started_at or '-'}",
            f"Finished: {g.finished_at or '-'}",
        ])
    if g.confirm_token and g.state == GoalState.PROPOSED:
        lines.append(f"Confirm token: {g.confirm_token}")
        if g.confirm_token_expires:
            remaining = g.confirm_token_expires - __import__('time').time()
            lines.append(f"Token expires in: {remaining:.0f}s")
    return "\n".join(lines)


# ── Subcommand handlers ────────────────────────────────────────

def cmd_propose(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.propose(
            proposal=args.proposal,
            supervisor=getattr(args, 'supervisor', '') or '',
            reason=getattr(args, 'reason', '') or '',
            max_concurrent_runners=getattr(args, 'max_runners', 1) or 1,
            max_duration=getattr(args, 'max_duration', None),
        )
        print(f"✅ Goal proposed: {goal.id}")
        print(f"   Confirm token: {goal.confirm_token}")
        print(f"   Token expires in 300s")
    finally:
        store.close()


def cmd_confirm(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.confirm(args.goal_id, args.token)
        print(f"✅ Goal confirmed: {goal.id}")
        print(f"   State: {goal.state.value}")
    except ConfirmTokenError as e:
        print(f"❌ Confirm failed: {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, InvalidTransition) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        store.close()


def cmd_run(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.run(args.goal_id, args.token)
        print(f"✅ Goal running: {goal.id}")
        print(f"   State: {goal.state.value}")

        # Spawn the runner subprocess. spawn_runner launches a daemon pump
        # thread that drains runner events and drives the goal FSM to a
        # terminal state. The daemon dies if the main thread exits, so we
        # must block here and poll until completion.
        session_id = ctrl.spawn_runner(goal.id)
        print(f"   Runner session: {session_id}")

        terminal = {
            GoalState.DONE, GoalState.FAILED,
            GoalState.BUDGET_EXHAUSTED, GoalState.TIMEOUT,
        }
        interval = float(getattr(args, 'poll_interval', None) or 0.5)
        last_state = goal.state
        start = time.time()
        cur = goal

        while cur.state not in terminal:
            cur = ctrl.status(args.goal_id)
            if cur is None:
                print(f"\n❌ Goal vanished while polling: {args.goal_id}", file=sys.stderr)
                sys.exit(1)
            if cur.state != last_state:
                sys.stderr.write("\n")
                print(f"   State: {cur.state.value}")
                last_state = cur.state
            if cur.state in terminal:
                break
            # heartbeat progress (stderr keeps stdout clean for the deliverable)
            elapsed = time.time() - start
            sys.stderr.write(f"\r   ⏳ {cur.state.value} ({elapsed:.0f}s)…")
            sys.stderr.flush()
            time.sleep(interval)

        # clear the heartbeat line
        sys.stderr.write("\r" + " " * 48 + "\r")
        sys.stderr.flush()

        if cur.state == GoalState.DONE:
            d = store.get_deliverable(goal.id)
            print("\n🏁 DONE")
            if d and d.content:
                print(d.content)
            else:
                print("(goal completed but produced no deliverable)")
        else:
            print(f"\n❌ Goal ended in state: {cur.state.value}", file=sys.stderr)
            sys.exit(1)
    except ConfirmTokenError as e:
        print(f"❌ Run failed (token): {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, InvalidTransition) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        store.close()


def cmd_status(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.status(args.goal_id)
        if goal is None:
            print(f"❌ Goal not found: {args.goal_id}", file=sys.stderr)
            sys.exit(1)
        print(_fmt_goal(goal, verbose=True))
    finally:
        store.close()


def cmd_list(args):
    ctrl, store = _make_controller(args)
    try:
        state_filter = None
        if getattr(args, 'state', None):
            try:
                state_filter = GoalState(args.state)
            except ValueError:
                print(f"❌ Invalid state: {args.state}. Valid: {[s.value for s in GoalState]}", file=sys.stderr)
                sys.exit(1)
        goals = ctrl.list_goals(state_filter=state_filter)
        if not goals:
            print("No goals found.")
            return
        print(f"Found {len(goals)} goal(s):\n")
        for g in goals:
            print(_fmt_goal(g))
            print()
    finally:
        store.close()


def cmd_done(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.mark_done(args.goal_id)
        print(f"✅ Goal done: {goal.id}")
        print(f"   State: {goal.state.value}")
    except (ValueError, InvalidTransition) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        store.close()


def cmd_fail(args):
    ctrl, store = _make_controller(args)
    try:
        goal = ctrl.mark_failed(args.goal_id)
        print(f"✅ Goal failed: {goal.id}")
        print(f"   State: {goal.state.value}")
    except (ValueError, InvalidTransition) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        store.close()


def cmd_deliverable(args):
    ctrl, store = _make_controller(args)
    try:
        d = ctrl.store.get_deliverable(args.goal_id)
        if d is None:
            print(f"❌ No deliverable for goal: {args.goal_id}", file=sys.stderr)
            sys.exit(1)
        if getattr(args, 'json', False):
            output = {
                "goal_id": d.goal_id,
                "content": d.content,
                "format": d.format,
                "created_at": d.created_at,
                "session_summaries": d.session_summaries,
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(f"Goal: {d.goal_id}")
            print(f"Format: {d.format}")
            print(f"Content:\n{d.content}")
            if d.session_summaries:
                print(f"\nSession summaries ({len(d.session_summaries)}):")
                for s in d.session_summaries:
                    print(f"  - {s}")
    finally:
        store.close()


# ── Main entry point ───────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ga goal",
        description="Goal lifecycle management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              ga goal propose "Build the feature" --supervisor alice --max-runners 3
              ga goal confirm <id> --token <TOKEN>
              ga goal run <id> --token <TOKEN>
              ga goal status <id>
              ga goal list --state running
              ga goal done <id>
              ga goal fail <id>
              ga goal deliverable <id> --json
        """),
    )
    parser.add_argument("--db", help="Path to goals SQLite DB (default: goals.db or $GA_GOALS_DB)")

    sub = parser.add_subparsers(dest="subcommand", help="Goal subcommand")

    # propose
    p = sub.add_parser("propose", help="Propose a new goal")
    p.add_argument("proposal", help="Goal proposal text")
    p.add_argument("--supervisor", "-s", default="", help="Supervisor name")
    p.add_argument("--reason", "-r", default="", help="Reason for the goal")
    p.add_argument("--max-runners", type=int, default=1, help="Max concurrent runners (default: 1)")
    p.add_argument("--max-duration", type=float, default=None, help="Max duration in seconds (default: unlimited)")

    # confirm
    p = sub.add_parser("confirm", help="Confirm a proposed goal")
    p.add_argument("goal_id", help="Goal ID")
    p.add_argument("--token", "-t", required=True, help="Confirm token")

    # run
    p = sub.add_parser("run", help="Confirm + run a goal")
    p.add_argument("goal_id", help="Goal ID")
    p.add_argument("--token", "-t", required=True, help="Confirm token")
    p.add_argument("--poll-interval", type=float, default=0.5,
                   help="Seconds between status polls while blocking (default: 0.5)")

    # status
    p = sub.add_parser("status", help="Show goal status")
    p.add_argument("goal_id", help="Goal ID")

    # list
    p = sub.add_parser("list", help="List goals")
    p.add_argument("--state", help="Filter by state (proposed/confirmed/running/done/failed/budget_exhausted/timeout/paused)")

    # done
    p = sub.add_parser("done", help="Mark a running goal as done")
    p.add_argument("goal_id", help="Goal ID")

    # fail
    p = sub.add_parser("fail", help="Mark a running goal as failed")
    p.add_argument("goal_id", help="Goal ID")

    # deliverable
    p = sub.add_parser("deliverable", help="Get goal deliverable")
    p.add_argument("goal_id", help="Goal ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return

    dispatch = {
        "propose": cmd_propose,
        "confirm": cmd_confirm,
        "run": cmd_run,
        "status": cmd_status,
        "list": cmd_list,
        "done": cmd_done,
        "fail": cmd_fail,
        "deliverable": cmd_deliverable,
    }

    handler = dispatch.get(args.subcommand)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
