"""Black-box wire-test harness for ga_stdio protocol tests.

Spawns `python -m ga_stdio` as a child; sends JSON lines on stdin; reads
JSON lines from stdout with a timeout. Asserts on message structure
(type sequence / required fields / terminal reason), never on LLM text.
"""
import json
import os
import subprocess
import sys
import threading
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# LLM availability: GA_HAS_LLM (explicit) OR MYKEY_PATH (a mykey.jsonc exists
# somewhere GA can find). Pure-protocol tests (transport/capability/mcp/
# resilience/scheduler) don't need LLM and don't guard; behavior tests that
# actually drive an LLM (single/multi/interrupt/approval/llm/autonomous/goal)
# use @needs_llm and skip when no LLM is reachable.
_HAS_LLM = bool(os.environ.get("GA_HAS_LLM") or os.environ.get("MYKEY_PATH"))
try:
    import pytest
    needs_llm = pytest.mark.skipif(not _HAS_LLM,
                                   reason="no LLM configured (set GA_HAS_LLM=1)")
except Exception:
    def needs_llm(fn): return fn


class BridgeProc:
    """Spawns `python -m ga_stdio` and exposes line-oriented send/recv."""

    def __init__(self, timeout=10.0):
        env = dict(os.environ)
        env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ga_stdio"],
            cwd=_REPO_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self._deadline = time.monotonic() + timeout

    def send(self, msg):
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def recv(self, timeout=None):
        """Read one JSON frame from stdout. Returns parsed dict or None on timeout/EOF.

        The wire contract says stdout carries only JSON frames, but the GA
        engine emits a few diagnostic print()s during init (e.g. llmcore's
        ``[Info] Load mykeys from ...``) that land on stdout before the first
        protocol frame. A robust wire harness tolerates those stray lines:
        we keep reading lines within the timeout window, skipping any that
        don't parse as a JSON dict, and return the first JSON frame we see.
        """
        if timeout is None:
            timeout = max(0.0, self._deadline - time.monotonic())
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            box = []
            t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
            t.start()
            t.join(remaining)
            if t.is_alive():
                # readline blocked past the deadline — give up.
                return None
            if not box or box[0] == "":
                # EOF on stdout (child gone / pipe closed).
                return None
            try:
                return json.loads(box[0])
            except (json.JSONDecodeError, ValueError):
                # Non-JSON diagnostic line on stdout — skip it and keep
                # reading within the remaining budget.
                continue

    def recv_until(self, predicate, timeout=30.0, max_msgs=200):
        """Read messages until predicate(msg) is True or timeout. Returns collected list."""
        out = []
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(out) < max_msgs:
            m = self.recv(timeout=min(5.0, max(0.1, end - time.monotonic())))
            if m is None:
                # Distinguish EOF (child gone) from a slow line: probe once
                # more with a short timeout; if still None and proc is dead,
                # break so the caller sees what was collected so far.
                if self.proc.poll() is not None:
                    break
                continue
            out.append(m)
            if predicate(m):
                break
        return out

    def close(self):
        try: self.proc.stdin.close()
        except Exception: pass
        try: self.proc.wait(timeout=5)
        except Exception:
            try: self.proc.kill()
            except Exception: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def initialize(bp, caps=None):
    """Perform initialize/ready handshake; returns ready message."""
    bp.send({"id": 1, "type": "initialize", "version": "1",
             "capabilities": caps or ["streaming", "multi-session", "autonomous",
                                       "approval", "mcp", "slash", "llm-switch",
                                       "session-resume"]})
    msg = bp.recv(timeout=10)
    assert msg is not None, "no ready after initialize"
    assert msg["type"] == "ready"
    assert msg["version"] == "1"
    return msg
