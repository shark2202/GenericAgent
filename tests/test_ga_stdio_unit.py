"""ga_stdio internal-function unit tests (protocol layer, no subprocess spawn).

Covers Task 1 scope:
- Frame parse / serialize
- VERSION / SERVER_CAPABILITIES
- Handshake state machine: initialize -> ready; business-before-init -> not_initialized
- Required-field validation: missing id/type/version -> error
- E2 resilience: malformed JSON -> bad_json, loop continues
- Capability negotiation: unsupported cap -> capability_unsupported
- Unknown type routing
"""
import io
import json
import os
import queue
import sys
import threading

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import ga_stdio  # noqa: E402


# ---- brief step-1 tests: frame parse/serialize + VERSION/caps ----

def test_serialize_roundtrip():
    msg = {"id": 7, "type": "ready", "version": "1", "capabilities": ["streaming"]}
    line = ga_stdio._serialize(msg)
    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed == msg


def test_parse_line_valid_json():
    line = '{"id": 1, "type": "initialize", "version": "1", "capabilities": []}\n'
    msg = ga_stdio._parse_line(line)
    assert msg is not None
    assert msg["type"] == "initialize"
    assert msg["version"] == "1"


def test_parse_line_bad_json_returns_none():
    """Malformed JSON line returns None so caller can emit bad_json error (E2)."""
    assert ga_stdio._parse_line("not json at all\n") is None
    assert ga_stdio._parse_line('{"id": 1, "type":}\n') is None


def test_version_is_major_only_string_one():
    assert ga_stdio.VERSION == "1"


def test_server_capabilities_include_all_v1_caps():
    expected = {"streaming", "multi-session", "autonomous", "approval",
                "mcp", "slash", "llm-switch", "session-resume"}
    assert expected.issubset(set(ga_stdio.SERVER_CAPABILITIES))


# ---- in-memory pipe harness for state-machine / end-to-end tests ----

def _run_bridge(lines):
    """Run BridgeCore.serve() over in-memory pipes; return list of parsed output msgs."""
    stdin = io.StringIO("".join(lines))
    stdout = io.StringIO()
    bridge = ga_stdio.BridgeCore(stdin=stdin, stdout=stdout)
    bridge.serve()
    out = stdout.getvalue()
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def _line(d):
    return json.dumps(d) + "\n"


# ---- handshake state machine ----

def test_handshake_initialize_emits_ready():
    out = _run_bridge([
        _line({"id": 1, "type": "initialize", "version": "1", "capabilities": []}),
    ])
    assert len(out) == 1
    msg = out[0]
    assert msg["type"] == "ready"
    assert msg["version"] == "1"
    # ready echoes the client id
    assert msg["id"] == 1
    # server advertises all v1 caps
    expected = {"streaming", "multi-session", "autonomous", "approval",
                "mcp", "slash", "llm-switch", "session-resume"}
    assert expected.issubset(set(msg["capabilities"]))
    # agent_info stub (real values wired in Task 6)
    ai = msg["agent_info"]
    assert ai["name"] == "GenericAgent"
    assert "mcp_connected" in ai
    assert "llm_count" in ai


def test_business_before_initialize_returns_not_initialized():
    out = _run_bridge([
        _line({"id": 5, "type": "task/start", "version": "1", "prompt": "hi"}),
    ])
    assert len(out) == 1
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "not_initialized"
    # original_id associates the triggering message (E3)
    assert err["original_id"] == 5


def test_ready_then_task_start_returns_ack():
    """After initialize, task/start → task/ack{status:running} (Task 2 contract).

    Replaces the Task-1 expectation (task/start → unknown_type). _spawn_ga is
    stubbed so no real GA engine / LLM is constructed; the fake dq blocks the
    drain thread so the synchronously-emitted task/ack can be read without
    racing task/done writes.
    """
    stdin = io.StringIO("".join([
        _line({"id": 1, "type": "initialize", "version": "1", "capabilities": []}),
        _line({"id": 2, "type": "task/start", "version": "1", "prompt": "hi"}),
    ]))
    stdout = io.StringIO()
    bridge = ga_stdio.BridgeCore(stdin=stdin, stdout=stdout)
    release = threading.Event()

    class _BlockingDQ:
        def get(self, timeout=None):
            release.wait(timeout=10.0)
            return {"done": "ok", "source": "user", "turn": 0, "outputs": ["ok"]}

    class _FakeGA:
        def put_task(self, query, source="user", images=None):
            return _BlockingDQ()
        def shutdown(self):
            pass

    bridge._spawn_ga = lambda: _FakeGA()
    bridge.serve()
    out = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert out[0]["type"] == "ready"
    ack = [m for m in out if m["type"] == "task/ack"][0]
    assert ack["status"] == "running"
    assert ack["task_id"].startswith("t")
    assert ack["id"] == 2  # echoes client id
    assert ack["version"] == "1"
    # release the parked drain thread + join so no thread leaks
    ctxs = list(bridge.pool.values())
    release.set()
    for c in ctxs:
        if c.thread is not None:
            c.thread.join(timeout=2.0)


# ---- required-field validation ----

def test_missing_id_returns_error():
    out = _run_bridge([
        _line({"type": "initialize", "version": "1", "capabilities": []}),
    ])
    assert len(out) == 1
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "bad_request"
    # no client id to associate -> null
    assert err["original_id"] is None


def test_missing_type_returns_error():
    out = _run_bridge([
        _line({"id": 9, "version": "1", "capabilities": []}),
    ])
    assert len(out) == 1
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "bad_request"
    assert err["original_id"] == 9


def test_missing_version_returns_error():
    out = _run_bridge([
        _line({"id": 9, "type": "initialize", "capabilities": []}),
    ])
    assert len(out) == 1
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "bad_request"
    assert err["original_id"] == 9


