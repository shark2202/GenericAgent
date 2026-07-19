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
import time

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


# ---- Task 3: hook routing — tool_before→tool/call, turn_after→tool/result ----
#
# Covers design §6.3 / tasks.md §3.3 (tool events). Hook callbacks recover the
# GA instance via ctx['self'].parent (tool_before) or ctx['handler'].parent
# (turn_after), then reverse-lookup task_id in ga_to_task. The hook ctx is a
# locals() snapshot whose fields may drift across agent_loop versions, so
# callbacks use .get() throughout — the resilience tests pin that boundary.

@pytest.fixture(autouse=True)
def _isolate_hook_registry():
    """Snapshot/restore the global hook registry around each test.

    register_hooks() appends to plugins.hooks._registry (module-level). Without
    isolation, callbacks registered by serve() in one test would leak into the
    next test's trigger() call. We restore only the events this module touches
    (tool_before / turn_after / tool_after) so plugin callbacks for other events
    stay untouched.
    """
    from plugins.hooks import _registry
    saved = {e: list(_registry.get(e, []))
             for e in ("tool_before", "turn_after", "tool_after")}
    yield
    for e, fns in saved.items():
        _registry[e] = list(fns)


class _FakeGA:
    """Stand-in for GenericAgent so we can test ga_to_task reverse-lookup."""
    pass


class _FakeHandler:
    def __init__(self, parent):
        self.parent = parent


def test_resolve_ga_from_tool_before_ctx():
    """tool_before ctx has `self` = handler; GA = handler.parent."""
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    ctx = {"self": handler, "tool_name": "code_run", "args": {"x": 1}}
    assert ga_stdio._resolve_ga_from_ctx(ctx) is ga


def test_resolve_ga_from_turn_after_ctx():
    """turn_after ctx has `handler` (no `self`); GA = handler.parent."""
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    ctx = {"handler": handler, "tool_results": [], "turn": 1}
    assert ga_stdio._resolve_ga_from_ctx(ctx) is ga


def test_resolve_ga_returns_none_when_missing():
    """Resilience: missing handler/self → None (don't crash the hook)."""
    assert ga_stdio._resolve_ga_from_ctx({"tool_name": "x"}) is None
    assert ga_stdio._resolve_ga_from_ctx({}) is None


def test_tool_call_serialization_has_required_fields():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core._emit_tool_call(task_id="t3", tool_id="tool_1", name="code_run", args={"x": 1})
    m = sent[-1]
    assert m["type"] == "tool/call"
    assert m["task_id"] == "t3"
    assert m["tool_id"] == "tool_1"
    assert m["name"] == "code_run"
    assert m["args"] == {"x": 1}
    assert m["version"] == "1"
    assert "id" in m


