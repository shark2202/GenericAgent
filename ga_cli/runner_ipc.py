"""Goal-Runner IPC Protocol (v0.1) — JSON Lines over stdin/stdout.

Wire format: one JSON object per line, UTF-8, compact separators.
Each message carries a ``kind`` discriminator string.

This is a *deliberate subset* of galley's ``runner/ipc.py`` (17 events / 12
commands). We keep only what the goal-runner spawn link needs to go live:
- Events  (subprocess -> controller): Ready / TurnStart / ToolCallStart /
           ToolCallEnd / FinalAnswer / Error / Exited
- Commands (controller -> subprocess): Run / Abort / Shutdown / Ping

Field names are camelCase so the dataclasses map 1:1 onto the JSON payload
(mirror of galley's convention). ``encode`` emits a compact single line with
*no* trailing newline — the caller appends ``\\n`` when writing to the pipe.
``decode_line`` tolerates surrounding whitespace/newlines so it is robust both
to ``encode`` output and to line-fragments from a reader thread.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Union

PROTOCOL_VERSION = "0.1"


# --------------------------------------------------------------------------- #
# Commands  (controller -> subprocess, written to the child's stdin)
# --------------------------------------------------------------------------- #

@dataclass
class Run:
    """Tell the runner to execute a task (a fresh user message)."""
    task: str
    kind: str = "run"


@dataclass
class Abort:
    """Abort the in-flight turn (cooperative; maps to ``agent.abort()``)."""
    kind: str = "abort"


@dataclass
class Shutdown:
    """Ask the runner to exit cleanly after the current turn."""
    kind: str = "shutdown"


@dataclass
class Ping:
    """Health check; runner replies with a Ready/Pong-style event."""
    kind: str = "ping"


Command = Union[Run, Abort, Shutdown, Ping]

_COMMAND_REGISTRY: dict[str, type] = {c.kind: c for c in (Run, Abort, Shutdown, Ping)}


# --------------------------------------------------------------------------- #
# Events  (subprocess -> controller, read from the child's stdout)
# --------------------------------------------------------------------------- #

@dataclass
class Ready:
    """First event. Runner is up, SDK imported, waiting for commands."""
    protocolVersion: str = PROTOCOL_VERSION
    sessionId: str = ""
    kind: str = "ready"


@dataclass
class Pong:
    """Reply to a Ping health check (liveness heartbeat)."""
    sessionId: str = ""
    kind: str = "pong"


@dataclass
class TurnStart:
    """A model turn has started."""
    turn: int = 1
    kind: str = "turnStart"


@dataclass
class ToolCallStart:
    """The model is about to invoke a tool."""
    toolName: str = ""
    callId: str = ""
    kind: str = "toolCallStart"


@dataclass
class ToolCallEnd:
    """A tool call finished."""
    callId: str = ""
    ok: bool = True
    kind: str = "toolCallEnd"


@dataclass
class FinalAnswer:
    """The turn produced a final answer text — terminal success signal."""
    text: str = ""
    kind: str = "finalAnswer"


@dataclass
class Error:
    """A recoverable or fatal error occurred in the runner."""
    message: str = ""
    fatal: bool = True
    kind: str = "error"


@dataclass
class Exited:
    """The runner subprocess is exiting. code==0 == clean."""
    code: int = 0
    reason: str = ""
    kind: str = "exited"


Event = Union[Ready, Pong, TurnStart, ToolCallStart, ToolCallEnd,
              FinalAnswer, Error, Exited]

_EVENT_REGISTRY: dict[str, type] = {
    e.kind: e for e in
    (Ready, Pong, TurnStart, ToolCallStart, ToolCallEnd,
     FinalAnswer, Error, Exited)
}


# --------------------------------------------------------------------------- #
# (De)serialization
# --------------------------------------------------------------------------- #

class IPCProtocolError(Exception):
    """Raised when an IPC message fails to parse or validate."""


def encode(msg: Any) -> str:
    """Serialize a dataclass command/event to one compact JSON line.

    No trailing newline. The caller appends ``\\n`` when writing to a pipe.
    """
    return json.dumps(asdict(msg), ensure_ascii=False, separators=(",", ":"))


def encode_line(msg: Any) -> str:
    """``encode`` + trailing newline — the actual wire form for a pipe write."""
    return encode(msg) + "\n"


def _construct(cls: type, payload: dict[str, Any]) -> Any:
    """Build ``cls`` from ``payload``, ignoring unknown keys (forward-compat)."""
    names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in payload.items() if k in names}
    return cls(**kwargs)


def decode_line(line: str) -> Union[Command, Event]:
    """Decode one JSON line into a Command or Event.

    Tolerates leading/trailing whitespace and the trailing newline. Raises
    :class:`IPCProtocolError` on malformed/unknown messages.
    """
    s = line.strip()
    if not s:
        raise IPCProtocolError("empty line")
    try:
        payload = json.loads(s)
    except json.JSONDecodeError as exc:
        raise IPCProtocolError(f"invalid json: {s!r}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IPCProtocolError(f"not a json object: {s!r}")
    kind = payload.get("kind")
    if not kind:
        raise IPCProtocolError(f"missing 'kind' field: {s!r}")
    if kind in _EVENT_REGISTRY:
        return _construct(_EVENT_REGISTRY[kind], payload)
    if kind in _COMMAND_REGISTRY:
        return _construct(_COMMAND_REGISTRY[kind], payload)
    raise IPCProtocolError(f"unknown 'kind': {kind!r}")


def decode_event(line: str) -> Event:
    """Decode a line known to be an event (stdout reader)."""
    msg = decode_line(line)
    if msg.kind not in _EVENT_REGISTRY:
        raise IPCProtocolError(f"expected event, got command kind={msg.kind!r}")
    return msg  # type: ignore[return-value]


def decode_command(line: str) -> Command:
    """Decode a line known to be a command (stdin reader in the child)."""
    msg = decode_line(line)
    if msg.kind not in _COMMAND_REGISTRY:
        raise IPCProtocolError(f"expected command, got event kind={msg.kind!r}")
    return msg  # type: ignore[return-value]


def is_event(msg: Any) -> bool:
    return getattr(msg, "kind", None) in _EVENT_REGISTRY


def is_command(msg: Any) -> bool:
    return getattr(msg, "kind", None) in _COMMAND_REGISTRY


__all__ = [
    "PROTOCOL_VERSION",
    # commands
    "Run", "Abort", "Shutdown", "Ping", "Command",
    # events
    "Ready", "TurnStart", "ToolCallStart", "ToolCallEnd",
    "FinalAnswer", "Error", "Exited", "Event",
    # codec
    "encode", "encode_line", "decode_line", "decode_event", "decode_command",
    "is_event", "is_command", "IPCProtocolError",
]
