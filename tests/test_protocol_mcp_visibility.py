"""§4.7 mcp/list visibility query (S5). Structure only, no MCP config required."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize


def test_mcp_list_returns_servers_array_structure():
    with BridgeProc() as bp:
        initialize(bp)
        bp.send({"id": 50, "type": "mcp/list", "version": "1"})
        msg = bp.recv(timeout=10)
        assert msg["type"] == "mcp/list"
        assert isinstance(msg["servers"], list)
        for s in msg["servers"]:
            assert "name" in s and "tools" in s
            for t in s["tools"]:
                assert "name" in t