def test_tool_result_from_turn_after_ctx():
    """turn_after callback walks tool_results list → one tool/result per entry."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    with core._pool_lock:
        core.ga_to_task[id(ga)] = "t5"
    sent = []
    core.send = lambda m: sent.append(m)
    tool_results = [{"tool_use_id": "tu_1", "content": "ok"},
                    {"tool_use_id": "tu_2", "content": "fail"}]
    ctx = {"handler": handler, "tool_results": tool_results, "turn": 3}
    core._on_turn_after(ctx)
    results = [m for m in sent if m["type"] == "tool/result"]
    assert len(results) == 2
    assert results[0]["tool_id"] == "tu_1"
    assert results[0]["content"] == "ok"
    assert results[0]["task_id"] == "t5"
    assert results[1]["tool_id"] == "tu_2"


# ---- register+trigger path: end-to-end hook wiring through the registry ----

def test_register_hooks_then_trigger_tool_before_emits_tool_call():
    """register_hooks() + plugins.hooks.trigger('tool_before', ctx) → tool/call frame.

    Verifies the global hook is wired: trigger calls our closure, which resolves
    the GA from ctx['self'].parent, reverse-looks-up task_id in ga_to_task,
    strips the internal _index/_tool_num keys agent_loop dispatch injects, and
    emits a versioned tool/call frame.
    """
    from plugins.hooks import trigger
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    with core._pool_lock:
        core.ga_to_task[id(ga)] = "t7"
    sent = []
    core.send = lambda m: sent.append(m)
    core.register_hooks()
    trigger("tool_before", {"self": handler, "tool_name": "code_run",
                           "args": {"x": 1, "_index": 0, "_tool_num": 1}})
    calls = [m for m in sent if m["type"] == "tool/call"]
    assert len(calls) == 1
    assert calls[0]["task_id"] == "t7"
    assert calls[0]["name"] == "code_run"
    assert calls[0]["args"] == {"x": 1}  # _index/_tool_num stripped
    assert calls[0]["tool_id"].startswith("tool_")
    assert calls[0]["version"] == "1"


def test_register_hooks_then_trigger_turn_after_emits_tool_results():
    """register_hooks() + trigger('turn_after', ctx) → one tool/result per entry."""
    from plugins.hooks import trigger
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    with core._pool_lock:
        core.ga_to_task[id(ga)] = "t8"
    sent = []
    core.send = lambda m: sent.append(m)
    core.register_hooks()
    trigger("turn_after", {"handler": handler, "tool_results": [
        {"tool_use_id": "tu_1", "content": "ok"}], "turn": 2})
    results = [m for m in sent if m["type"] == "tool/result"]
    assert len(results) == 1
    assert results[0]["tool_id"] == "tu_1"
    assert results[0]["content"] == "ok"
    assert results[0]["task_id"] == "t8"
    assert results[0]["version"] == "1"


def test_tool_before_skips_when_ga_not_in_pool():
    """handler.parent is a GA not registered in ga_to_task → no frame sent."""
    from plugins.hooks import trigger
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    sent = []
    core.send = lambda m: sent.append(m)
    core.register_hooks()
    trigger("tool_before", {"self": handler, "tool_name": "code_run", "args": {}})
    assert sent == []  # no task_id reverse-lookup → skip silently


def test_tool_before_resilience_missing_tool_name():
    """ctx without 'tool_name' must not crash the hook (.get fallback)."""
    from plugins.hooks import trigger
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGA()
    handler = _FakeHandler(ga)
    with core._pool_lock:
        core.ga_to_task[id(ga)] = "t9"
    sent = []
    core.send = lambda m: sent.append(m)
    core.register_hooks()
    trigger("tool_before", {"self": handler, "args": {}})  # no tool_name
    # Either skips or emits with the fallback name; both are acceptable as
    # long as the hook does not raise. If emitted, name must be the fallback.
    calls = [m for m in sent if m["type"] == "tool/call"]
    if calls:
        assert calls[0]["name"] == "?"


# ---- Task 4: task/interrupt → abort → task/done{reason:interrupted} (S3) ----
#
# Covers design §6.5 line 167 / tasks.md §3.5. The interrupt path: client sends
# task/interrupt{task_id} → handle_task_interrupt finds the TaskCtx, sets the
# interrupted flag, and calls ctx.ga.abort() (agentmain.py:137-141). The GA's
# run() thread then breaks on stop_sig (agentmain.py:224) and still puts a done
# item on display_queue (agentmain.py:235). drain_display_queue inspects
# ctx.interrupted (set here) to choose reason="interrupted" over "completed".

class _FakeGAWithAbort:
    """Stand-in GA with a recording abort() — proves handle_task_interrupt
    delegates to the engine's existing abort extension point.

    Task 6 also uses this as the _spawn_ga return value for the bounded-
    concurrency tests, so it needs a put_task too — returning an empty
    queue.Queue makes the drain worker block on dq.get(2200s) and stay out
    of the way of the ack-status assertions (no spurious task/done).
    """
    def __init__(self):
        self.aborted = False
    def abort(self):
        self.aborted = True
    def put_task(self, query, source="user", images=None):
        return queue.Queue()
    def shutdown(self):
        pass


class _FakeGAWithFailingAbort:
    """Stand-in GA whose abort() raises — proves handle_task_interrupt must
    NOT leave ctx.interrupted=True when abort fails (I-1 fix). If the flag
    were set before the try and never rolled back, drain_display_queue would
    later emit task/done{reason:"interrupted"} for a task that actually ran
    to completion, contradicting the interrupt_failed error already sent.
    """
    def __init__(self, exc=None):
        self.aborted = False
        self._exc = exc or RuntimeError("abort boom")
    def abort(self):
        self.aborted = True  # simulate partial work before the raise
        raise self._exc
    def shutdown(self):
        pass


def test_handle_task_interrupt_calls_abort_and_sets_flag():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t7", thread=None)
    with core._pool_lock:
        core.pool["t7"] = ctx
        core.ga_to_task[id(ga)] = "t7"
    core.handle_task_interrupt({"id": 100, "type": "task/interrupt", "task_id": "t7"})
    assert ga.aborted is True
    assert ctx.interrupted is True


def test_handle_task_interrupt_unknown_task_id_emits_error():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_task_interrupt({"id": 100, "type": "task/interrupt", "task_id": "nope"})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "unknown_task"


def test_drain_display_queue_interrupted_flag_emits_interrupted_reason():
    """When ctx.interrupted was set by handle_task_interrupt, the subsequent
    done from display_queue (agentmain still puts done after stop_sig break)
    must come out as task/done{reason:interrupted}, not "completed"."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    dq = queue.Queue()
    # non-error-shaped done (no fenced Error block) → normally "completed"
    dq.put({"done": "partial", "source": "user", "turn": 2, "outputs": ["partial"]})
    sent = []
    core.send = lambda m: sent.append(m)
    ctx = ga_stdio.TaskCtx(ga=None, dq=dq, task_id="t_int", thread=None)
    ctx.interrupted = True  # set by handle_task_interrupt earlier
    core.drain_display_queue(ctx)
    assert sent[-1]["type"] == "task/done"
    assert sent[-1]["reason"] == "interrupted"
    assert sent[-1]["task_id"] == "t_int"
    assert "error" not in sent[-1]  # interrupted is not an error frame