# ---- E2 resilience: malformed JSON does not break the loop ----

def test_malformed_json_emits_bad_json_and_continues():
    out = _run_bridge([
        "not json at all\n",
        _line({"id": 1, "type": "initialize", "version": "1", "capabilities": []}),
    ])
    assert len(out) == 2
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "bad_json"
    # bad_json cannot parse an id -> original_id is null (E2)
    assert err["original_id"] is None
    # loop continued: initialize still processed -> ready
    assert out[1]["type"] == "ready"


# ---- capability negotiation ----

def test_capability_unsupported_returns_error():
    out = _run_bridge([
        _line({"id": 1, "type": "initialize", "version": "1",
               "capabilities": ["nonexistent-cap"]}),
    ])
    assert len(out) == 1
    err = out[0]
    assert err["type"] == "error"
    assert err["code"] == "capability_unsupported"
    assert err["original_id"] == 1
    # server reports what it lacks
    assert "nonexistent-cap" in err.get("missing", [])


# ---- unknown type routing ----

def test_unknown_type_returns_unknown_type():
    out = _run_bridge([
        _line({"id": 1, "type": "initialize", "version": "1", "capabilities": []}),
        _line({"id": 2, "type": "totally_made_up", "version": "1"}),
    ])
    assert len(out) == 2
    assert out[0]["type"] == "ready"
    err = out[1]
    assert err["type"] == "error"
    assert err["code"] == "unknown_type"
    assert err["original_id"] == 2


# ---- Task 2: drain_display_queue + task/start → task/ack ----

def test_drain_display_queue_emits_delta_then_done():
    """drain turns {next,done} display_queue items into task/delta + task/done."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    dq = queue.Queue()
    dq.put({"next": "hello", "source": "user", "turn": 1, "outputs": ["hello"]})
    dq.put({"done": "hello world", "source": "user", "turn": 1, "outputs": ["hello world"]})
    sent = []
    core.send = lambda m: sent.append(m)  # capture
    task_ctx = ga_stdio.TaskCtx(ga=None, dq=dq, task_id="t1", thread=None)
    core.drain_display_queue(task_ctx)
    types = [m["type"] for m in sent]
    assert "task/delta" in types
    assert types[-1] == "task/done"
    done_msg = sent[-1]
    assert done_msg["task_id"] == "t1"
    assert done_msg["reason"] == "completed"
    assert done_msg["version"] == "1"
    assert "turn" in done_msg


def test_drain_display_queue_error_done_emits_error_reason():
    """A done carrying an error-shaped payload → task/done{reason:error}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    dq = queue.Queue()
    # agentmain except branch appends a fenced error block to done text (agentmain.py:239)
    dq.put({"done": "partial\n```\nValueError: boom @ file:1\n```", "source": "user",
            "turn": 0, "outputs": []})
    sent = []
    core.send = lambda m: sent.append(m)
    core.drain_display_queue(ga_stdio.TaskCtx(ga=None, dq=dq, task_id="t9", thread=None))
    assert sent[-1]["type"] == "task/done"
    assert sent[-1]["reason"] == "error"
    assert sent[-1].get("error")


def test_drain_display_queue_timeout_emits_error_done():
    """dq.get raising queue.Empty (timeout) → task/done{reason:error}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)

    class _EmptyQ:
        def get(self, timeout=None):
            raise queue.Empty()

    ctx = ga_stdio.TaskCtx(ga=None, dq=_EmptyQ(), task_id="t_to", thread=None)
    core.drain_display_queue(ctx)
    assert sent, "expected at least one message"
    assert sent[-1]["type"] == "task/done"
    assert sent[-1]["reason"] == "error"
    assert sent[-1].get("error")


def test_handle_task_start_assigns_task_id_and_regs_pool():
    """handle_task_start: assigns t<N> id, sends task/ack, registers TaskCtx in pool."""
    core = ga_stdio.BridgeCore(stdout=io.StringIO())
    sent = []
    core.send = lambda m: sent.append(m)
    release = threading.Event()

    class _BlockingDQ:
        def get(self, timeout=None):
            release.wait(timeout=10.0)
            return {"done": "ok", "source": "user", "turn": 0, "outputs": ["ok"]}

    class _FakeGA:
        def put_task(self, query, source="user", images=None):
            return _BlockingDQ()
        def shutdown(self):
            pass

    core._spawn_ga = lambda: _FakeGA()
    core.handle_task_start({"id": 7, "type": "task/start", "version": "1",
                            "prompt": "hello", "mode": "single"})
    core.handle_task_start({"id": 8, "type": "task/start", "version": "1",
                            "prompt": "again"})
    acks = [m for m in sent if m["type"] == "task/ack"]
    assert len(acks) == 2
    assert acks[0]["task_id"] == "t1"  # first task
    assert acks[0]["status"] == "running"
    assert acks[0]["id"] == 7
    assert acks[1]["task_id"] == "t2"  # second task increments
    assert acks[1]["id"] == 8
    # both registered in pool with their ctx
    assert set(core.pool.keys()) == {"t1", "t2"}
    assert core.pool["t1"].task_id == "t1"
    assert core.pool["t2"].task_id == "t2"
    assert core.pool["t1"].mode == "single"
    assert core.pool["t1"].thread is not None
    # ga→task routing populated (used by Task 3 hooks)
    assert core.ga_to_task[id(core.pool["t1"].ga)] == "t1"
    # release + join both drain threads so no thread leaks
    ctxs = list(core.pool.values())
    release.set()
    for c in ctxs:
        if c.thread is not None:
            c.thread.join(timeout=2.0)
