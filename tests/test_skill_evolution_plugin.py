"""
Tests for plugins/skill_evolution (post-task skill-distillation, L1 hook layer).

Covers the plugin-side logic that does NOT need the heavy ga.py deps:
  - build_brief: brief text shape
  - _parse_op: JSON extraction from LLM text
  - _evolution_enabled: env gate
  - _apply_op: circuit breaker (D8) — skip after MAX_AUTO_PATCH_PER_SKILL; failed patch not counted
  - _on_agent_after: gating (env off / low turns / wrong exit_reason / valid → spawn)

distill()'s LLM call and do_skill_manage end-to-end (T1,T3,T8) need a real
handler+client and are exercised in a full-env integration test (step 5b).
"""

import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import plugins.skill_evolution as se  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_counts_and_registry():
    import agent_loop
    se._auto_patch_counts.clear()
    saved = agent_loop._TOOL_REGISTRY.get("skill_manage")
    yield
    if saved is None:
        agent_loop._TOOL_REGISTRY.pop("skill_manage", None)
    else:
        agent_loop._TOOL_REGISTRY["skill_manage"] = saved


# ── build_brief ───────────────────────────────────────────────────


def test_build_brief_shape():
    b = se.build_brief("patch", "my-skill", "fixed typo", ".agents/skills/my-skill/SKILL.md")
    assert b.startswith("\n📌[Skill蒸馏] patch `my-skill`")
    assert "fixed typo" in b
    assert "file_read" in b
    assert "[approve|edit|revert]" in b


def test_build_brief_no_reason():
    b = se.build_brief("create", "x", "", "p")
    assert b.count(" — ") == 1  # only the action->file_read separator, no why segment
    assert " — " in b  # the action -> file_read separator is always present


def test_build_brief_with_reason_has_two_separators():
    b = se.build_brief("create", "x", "why", "p")
    assert b.count(" — ") == 2  # why segment + file_read separator


# ── _parse_op ────────────────────────────────────────────────────


def test_parse_op_none_when_no_json():
    assert se._parse_op("no json here at all") is None


def test_parse_op_extracts_json():
    assert se._parse_op('prefix {"action":"none"} trailing') == {"action": "none"}


def test_parse_op_empty():
    assert se._parse_op("") is None
    assert se._parse_op(None) is None


def test_parse_op_malformed_json():
    assert se._parse_op("{bad json}") is None


# ── _evolution_enabled ───────────────────────────────────────────


def test_evolution_enabled_off(monkeypatch):
    monkeypatch.delenv("GA_SKILL_EVOLUTION_ENABLED", raising=False)
    assert se._evolution_enabled() is False


def test_evolution_enabled_on(monkeypatch):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    assert se._evolution_enabled() is True


# ── _apply_op + circuit breaker ──────────────────────────────────


class _FakeOutcome:
    def __init__(self, data):
        self.data = data


def _install_fake_skill_manage(status="ok"):
    """Register a fake skill_manage tool bound to a fresh fake handler; return the handler.
    Mirrors the post-ripple contract: _apply_op calls get_tool('skill_manage')(handler, args, None)."""
    import agent_loop
    h = _FakeHandler(status=status)

    @agent_loop.register_tool("skill_manage")
    def _fn(handler, args, response):
        h.calls.append(dict(args))
        yield "streamed"
        return _FakeOutcome({"status": h._status, "action": args["action"], "name": args["name"]})
    return h


class _FakeHandler:
    """Receives skill_manage calls via the registry (post-ripple), not as a method."""

    def __init__(self, status="ok"):
        self.calls = []
        self._pending_briefs = []
        self._status = status


def test_apply_op_patch_succeeds_and_counts(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "off")  # 既有 v1 行为走 legacy 累加(策略 A)
    h = _install_fake_skill_manage(status="ok")
    # preset 'background_review' 镜像 _safe_distill 调用上下文(Task 6 _apply_op 改造后:
    # L413 inner set/reset 后,skill_write_origin.get() 回到 caller 设的值;未设则默认
    # 'foreground' → 走 foreground else-branch(reset),断言破。设 token 使 background
    # if-branch 触发 + scoring off → _scoring_on_and_passed=False → legacy 累加)
    tok = se.skill_write_origin.set('background_review')
    try:
        se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    finally:
        se.skill_write_origin.reset(tok)
    assert len(h.calls) == 1
    assert se._auto_patch_counts["s"] == 1


def test_apply_op_create_does_not_count_circuit():
    h = _install_fake_skill_manage(status="ok")
    se._apply_op(h, {"action": "create", "name": "s", "skill_md": "x", "reason": "r"})
    assert len(h.calls) == 1
    assert "s" not in se._auto_patch_counts  # create doesn't trip the patch breaker


def test_apply_op_failed_patch_not_counted():
    h = _install_fake_skill_manage(status="invalid")
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    assert se._auto_patch_counts.get("s", 0) == 0


def test_apply_op_unknown_action_skipped():
    h = _install_fake_skill_manage()
    se._apply_op(h, {"action": "retire", "name": "s", "skill_md": "x", "reason": "r"})
    assert h.calls == []


