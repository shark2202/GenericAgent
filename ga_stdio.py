"""Durable Agent Protocol v1 — stdio bridge (skeleton, Task 1).

Spawned by a non-Python frontend (Rust CLI/TUI/GUI) as a child process:
    python -m ga_stdio
Communicates via newline-delimited JSON on stdin/stdout. stderr is for
diagnostics only (not part of the protocol).

Design: docs/superpowers/specs/2026-07-19-durable-agent-protocol-design.md
Engine internals (agent_loop/ga/llmcore) are untouched; this module only
reuses existing extension points (plugins.hooks, GenericAgent SDK,
slash_cmds.prompt_for, MCPClientManager) — wired in later tasks.

Task 1 scope: stdio read/write loop + initialize/ready handshake + malformed
JSON error (E2) + not-initialized guard (E3). Business message handlers are
wired in Tasks 2-10; until then they return unknown_type.
"""
import sys
import os
import json
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "1"  # major only, LSP-style; breaking change bumps major
SERVER_CAPABILITIES = [
    "streaming", "multi-session", "autonomous", "approval",
    "mcp", "slash", "llm-switch", "session-resume",
]

# Error codes (design §7)
ERR_BAD_JSON = "bad_json"
ERR_NOT_INITIALIZED = "not_initialized"
ERR_CAPABILITY_UNSUPPORTED = "capability_unsupported"
ERR_UNKNOWN_TYPE = "unknown_type"
ERR_BAD_REQUEST = "bad_request"
ERR_INTERNAL_ERROR = "internal_error"

_REQUIRED_FIELDS = ("id", "type", "version")


def _parse_line(line):
    """Parse one stdin line into a dict, or None if malformed (E2).

    Blank lines return None too; the caller distinguishes by checking
    ``line.strip()`` first (see BridgeCore.serve).
    """
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def _serialize(msg):
    """Serialize one message dict to a JSON line (with trailing newline)."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


class BridgeCore:
    """Protocol state machine + stdio reader loop.

    Task 1: handshake + error routing only. Pool, hooks, semaphore, and
    business handlers are wired in later tasks.
    """

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._next_event_id = 1
        self.initialized = False
        # pool / hooks / semaphore wired in later tasks

    def _next_id(self):
        i = self._next_event_id
        self._next_event_id += 1
        return i

    def send(self, msg):
        """Write one JSON line to stdout (thread-safe)."""
        line = _serialize(msg)
        with self._write_lock:
            self.stdout.write(line)
            self.stdout.flush()

    def send_error(self, code, message, original_id=None, **extra):
        """Emit an error frame.

        ``original_id`` is always present (null when the triggering line
        could not be parsed, e.g. bad_json) — design §7. Extra fields
        (e.g. ``missing=[...]`` for capability_unsupported) are merged in.
        """
        err = {"id": self._next_id(), "type": "error", "version": VERSION,
               "code": code, "message": message, "original_id": original_id}
        err.update(extra)
        self.send(err)

    def handle_initialize(self, msg):
        """Capability negotiation (design §4)."""
        caps = msg.get("capabilities", []) or []
        missing = [c for c in caps if c not in SERVER_CAPABILITIES]
        if missing:
            self.send_error(ERR_CAPABILITY_UNSUPPORTED,
                            f"server lacks: {missing}",
                            original_id=msg.get("id"), missing=missing)
            return
        self.initialized = True
        # agent_info stub — real values wired in Task 6 (needs GA instance)
        self.send({"id": msg["id"], "type": "ready", "version": VERSION,
                   "capabilities": SERVER_CAPABILITIES,
                   "agent_info": {"name": "GenericAgent",
                                  "mcp_connected": 0, "llm_count": 0}})

    def dispatch(self, msg):
        """Route one parsed client message.

        All messages must carry id/type/version (design §3). Business
        messages require a prior successful initialize (design §4, E3).
        """
        for field in _REQUIRED_FIELDS:
            if field not in msg:
                self.send_error(ERR_BAD_REQUEST,
                                f"missing required field: {field}",
                                original_id=msg.get("id"))
                return
        mtype = msg["type"]
        if mtype == "initialize":
            self.handle_initialize(msg)
            return
        if not self.initialized:
            self.send_error(ERR_NOT_INITIALIZED,
                            f"got {mtype!r} before initialize (E3)",
                            original_id=msg.get("id"))
            return
        # Business handlers wired in later tasks; skeleton rejects as
        # unknown_type so the protocol contract is observable now.
        self.send_error(ERR_UNKNOWN_TYPE, f"{mtype} not implemented yet",
                        original_id=msg.get("id"))

    def serve(self):
        """Main stdio reader loop. One JSON line per stdin line.

        E2: malformed JSON emits bad_json and continues (does not break).
        Blank lines are skipped silently. On stdin EOF the loop returns
        and the child process exits; the client detects stdout EOF (E1).
        """
        for line in self.stdin:
            stripped = line.strip()
            if not stripped:
                continue  # tolerate blank lines without an error frame
            msg = _parse_line(line)
            if msg is None:
                self.send_error(ERR_BAD_JSON,
                                f"unparseable line: {stripped[:80]}",
                                original_id=None)
                continue
            try:
                self.dispatch(msg)
            except Exception as e:
                self.send_error(ERR_INTERNAL_ERROR,
                                f"{type(e).__name__}: {e}",
                                original_id=msg.get("id"))


if __name__ == "__main__":
    BridgeCore().serve()
