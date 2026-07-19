"""§4.2 capability negotiation: unsupported cap → graceful error+exit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc


def test_unsupported_capability_emits_capability_unsupported():
    with BridgeProc() as bp:
        bp.send({"id": 1, "type": "initialize", "version": "1",
                 "capabilities": ["streaming", "nonexistent-cap"]})
        msg = bp.recv(timeout=5)
        assert msg["type"] == "error"
        assert msg["code"] == "capability_unsupported"
        assert "nonexistent-cap" in msg["message"]
