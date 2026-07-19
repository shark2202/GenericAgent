"""§4.5 interrupt a running task → task/done{reason:interrupted} (S3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_interrupt_running_task_yields_interrupted_done():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        # A multi-paragraph prompt keeps the LLM generating long enough that
        # the interrupt lands mid-generation (so abort()'s stop_sig actually
        # fires inside agent_runner_loop's for-chunk loop). A "very long essay"
        # prompt made the LLM run far past the test's timeout — kept short so
        # the interrupt test is bounded.
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Write a 3-paragraph essay about cats.",
                 "mode": "single"})
        ack = bp.recv(timeout=5)
        task_id = ack["task_id"]
        # wait until running (first delta), then interrupt
        bp.recv_until(lambda m: m.get("type") == "task/delta", timeout=30)
        bp.send({"id": 11, "type": "task/interrupt", "version": "1", "task_id": task_id})
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                                  timeout=60)
        dones = [m for m in collected if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] == "interrupted"
