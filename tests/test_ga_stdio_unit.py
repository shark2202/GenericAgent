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
import sys

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


def test_ready_then_task_start_returns_unknown_type():
    """After initialize, business messages are accepted but not yet implemented (Task 2+)."""
    out = _run_bridge([
        _line({"id": 1, "type": "initialize", "version": "1", "capabilities": []}),
        _line({"id": 2, "type": "task/start", "version": "1", "prompt": "hi"}),
    ])
    assert len(out) == 2
    assert out[0]["type"] == "ready"
    err = out[1]
    assert err["type"] == "error"
    assert err["code"] == "unknown_type"
    assert err["original_id"] == 2


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