def test_dispatch_routes_task_interrupt_after_ready():
    """dispatch must route task/interrupt → handle_task_interrupt once
    initialized; verified by monkeypatching the handler to a sentinel."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    core.initialized = True
    routed = []
    core.handle_task_interrupt = lambda msg: routed.append(msg)
    core.dispatch({"id": 42, "type": "task/interrupt",
                   "version": "1", "task_id": "t1"})
    assert routed == [{"id": 42, "type": "task/interrupt",
                       "version": "1", "task_id": "t1"}]


# ---- Task 4 I-1 fix: abort failure must not leave ctx.interrupted=True ----
#
# reviewer I-1: ctx.interrupted=True was set BEFORE the try (ga_stdio.py:268),
# so if ctx.ga.abort() raised, the except branch sent error{interrupt_failed}
# and returned WITHOUT rolling back the flag. The drain worker would then
# read ctx.interrupted=True on the task's natural done and emit
# task/done{reason:"interrupted"}, contradicting the interrupt_failed error
# the client already received. Fix: move the flag set to AFTER abort() succee
# so abort failure leaves the flag at its default False (drain then emits
# completed/error per the task's true outcome).

def test_handle_task_interrupt_abort_failure_leaves_flag_false():
    """abort() raising must NOT leave ctx.interrupted=True (I-1).

    RED on the pre-fix code: the flag is set before the try and never rolled
    back, so this assertion fails (ctx.interrupted is True).
    """
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithFailingAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t7", thread=None)
    sent = []
    core.send = lambda m: sent.append(m)
    with core._pool_lock:
        core.pool["t7"] = ctx
        core.ga_to_task[id(ga)] = "t7"
    core.handle_task_interrupt({"id": 100, "type": "task/interrupt",
                                "task_id": "t7"})
    # the flag must remain at its default — abort failed, the task was NOT
    # interrupted and may yet run to a natural completed/error outcome.
    assert ctx.interrupted is False, (
        "abort failure must not leave ctx.interrupted=True; drain would then "
        "mislabel a natural done as reason=interrupted, contradicting the "
        "interrupt_failed error already sent")


def test_handle_task_interrupt_abort_failure_emits_interrupt_failed_error():
    """abort() raising must emit error{code:interrupt_failed} (I-1)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithFailingAbort(exc=ValueError("vboom"))
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t9", thread=None)
    sent = []
    core.send = lambda m: sent.append(m)
    with core._pool_lock:
        core.pool["t9"] = ctx
        core.ga_to_task[id(ga)] = "t9"
    core.handle_task_interrupt({"id": 200, "type": "task/interrupt",
                                "task_id": "t9"})
    assert sent, "expected an error frame"
    err = sent[-1]
    assert err["type"] == "error"
    assert err["code"] == "interrupt_failed"
    assert err["original_id"] == 200
    assert "ValueError" in err.get("message", "") or "vboom" in err.get("message", "")


