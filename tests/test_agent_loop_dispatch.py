"""Dispatch tests (audit F26: core dispatch hot path had zero tests).

Drives BaseHandler.dispatch directly with canned args/response objects.
Covers method-track, registry-track fallback, method priority on
collision, arg-injection parity, and unknown-tool path — the 5 spec
requirements for the dual-track dispatch contract.
"""
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_loop import (  # noqa: E402
    _TOOL_REGISTRY,
    BaseHandler,
    StepOutcome,
    register_tool,
)


class _Resp:
    """Stand-in for the LLM response object; dispatch only reads args/response."""
    def __init__(self, content=""):
        self.content = content
        self.thinking = ""


def _drain(gen):
    """Run a dispatch generator to exhaustion, return its StopIteration.value (StepOutcome)."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


class _EchoHandler(BaseHandler):
    """Minimal handler exposing one do_* method (method-track)."""

    def __init__(self):
        self.cwd = os.getcwd()

    def do_echo(self, args, response):
        yield f"echo: {args.get('msg', '')}\n"
        return StepOutcome({"echoed": args.get("msg", "")}, next_prompt="\n")


# ── method-track (spec req 3: backward compat of existing do_*) ───

def test_method_track_dispatches_do_echo():
    h = _EchoHandler()
    outcome = _drain(h.dispatch("echo", {"msg": "hi"}, _Resp(), index=0, tool_num=1))
    assert outcome.data == {"echoed": "hi"}


def test_method_track_injects_index_and_tool_num():
    """Method-track receives _index/_tool_num injection (the parity baseline)."""
    class _H(BaseHandler):
        def do_probe(self, args, response):
            return StepOutcome({"i": args.get("_index"), "n": args.get("_tool_num")}, next_prompt="\n")
    h = _H()
    outcome = _drain(h.dispatch("probe", {}, _Resp(), index=4, tool_num=2))
    assert outcome.data == {"i": 4, "n": 2}


# ── registry-track fallback (spec req 1 scenario 2, req 2) ───────

@pytest.fixture
def _reg_echo():
    """Register a registry-only echo2 tool; clean up after."""
    @register_tool("echo2")
    def echo2(handler, args, response):
        yield f"echo2: {args.get('msg', '')}\n"
        return StepOutcome({"echoed2": args.get("msg", "")}, next_prompt="\n")
    yield "echo2"
    _TOOL_REGISTRY.pop("echo2", None)


def test_registry_track_dispatches_without_handler_method(_reg_echo):
    """No do_echo2 method on handler, but echo2 registered -> registry-track."""
    class _H(BaseHandler):
        pass
    h = _H()
    outcome = _drain(h.dispatch("echo2", {"msg": "yo"}, _Resp(), index=0, tool_num=1))
    assert outcome.data == {"echoed2": "yo"}


def test_registry_track_forwards_yielded_output(_reg_echo):
    """Registry fn's yielded strings flow through dispatch's generator (try_call_generator parity)."""
    class _H(BaseHandler):
        pass
    h = _H()
    gen = h.dispatch("echo2", {"msg": "yo"}, _Resp(), index=0, tool_num=1)
    chunks = []
    outcome = None
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as e:
        outcome = e.value
    assert "echo2: yo" in "".join(chunks)
    assert outcome.data == {"echoed2": "yo"}


def test_method_track_beats_registry_on_collision():
    """do_echo2_collide on handler AND echo2_collide registered -> method wins (spec req 1 scenario 1)."""
    call_log = []

    @register_tool("echo2_collide")
    def _reg_fn(handler, args, response):
        call_log.append("registry")
        return StepOutcome({"via": "registry"}, next_prompt="\n")

    class _H(BaseHandler):
        def do_echo2_collide(self, args, response):
            call_log.append("method")
            return StepOutcome({"via": "method"}, next_prompt="\n")

    try:
        h = _H()
        outcome = _drain(h.dispatch("echo2_collide", {}, _Resp(), index=0, tool_num=1))
        assert outcome.data == {"via": "method"}
        assert call_log == ["method"]  # registry NOT invoked
    finally:
        _TOOL_REGISTRY.pop("echo2_collide", None)


def test_registry_arg_injection_parity(_reg_echo):
    """Registry fn receives _index/_tool_num like a method would (spec req 4)."""
    @register_tool("argprobe")
    def _probe(handler, args, response):
        return StepOutcome({"_index": args.get("_index"), "_tool_num": args.get("_tool_num")}, next_prompt="\n")
    try:
        class _H(BaseHandler):
            pass
        h = _H()
        outcome = _drain(h.dispatch("argprobe", {"msg": "x"}, _Resp(), index=3, tool_num=7))
        assert outcome.data == {"_index": 3, "_tool_num": 7}
    finally:
        _TOOL_REGISTRY.pop("argprobe", None)


def test_registry_fn_signature_uses_handler_param():
    """Registry fn accesses handler state via `handler` param, not self (spec req 5)."""
    @register_tool("cwdprobe")
    def _fn(handler, args, response):
        return StepOutcome({"cwd_seen": handler.cwd}, next_prompt="\n")
    try:
        class _H(BaseHandler):
            pass
        h = _H()
        h.cwd = "/custom/cwd"
        outcome = _drain(h.dispatch("cwdprobe", {}, _Resp(), index=0, tool_num=1))
        assert outcome.data == {"cwd_seen": "/custom/cwd"}
    finally:
        _TOOL_REGISTRY.pop("cwdprobe", None)


# ── unknown tool (spec req 1 scenario 3) ─────────────────────────

def test_unknown_tool_when_neither_method_nor_registry():
    class _H(BaseHandler):
        pass
    h = _H()
    outcome = _drain(h.dispatch("totally_missing", {"x": 1}, _Resp(), index=0, tool_num=1))
    assert "未知工具" in (outcome.next_prompt or "")
    assert outcome.data is None
    assert outcome.should_exit is False