def test_apply_op_missing_fields_skipped():
    h = _install_fake_skill_manage()
    se._apply_op(h, {"action": "patch", "name": "", "skill_md": "x", "reason": ""})
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "", "reason": ""})
    assert h.calls == []


def test_circuit_breaker_skips_after_max(monkeypatch):
    """After MAX_AUTO_PATCH_PER_SKILL successful patches, further patches are
    skipped (brief appended instead) and do_skill_manage is NOT called."""
    monkeypatch.setenv("GA_SKILL_SCORER", "off")  # 既有 v1 行为走 legacy 累加(策略 A)
    h = _install_fake_skill_manage(status="ok")
    op = {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"}
    tok = se.skill_write_origin.set('background_review')  # 镜像 _safe_distill 调用上下文
    try:
        for _ in range(se.MAX_AUTO_PATCH_PER_SKILL):
            se._apply_op(h, op)
        assert se._auto_patch_counts["s"] == se.MAX_AUTO_PATCH_PER_SKILL
        calls_before = len(h.calls)
        briefs_before = len(h._pending_briefs)

        se._apply_op(h, op)  # (MAX+1)th → circuit trips
    finally:
        se.skill_write_origin.reset(tok)
    assert len(h.calls) == calls_before  # do_skill_manage NOT called
    assert len(h._pending_briefs) == briefs_before + 1
    assert any("circuit" in b for b in h._pending_briefs)


# ── _on_agent_after gating ───────────────────────────────────────


class _FakeThread:
    def __init__(self, target=None, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self):
        se._spawned.append((self.target, self.args))


@pytest.fixture
def _capture_threads(monkeypatch):
    se._spawned = []
    monkeypatch.setattr(se, "threading", types.SimpleNamespace(Thread=_FakeThread))
    yield se._spawned


class _H:
    def __init__(self, turn=10, signal=None):
        self.current_turn = turn
        self._evolution_signal = signal


def test_agent_after_disabled_env(monkeypatch, _capture_threads):
    monkeypatch.delenv("GA_SKILL_EVOLUTION_ENABLED", raising=False)
    se._on_agent_after({"exit_reason": {"result": "CURRENT_TASK_DONE"}, "handler": _H(), "client": object()})
    assert _capture_threads == []


def test_agent_after_low_turns(monkeypatch, _capture_threads):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._on_agent_after({"exit_reason": {"result": "CURRENT_TASK_DONE"}, "handler": _H(turn=3), "client": object()})
    assert _capture_threads == []  # < SKILL_DISTILL_MIN_TURNS


def test_agent_after_wrong_exit_reason(monkeypatch, _capture_threads):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._on_agent_after({"exit_reason": {"result": "MAX_TURNS_EXCEEDED"}, "handler": _H(), "client": object()})
    assert _capture_threads == []


def test_agent_after_missing_handler_or_client(monkeypatch, _capture_threads):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._on_agent_after({"exit_reason": {"result": "CURRENT_TASK_DONE"}, "handler": None, "client": object()})
    se._on_agent_after({"exit_reason": {"result": "CURRENT_TASK_DONE"}, "handler": _H(), "client": None})
    assert _capture_threads == []


def test_agent_after_valid_current_task_done(monkeypatch, _capture_threads):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._on_agent_after(
        {"exit_reason": {"result": "CURRENT_TASK_DONE"}, "handler": _H(turn=10, signal="manual"), "client": object()}
    )
    assert len(_capture_threads) == 1
    target, args = _capture_threads[0]
    assert target is se._safe_distill
    # args = (client, handler, signal)
    assert args[1].current_turn == 10 and args[2] == "manual"


def test_agent_after_valid_exited(monkeypatch, _capture_threads):
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._on_agent_after({"exit_reason": {"result": "EXITED"}, "handler": _H(), "client": object()})
    assert len(_capture_threads) == 1


# ── reset_auto_patch_count ───────────────────────────────────────

def test_reset_auto_patch_count_single_skill():
    se._auto_patch_counts["s"] = 5
    se.reset_auto_patch_count("s")
    assert "s" not in se._auto_patch_counts


def test_reset_auto_patch_count_all():
    se._auto_patch_counts = {"a": 1, "b": 2}
    se.reset_auto_patch_count()
    assert se._auto_patch_counts == {}


# ── fitness log ──────────────────────────────────────────────────

def test_append_fitness_log_format(tmp_path):
    real_script_dir = se._skill_loader_script_dir
    try:
        se._skill_loader_script_dir = str(tmp_path)
        se._append_fitness_log("my-skill", "patch", 12, {"type": "correction"})
        exp_path = tmp_path / "memory" / "skill_exp_my-skill.md"
        content = exp_path.read_text(encoding="utf-8")
        assert "action=patch" in content
        assert "turns=12" in content
        assert '"type": "correction"' in content
        assert content.startswith("- 2")  # ISO8601 timestamp starts with year
    finally:
        se._skill_loader_script_dir = real_script_dir