def test_handle_task_interrupt_abort_success_sets_flag_and_acks():
    """abort() succeeding must set ctx.interrupted=True and emit task/ack
    (I-1 guard: the fix must not break the happy path — flag set AFTER the
    try, before the ack)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t3", thread=None)
    sent = []
    core.send = lambda m: sent.append(m)
    with core._pool_lock:
        core.pool["t3"] = ctx
        core.ga_to_task[id(ga)] = "t3"
    core.handle_task_interrupt({"id": 300, "type": "task/interrupt",
                                "task_id": "t3"})
    assert ga.aborted is True
    assert ctx.interrupted is True
    acks = [m for m in sent if m["type"] == "task/ack"]
    assert len(acks) == 1
    assert acks[0]["task_id"] == "t3"
    assert acks[0]["status"] == "interrupting"
    assert acks[0]["id"] == 300


# ---- Task 5: approval loop — patch ask_user, block agent thread, continuation ----
#
# Covers design §6.4 / tasks.md §3.7 / S4. The approval path:
# 1. tool_before fires for ask_user → _on_approval_request stamps the current GA
#    onto the patched ask_user closure and sets ctx.pending_approval (so the
#    upcoming done is recognized as ask_user-caused, not a real completion).
# 2. do_ask_user (ga.py:76-81) calls ga_utils.ask_user — bridge patches it at
#    startup with _bridge_ask_user, which emits approval/request and blocks on
#    a threading.Event until approval/response arrives.
# 3. handle_approval_response (stdio reader thread) fills the box + sets the
#    Event → blocked agent thread wakes, returns client's input → do_ask_user's
#    StepOutcome carries it → should_exit=True → agent_loop break → run() done.
# 4. drain_display_queue sees pending_approval → re-feeds the input as a new
#    prompt on the SAME GA (history preserved, task_id unchanged) and continues
#    draining the new display_queue — client sees no task boundary.

def test_approval_request_emits_and_response_wakes():
    """_on_approval_request stamps GA + sets pending flag; the patched ask_user
    (called by do_ask_user on the same agent thread) emits approval/request and
    blocks; handle_approval_response fills the box and sets the Event."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithAbort()
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id="t1", thread=None)
    with core._pool_lock:
        core.pool["t1"] = ctx
        core.ga_to_task[id(ga)] = "t1"
    sent = []
    core.send = lambda m: sent.append(m)
    core._patch_ask_user()
    core._bridge_ask_user._current_ga = ga
    # _on_approval_request only stamps GA + flag; the actual approval/request
    # is emitted by the patched ask_user when do_ask_user calls it. Simulate that:
    core._on_approval_request(task_id="t1", tool_id="tool_1",
                              args={"question": "continue?", "candidates": ["y", "n"]})
    assert ctx.pending_approval is True
    # simulate do_ask_user calling our patched ask_user (on the agent thread):
    import threading as _th
    box = {}
    th = _th.Thread(target=lambda: box.update({"ret": core._bridge_ask_user("continue?", ["y", "n"])}),
                    daemon=True)
    th.start()
    # the patched ask_user should have emitted approval/request and be blocking
    import time as _t; _t.sleep(0.05)
    reqs = [m for m in sent if m["type"] == "approval/request"]
    assert len(reqs) == 1
    assert reqs[0]["task_id"] == "t1"
    assert reqs[0]["prompt"] == "continue?"
    assert reqs[0]["options"] == ["y", "n"]
    # client responds → handle_approval_response wakes the blocked thread
    tool_id = reqs[0]["tool_id"]
    core.handle_approval_response({"id": 200, "type": "approval/response",
                                   "task_id": "t1", "tool_id": tool_id,
                                   "decision": "approve", "input": "yes please"})
    th.join(timeout=2)
    assert not th.is_alive()
    assert box["ret"] == "yes please"
    assert ctx._approval_input == "yes please"


def test_approval_response_unknown_slot_emits_stale_approval():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_approval_response({"id": 200, "type": "approval/response",
                                   "task_id": "ghost", "tool_id": "tool_x",
                                   "decision": "approve"})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "stale_approval"


