"""§4.8 llm/list + llm/select + session/resume (S6).

Queries llm/session state on a *running* task (the ctx is released by
_release_task once task/done is emitted, so llm/list on a completed task
returns unknown_task — the bridge only exposes live task state). This is
the correct S6 lifecycle: ask_user-style introspection mid-task.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_llm_list_and_select_then_session_resume():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply: hello", "mode": "single"})
        ack = bp.recv(timeout=5); task_id = ack["task_id"]
        # Wait until the GA is alive and running (first delta) — querying before
        # _start_task_thread has populated ctx.ga returns unknown_task.
        bp.recv_until(lambda m: m.get("type") == "task/delta", timeout=30)
        bp.send({"id": 20, "type": "llm/list", "version": "1", "task_id": task_id})
        m = bp.recv(timeout=10)
        assert m["type"] == "llm/list"
        assert isinstance(m["llms"], list) and len(m["llms"]) >= 1
        assert "current_no" in m
        if len(m["llms"]) > 1:
            target = (m["current_no"] + 1) % len(m["llms"])
            bp.send({"id": 21, "type": "llm/select", "version": "1",
                     "task_id": task_id, "n": target})
            ack2 = bp.recv(timeout=10)
            assert ack2["type"] == "llm/ack"
        bp.send({"id": 22, "type": "session/resume", "version": "1",
                 "task_id": task_id, "history": [], "llm_no": 0})
        s = bp.recv(timeout=10)
        assert s["type"] == "session/ack"
