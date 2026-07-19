"""§4.1 transport: initialize/ready handshake + malformed JSON doesn't crash (E2)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize


def test_handshake_returns_ready_with_caps_and_agent_info():
    with BridgeProc() as bp:
        ready = initialize(bp)
        assert "capabilities" in ready and len(ready["capabilities"]) > 0
        ai = ready["agent_info"]
        assert ai["name"] == "GenericAgent"
        assert "mcp_connected" in ai and "llm_count" in ai


def test_malformed_json_emits_bad_json_error_and_keeps_running():
    """E2: a bad line → error{code:bad_json}; next valid line still processed."""
    with BridgeProc() as bp:
        initialize(bp)
        bp.proc.stdin.write("this is not json\n")
        bp.proc.stdin.flush()
        err = bp.recv(timeout=5)
        assert err is not None and err["type"] == "error"
        assert err["code"] == "bad_json"
        # bridge must still be alive — send mcp/list, expect a response
        bp.send({"id": 99, "type": "mcp/list", "version": "1"})
        m = bp.recv(timeout=5)
        assert m is not None and m["type"] == "mcp/list"


def test_stdout_first_line_is_json_no_engine_diagnostic():
    """C1: engine print()s must not pollute stdout (wire transport).

    Design §3 line 40: stdout carries ONLY protocol frames; stderr is the
    diagnostics channel. A real Rust client does ``serde_json::from_str(line)``
    per line and crashes on a non-JSON first line like ``[Info] Load mykeys
    from ...`` (llmcore.py:100). The bridge (production path: ``python -m
    ga_stdio`` with no stdout arg) must dup fd 1 for wire writes and rebound
    sys.stdout → sys.stderr so the engine's diagnostic print()s land on stderr,
    not the wire transport.

    Reads the RAW first stdout line via ``bp.raw_recv`` (NOT ``bp.recv``, which
    skips non-JSON and would mask the bug) and asserts it parses as JSON.
    """
    with BridgeProc(timeout=15) as bp:
        bp.send({"id": 1, "type": "initialize", "version": "1",
                 "capabilities": ["streaming", "multi-session", "autonomous",
                                   "approval", "mcp", "slash", "llm-switch",
                                   "session-resume"]})
        line = bp.raw_recv(timeout=15)   # raw first stdout line, no skipping
        assert line, "no stdout line within timeout"
        try:
            json.loads(line)   # must parse as a JSON frame
        except (ValueError, json.JSONDecodeError):
            pytest.fail(
                "stdout first line is not JSON (engine diagnostic leaked onto "
                f"the wire transport): {line!r}")