# ---- Task 5 C1 fix: patch ga.ask_user too — do_ask_user routes via ga globals ----
#
# ga.py:8-12 binds `ask_user` into ga module globals via
# `from ga_utils import ask_user`; do_ask_user (ga.py:79) looks it up there
# (LOAD_GLOBAL). Patching ga_utils.ask_user alone never reaches the real
# do_ask_user path, so the approval human-in-the-loop fails at runtime even
# though brief Step 1 tests pass (they call core._bridge_ask_user directly,
# bypassing the route). This test pins the real route: after _patch_ask_user,
# ga.ask_user must be the bridge version.

def test_patch_ask_user_reroutes_ga_module_global():
    """C1: do_ask_user (ga.py:79) looks up `ask_user` in ga module globals.
    _patch_ask_user must replace ga.ask_user, not just ga_utils.ask_user,
    or the real do_ask_user path never invokes the bridge version."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    import ga
    original = ga.ask_user
    try:
        core._patch_ask_user()
        assert ga.ask_user is core._bridge_ask_user, \
            "ga.ask_user must be rerouted to bridge version (C1)"
    finally:
        ga.ask_user = original  # restore for test isolation


# ---- Task 6: per-task GA pool + bounded concurrency (S2/Q8) ----
#
# Covers design §10.6 / §6.2 / tasks.md §3.4 / S2 / Q8. handle_task_start now
# acquires from a counting Semaphore (max_concurrency, default 4, env-override
# via GA_STDIO_MAX_CONCURRENCY). Overflow task/starts return task/ack{status:
# queued} and park on a FIFO _queued list; when a running task's _release_task
# fires (worker finally block), it pops the head of _queued and starts it on a
# fresh GA via _start_task_thread — the woken task's running ack is emitted
# from _release_task. Tests assert structure (statuses + _queued len), not
# timing; the sleep gives the woken thread a moment to ack.

def test_task_start_over_limit_returns_queued():
    """max_concurrency=2; 3rd task_start → task/ack{status:queued}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"), max_concurrency=2)
    core._spawn_ga = lambda: _FakeGAWithAbort()  # avoid real GA spawn in unit test
    acks = []
    core.send = lambda m: acks.append(m)
    for i in range(3):
        core.handle_task_start({"id": i, "type": "task/start",
                                "prompt": f"p{i}", "mode": "single"})
    statuses = [a.get("status") for a in acks if a.get("type") == "task/ack"]
    assert statuses[:2] == ["running", "running"]
    assert statuses[2] == "queued"
    assert len(core._queued) == 1


def test_release_wakes_queued_task():
    """When a running task releases, the next queued task starts (running)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"), max_concurrency=1)
    ga = _FakeGAWithAbort()
    core._spawn_ga = lambda: ga
    acks = []
    core.send = lambda m: acks.append(m)
    core.handle_task_start({"id": 1, "type": "task/start", "prompt": "p1"})
    core.handle_task_start({"id": 2, "type": "task/start", "prompt": "p2"})
    # second is queued
    assert acks[-1]["status"] == "queued"
    # simulate first task done → release
    first_ctx = list(core.pool.values())[0]
    core._release_task(first_ctx)
    time.sleep(0.05)  # let woken thread ack
    running = [a for a in acks if a.get("status") == "running"]
    assert len(running) >= 2


def test_release_hands_off_slot_without_semaphore_release():
    """Race fix (Task 6 main concern): when _queued is non-empty, _release_task
    must hand off the slot to the next queued task WITHOUT calling
    semaphore.release() — else a concurrent stdio-reader task/start acquires
    the just-released slot while the woken queued task also starts without
    acquiring, exceeding max_concurrency by 1."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"), max_concurrency=1)
    ga1, ga2, ga3 = _FakeGAWithAbort(), _FakeGAWithAbort(), _FakeGAWithAbort()
    seq = iter([ga1, ga2, ga3])
    core._spawn_ga = lambda: next(seq)
    acks = []; core.send = lambda m: acks.append(m)
    core.handle_task_start({"id": 1, "type": "task/start", "prompt": "p1"})  # running
    core.handle_task_start({"id": 2, "type": "task/start", "prompt": "p2"})  # queued
    assert acks[-1]["status"] == "queued"
    assert core.semaphore._value == 0  # 1 running, max 1
    first_ctx = next(c for c in core.pool.values() if c.ga is ga1)
    core._release_task(first_ctx)  # hand off slot to queued task2
    assert core.semaphore._value == 0, \
        "hand-off must not release semaphore (race fix), got " + str(core.semaphore._value)
    core.handle_task_start({"id": 3, "type": "task/start", "prompt": "p3"})
    last = [a for a in acks if a.get("type") == "task/ack"][-1]
    assert last["status"] == "queued", \
        "task3 must queue — slot still held by handed-off task2, got " + str(last["status"])


