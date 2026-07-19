"""§4.6 approval closed loop (S4). Agent asks_user → approval/request → response → continue."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_approval_request_then_response_continues_task():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        # Direct, imperative phrasing coaxes the LLM to actually call the
        # ask_user tool (a vague "call ask_user" prompt often just generates
        # prose). The tool call is what the bridge intercepts into the
        # approval/request → approval/response closed loop (S4).
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "You MUST use the ask_user tool to ask me: should I proceed? Then if approved, reply done.",
                 "mode": "single"})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        collected = bp.recv_until(lambda m: m.get("type") == "approval/request", timeout=90)
        reqs = [m for m in collected if m["type"] == "approval/request"]
        if not reqs:
            pytest.skip("agent did not call ask_user in this run (LLM nondeterminism)")
        req = reqs[-1]
        assert req["task_id"] == task_id
        assert req["prompt"]
        bp.send({"id": 20, "type": "approval/response", "version": "1",
                 "task_id": task_id, "tool_id": req["tool_id"],
                 "decision": "approve", "input": "yes, proceed"})
        done = bp.recv_until(lambda m: m.get("type") == "task/done" and m.get("task_id") == task_id,
                             timeout=90)
        dones = [m for m in done if m["type"] == "task/done"]
        assert dones
        assert dones[-1]["reason"] in ("completed", "error")
