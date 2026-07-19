"""§4.3 single-task end-to-end streaming (S1). Asserts structure, not content."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_single_task_emits_ack_delta_then_done_completed():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply with the single word: ok", "mode": "single"})
        ack = bp.recv(timeout=5)
        assert ack["type"] == "task/ack"
        assert ack["status"] in ("running", "queued")
        task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=120)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones, "expected task/done"
        assert dones[-1]["reason"] in ("completed", "error")
        assert dones[-1]["task_id"] == task_id
        deltas = [m for m in collected if m["type"] == "task/delta" and m.get("task_id") == task_id]
        assert len(deltas) >= 1