# ---- Task 7: autonomous continuation loop + budget{seconds,turns} (S7/Q5) ----

def test_validate_budget_requires_at_least_one_field():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    assert core._validate_budget({"seconds": 2}) is True
    assert core._validate_budget({"turns": 1}) is True
    assert core._validate_budget({"seconds": 2, "turns": 3}) is True
    assert core._validate_budget({}) is False
    assert core._validate_budget(None) is False
    assert core._validate_budget("oops") is False


def test_run_autonomous_emits_budget_done_when_seconds_exhausted():
    """With budget{seconds:0} the first continuation is immediately the wrap-up
    round → task/done{reason:budget}."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)

    class _GA:
        def __init__(self):
            self.calls = 0
        def put_task(self, prompt, source=None, images=None):
            dq = queue.Queue()
            if self.calls == 0:
                dq.put({"next": "work", "source": source, "turn": 1, "outputs": ["work"]})
                dq.put({"done": "work done", "source": source, "turn": 1, "outputs": ["work done"]})
            else:
                dq.put({"done": "[wrap up]", "source": source, "turn": 2, "outputs": ["[wrap]"]})
            self.calls += 1
            return dq
        def abort(self): pass
        def shutdown(self): pass

    ctx = ga_stdio.TaskCtx(ga=_GA(), dq=None, task_id="tA", thread=None,
                           mode="autonomous", budget={"seconds": 0})
    import time as _t
    ctx.start_time = _t.time() - 1   # force elapsed >= seconds immediately
    with core._pool_lock:
        core.pool["tA"] = ctx
        core.ga_to_task[id(ctx.ga)] = "tA"
    core.run_autonomous(ctx)
    reasons = [m for m in sent if m.get("type") == "task/done"]
    assert reasons and any(r["reason"] == "budget" for r in reasons)


# ---- Task 8: slash/cmd forward — injection via prompt_for + raw state-class (S8/S9/Q6) ----
#
# Covers design §10.8 / §6.6 / tasks.md §3.10 / S8/S9 / Q6. handle_slash_cmd has
# three routing paths plus a default reject:
#   1. /scheduler → error{slash_unsupported} (out of protocol scope — touches
#      local FS, no LLM; TUI handles it directly).
#   2. Injection-class (/update /autorun /morphling /goal /hive /conductor) →
#      frontends.slash_cmds.prompt_for(cmd, args) returns the injected prompt
#      → run as a new task via put_task (reuses Task 6 pool + queueing) →
#      slash/result{task_id, injected_prompt[:200]}.
#   3. State-class (/llm /resume /session.*) → raw put_task(f"{cmd} {args}")
#      so agentmain._handle_slash_cmd processes it internally →
#      slash/result{task_id} (no injected_prompt).
#   4. unknown → error{slash_unsupported}.
#
# Tests assert structure (which path was taken + which frame type emitted),
# not the injected prompt's content — prompt_for wording is slash_cmds's
# concern, not the bridge's.

def test_slash_injection_command_routes_via_prompt_for():
    """/goal <target> → prompt_for returns injected prompt → new task on a fresh GA."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    core._spawn_ga = lambda: _FakeGAWithAbort()
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd",
                           "cmd": "/goal", "args": "build a web"})
    acks = [m for m in sent if m.get("type") == "task/ack"]
    results = [m for m in sent if m.get("type") == "slash/result"]
    assert len(acks) == 1
    assert len(results) == 1
    assert results[0].get("injected_prompt")
    assert results[0].get("task_id") == acks[0]["task_id"]


