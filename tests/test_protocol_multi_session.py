"""§4.4 multi-session: two concurrent tasks, task_ids not crossed (S2)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


@needs_llm
def test_two_concurrent_tasks_have_distinct_ids_and_no_cross_talk():
    with BridgeProc(timeout=150) as bp:
        initialize(bp)
        bp.send({"id": 10, "type": "task/start", "version": "1",
                 "prompt": "Reply: A", "mode": "single"})
        bp.send({"id": 11, "type": "task/start", "version": "1",
                 "prompt": "Reply: B", "mode": "single"})
        ack1 = bp.recv(timeout=5); ack2 = bp.recv(timeout=5)
        assert ack1["type"] == "task/ack" and ack2["type"] == "task/ack"
        tids = {ack1["task_id"], ack2["task_id"]}
        assert len(tids) == 2  # distinct
        # collect until both done
        seen = set()
        collected = bp.recv_until(lambda m: m.get("type") == "task/done" and (seen.add(m["task_id"]) or len(seen) == 2),
                                  timeout=120, max_msgs=500)
        done_tids = {m["task_id"] for m in collected if m.get("type") == "task/done"}
        assert done_tids == tids  # both done, no leakage to other id
        delta_tids = {m["task_id"] for m in collected if m.get("type") == "task/delta"}
        assert delta_tids.issubset(tids)
