"""§4.1 transport: initialize/ready handshake + malformed JSON doesn't crash (E2)."""
import os, sys
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