def test_slash_state_command_routes_raw():
    """/llm 2 → raw put_task on a fresh GA (state-class, _handle_slash_cmd internal)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    captured = {}
    class _GA(_FakeGAWithAbort):
        def put_task(self, q, source=None, images=None):
            captured["raw"] = q
            dq = queue.Queue(); dq.put({"done": "ok", "source": source, "turn": 0, "outputs": []}); return dq
    core._spawn_ga = lambda: _GA()
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd", "cmd": "/llm", "args": "2"})
    assert captured["raw"] == "/llm 2"
    results = [m for m in sent if m.get("type") == "slash/result"]
    assert len(results) == 1


def test_slash_state_command_emits_task_ack_symmetric_with_injection():
    """/llm 2 → state-class path emits task/ack{running} + slash/result{task_id}
    (symmetric with injection-class; no orphan ack on queued wake-up)."""
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    captured = {}
    class _GA(_FakeGAWithAbort):
        def put_task(self, q, source=None, images=None):
            captured["raw"] = q
            dq = queue.Queue(); dq.put({"done": "ok", "source": source, "turn": 0, "outputs": []}); return dq
    core._spawn_ga = lambda: _GA()
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd", "cmd": "/llm", "args": "2"})
    acks = [m for m in sent if m.get("type") == "task/ack"]
    results = [m for m in sent if m.get("type") == "slash/result"]
    assert len(acks) == 1
    assert acks[0]["status"] == "running"
    assert acks[0]["task_id"] == results[0]["task_id"]
    assert captured["raw"] == "/llm 2"
    assert "injected_prompt" not in results[0]   # state-class has no injected prompt


def test_slash_unsupported_command_emits_error():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_slash_cmd({"id": 1, "type": "slash/cmd", "cmd": "/scheduler", "args": ""})
    assert sent[-1]["type"] == "error"
    assert sent[-1]["code"] == "slash_unsupported"


# ---- Task 9: llm/list + llm/select + session/resume (S6) ----
#
# Covers design §10.9 / tasks.md §3.9 / S6. llm/list queries ga.list_llms();
# llm/select calls ga.next_llm(n) to switch model; session/resume restores
# ga.llmclient.backend.history (and optionally next_llm(llm_no)). These operate
# on a GA for an existing task (v1 requires task_id; scoped to that task's GA).

class _FakeGAWithLLM:
    def __init__(self):
        self.switched_to = None
        self.history_set = None
    def list_llms(self):
        return [(0, "openai/gpt-4", True), (1, "anthropic/claude", False)]
    def next_llm(self, n=-1):
        self.switched_to = n
    def shutdown(self): pass


def _make_ctx_with_ga(core, ga, task_id="tL"):
    ctx = ga_stdio.TaskCtx(ga=ga, dq=queue.Queue(), task_id=task_id, thread=None)
    with core._pool_lock:
        core.pool[task_id] = ctx
        core.ga_to_task[id(ga)] = task_id
    return ctx


def test_llm_list_emits_models_with_current():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithLLM()
    _make_ctx_with_ga(core, ga, "tL")
    sent = []
    core.send = lambda m: sent.append(m)
    core.handle_llm_list({"id": 1, "type": "llm/list", "task_id": "tL"})
    m = [x for x in sent if x["type"] == "llm/list"][0]
    assert m["current_no"] == 0
    assert {"no": 0, "name": "openai/gpt-4", "current": True} in m["llms"]
    assert {"no": 1, "name": "anthropic/claude", "current": False} in m["llms"]


def test_llm_select_switches_model():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    ga = _FakeGAWithLLM()
    _make_ctx_with_ga(core, ga, "tL")
    core.handle_llm_select({"id": 1, "type": "llm/select", "task_id": "tL", "n": 1})
    assert ga.switched_to == 1


def test_session_resume_restores_history():
    core = ga_stdio.BridgeCore(stdout=open(os.devnull, "w"))
    class _GA(_FakeGAWithLLM):
        def __init__(self):
            super().__init__()
            class _Backend: history = None
            self.llmclient = type("C", (), {"backend": _Backend()})()
    ga = _GA()
    _make_ctx_with_ga(core, ga, "tS")
    core.handle_session_resume({"id": 1, "type": "session/resume",
                                "task_id": "tS",
                                "history": [{"role": "user", "content": "hi"}],
                                "llm_no": 0})
    assert ga.llmclient.backend.history == [{"role": "user", "content": "hi"}]
