"""§4.10 slash/cmd forward: injection-class + state-class (S8/S9).

Note: the bridge emits task/ack BEFORE slash/result for the injection-class
path (handle_slash_cmd spawns a task via the Task 6 pool, then emits the
slash/result ack-echo). The injection test reads until it sees slash/result
rather than asserting the very first frame.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize, needs_llm


def test_scheduler_returns_slash_unsupported():
    with BridgeProc() as bp:
        initialize(bp)
        bp.send({"id": 1, "type": "slash/cmd", "version": "1",
                 "cmd": "/scheduler", "args": ""})
        m = bp.recv(timeout=5)
        assert m["type"] == "error" and m["code"] == "slash_unsupported"


@needs_llm
def test_goal_injection_returns_slash_result_with_task_id():
    with BridgeProc(timeout=120) as bp:
        initialize(bp)
        bp.send({"id": 1, "type": "slash/cmd", "version": "1",
                 "cmd": "/goal", "args": "say hello"})
        # bridge emits task/ack then slash/result; collect until slash/result.
        # The protocol contract for the injection-class slash/cmd path is:
        # slash/result{task_id, injected_prompt} where task_id correlates to
        # the spawned task's upcoming task/ack/delta/done stream. We assert
        # that slash/result structure (design §6.6), not LLM completion —
        # /goal drops the agent into goal-mode SOP whose behavior (reading
        # memory files, tool calls, multi-turn continuation) is non-deterministic.
        collected = bp.recv_until(lambda m: m.get("type") == "slash/result",
                                  timeout=15)
        results = [m for m in collected if m["type"] == "slash/result"]
        assert results, "expected slash/result"
        m = results[-1]
        assert m.get("task_id")
        assert m.get("injected_prompt")
        # The spawned task should produce at least one delta (proves the
        # injected_prompt was put_task'd to a real GA and is streaming).
        deltas = bp.recv_until(lambda x: x.get("type") == "task/delta"
                                          and x.get("task_id") == m["task_id"],
                               timeout=30)
        assert any(x["type"] == "task/delta" for x in deltas)
