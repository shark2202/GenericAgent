"""§4.11 resilience: child crash (E1) + uninitialized business msg rejected (E3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _protocol_helpers import BridgeProc, initialize


def test_uninitialized_task_start_emits_not_initialized():
    """E3: task/start before initialize → error{code:not_initialized}."""
    with BridgeProc() as bp:
        bp.send({"id": 1, "type": "task/start", "version": "1", "prompt": "hi"})
        m = bp.recv(timeout=5)
        assert m["type"] == "error"
        assert m["code"] == "not_initialized"


def test_child_crash_surfaces_as_stdout_eof_no_hang():
    """E1: kill the child; client sees stdout EOF (recv returns None), not a hang."""
    with BridgeProc() as bp:
        initialize(bp)
        bp.proc.kill()
        bp.proc.wait(timeout=5)
        m = bp.recv(timeout=3)
        assert m is None
