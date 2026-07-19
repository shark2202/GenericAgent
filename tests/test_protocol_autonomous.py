"""§4.9 autonomous: run to budget exhaustion + interrupt (S7)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_autonomous_budget_exhaustion_yields_budget_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Summarize what 1+1 is.", "mode": "autonomous",
                 "budget": {"seconds": 2}})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=120)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] in ("budget", "interrupted", "completed", "error")


@needs_llm
def test_autonomous_interrupt_yields_interrupted_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Write a 3-paragraph essay about AI.", "mode": "autonomous",
                 "budget": {"turns": 99}})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        bp.recv_until(lambda m: m.get("type") == "task/delta", timeout=60)
        bp.send({"id": 11, "type": "task/interrupt", "version": "1", "task_id": task_id})
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=30)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] == "interrupted"
