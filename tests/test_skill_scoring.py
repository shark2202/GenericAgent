"""Tests for the skill scoring gate (hermes-isolated-skill-scorer).

Covers (by task):
  Task 1: Verdict / VerdictKind / Scorer Protocol / SCORE_SCHEMA / thresholds
  Task 2: InProcessScorer (6.1)
  Task 3: SubagentScorer + independence hard-guarantees (6.2, 6.5)
  Task 4: distill() gate insertion (6.4)
  Task 5: _resolve_scorer + fallback chain (6.3)
  Task 6: R6 breaker reset (6.6)

Integration tests (6.7/6.8) live in tests/test_skill_scoring_integration.py
(env-blocked: need deps-complete env to spawn real agentmain.py subprocess).
"""

import os
import sys
from contextlib import contextmanager

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import plugins.skill_evolution as se  # noqa: E402
from plugins.skill_evolution import (  # noqa: E402
    SCORE_SCHEMA,
    Scorer,
    Verdict,
    VerdictKind,
)

# ── Task 1: types + schema + thresholds ──────────────────────────


def test_verdict_kind_enum_values():
    assert VerdictKind.PASS.value == "pass"
    assert VerdictKind.REJECT.value == "reject"
    assert VerdictKind.REVISE.value == "revise"


def test_verdict_defaults_source_inprocess():
    v = Verdict(score=80, verdict=VerdictKind.PASS,
                dims={"reusability": 80, "verifiedness": 80, "non_redundancy": 80},
                rationale="ok")
    assert v.source == "inprocess"  # default留痕


def test_verdict_source_overridable():
    v = Verdict(score=80, verdict=VerdictKind.PASS, dims={}, rationale="", source="subagent")
    assert v.source == "subagent"


def test_scorer_is_protocol():
    # Protocol 不能直接实例化(typing.Protocol 在 3.8+ 有 _is_protocol)
    assert hasattr(Scorer, "_is_protocol") or Scorer._is_protocol


def test_score_schema_locks_three_dims():
    """v1 schema 必须锁 3 维(OQ4 决议),additionalProperties:false。"""
    props = SCORE_SCHEMA["properties"]["dims"]["properties"]
    assert set(props.keys()) == {"reusability", "verifiedness", "non_redundancy"}
    assert SCORE_SCHEMA["properties"]["dims"]["additionalProperties"] is False
    assert SCORE_SCHEMA["additionalProperties"] is False
    assert set(SCORE_SCHEMA["required"]) == {"score", "verdict", "dims", "rationale"}
    assert SCORE_SCHEMA["properties"]["verdict"]["enum"] == ["pass", "reject", "revise"]


def test_threshold_defaults(monkeypatch):
    """默认 60/600;env 可覆盖。"""
    monkeypatch.delenv("GA_SKILL_SCORER_THRESHOLD", raising=False)
    monkeypatch.delenv("GA_SKILL_SCORER_TIMEOUT", raising=False)
    # 重新 import 触发模块级常量重算
    import importlib
    importlib.reload(se)
    assert se.GA_SKILL_SCORER_THRESHOLD == 60
    assert se.GA_SKILL_SCORER_TIMEOUT == 600


# ── Task 2: InProcessScorer (6.1) ────────────────────────────────

from plugins.skill_evolution import InProcessScorer, ScorerDegraded  # noqa: E402, F401


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """记录 chat 调用,messages/tools 可断言;content 由构造注入。"""
    def __init__(self, content):
        self._content = content
        self.calls = []  # list of (messages, tools)

    def chat(self, messages=None, tools=None):
        self.calls.append((messages, tools))
        return _FakeResp(self._content)


_VALID_SCORE_JSON = (
    '{"score": 82, "verdict": "pass", '
    '"dims": {"reusability": 80, "verifiedness": 85, "non_redundancy": 81}, '
    '"rationale": "reusable pattern, verified in task"}'
)


def test_inprocess_scorer_maps_valid_json_to_verdict():
    c = _FakeClient(_VALID_SCORE_JSON)
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1", "h2"], "- cat: desc")
    assert v.score == 82
    # 用 .value 比较,而非枚举身份(==):Task 1 的 test_threshold_defaults 调
    # importlib.reload(se) 会重建 VerdictKind 类,导致同值枚举身份不等(双类问题)。
    # .value 对 reload 鲁棒,与 test_verdict_kind_enum_values 风格一致。
    assert v.verdict.value == VerdictKind.PASS.value
    assert v.dims == {"reusability": 80, "verifiedness": 85, "non_redundancy": 81}
    assert v.rationale.startswith("reusable")
    assert v.source == "inprocess"


def test_inprocess_scorer_uses_fresh_messages_and_empty_tools():
    """弱独立性(OQ5):fresh messages list + tools=[] 强制纯文本打分。"""
    c = _FakeClient(_VALID_SCORE_JSON)
    s = InProcessScorer(c)
    s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
            "md", ["h1"], "- cat: d")
    assert len(c.calls) == 1
    messages, tools = c.calls[0]
    assert tools == []                          # 强制纯文本打分
    assert messages[0]["role"] == "system"      # distinct prompt role = scorer 职责
    assert "评估" in messages[0]["content"] or "review" in messages[0]["content"].lower()


def test_inprocess_scorer_desc_excludes_op_reason():
    """user message 禁含 op['reason'](D3 独立性,InProcessScorer 同样适用)。"""
    c = _FakeClient(_VALID_SCORE_JSON)
    s = InProcessScorer(c)
    s.score({"action": "create", "name": "x", "skill_md": "md",
             "reason": "TOPSECRET_GENERATOR_REASON"},
            "md", ["h1"], "- cat: d")
    messages, _ = c.calls[0]
    user_msg = messages[-1]["content"]
    assert "TOPSECRET_GENERATOR_REASON" not in user_msg


def test_inprocess_scorer_degrades_on_non_json():
    """client.chat 返回非 JSON → Verdict(score=0, REJECT, source='degraded')。"""
    c = _FakeClient("not json at all")
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")
    assert v.score == 0
    assert v.verdict.value == VerdictKind.REJECT.value
    assert v.source == "degraded"


def test_inprocess_scorer_degrades_on_missing_dim():
    """dims 缺维度 → schema 校验失败 → 降级。"""
    bad = '{"score": 50, "verdict": "pass", "dims": {"reusability": 50}, "rationale": "x"}'
    c = _FakeClient(bad)
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")
    assert v.source == "degraded"
    assert v.verdict.value == VerdictKind.REJECT.value


def test_inprocess_scorer_handles_codeblock_json():
    """LLM 可能把 JSON 包在 ```json ... ``` 代码块里。"""
    c = _FakeClient(f"```json\n{_VALID_SCORE_JSON}\n```")
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")
    assert v.score == 82
    assert v.source == "inprocess"


# ── Task 3: SubagentScorer + independence (6.2, 6.5) ─────────────

from plugins.skill_evolution import SubagentScorer, _build_scorer_desc  # noqa: E402


class _WorkerResult:
    """镜像 subagent_manager.WorkerResult(uuid, state, result, error, worktree_path)。"""
    def __init__(self, state, result=None, error=None, uuid="u", worktree_path="wt"):
        self.uuid = uuid
        self.state = state          # "completed" / "failed" / "timed_out"
        self.result = result        # dict(合规打分对象)或 None
        self.error = error
        self.worktree_path = worktree_path


class _FakeMgr:
    """mock SubagentManager.run_single,记录 desc/schema/tools_subset/base_ref/timeout。"""
    def __init__(self, result):
        self._result = result  # _WorkerResult
        self.calls = []

    def run_single(self, desc=None, schema=None, tools_subset=None,
                   base_ref=None, model=None, tools=None, timeout_s=None):
        self.calls.append({
            "desc": desc, "schema": schema, "tools_subset": tools_subset,
            "base_ref": base_ref, "timeout_s": timeout_s,
        })
        return self._result


def test_subagent_scorer_maps_completed_to_verdict():
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 75, "verdict": "pass",
        "dims": {"reusability": 70, "verifiedness": 80, "non_redundancy": 75},
        "rationale": "good",
    }))
    s = SubagentScorer(mgr)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "SECRET"},
                "md", ["h1"], "- cat: d")
    assert v.score == 75
    # 用 .value 比较,而非枚举身份(==):test_threshold_defaults 调 importlib.reload(se)
    # 会重建 VerdictKind 类,致同值枚举身份不等(双类问题)。.value 对 reload 鲁棒,
    # 与 Task 2 InProcessScorer 测试风格一致。
    assert v.verdict.value == VerdictKind.PASS.value
    assert v.source == "subagent"


def test_subagent_scorer_raises_degraded_on_failed():
    """run_single failed → ScorerDegraded(distill 退回 InProcessScorer,D6)。"""
    mgr = _FakeMgr(_WorkerResult("failed", error="boom"))
    s = SubagentScorer(mgr)
    # 用 se.ScorerDegraded(模块属性引用)而非直接 ScorerDegraded:test_threshold_defaults
    # 调 importlib.reload(se) 会重建 ScorerDegraded 类,直接 import 的 ScorerDegraded 变成
    # 旧类引用,pytest.raises 捕不到 reload 后的新类实例(双类问题,与 VerdictKind 同源)。
    with pytest.raises(se.ScorerDegraded):
        s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")


def test_subagent_scorer_raises_degraded_on_timed_out():
    mgr = _FakeMgr(_WorkerResult("timed_out"))
    s = SubagentScorer(mgr)
    with pytest.raises(se.ScorerDegraded):
        s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")


def test_subagent_scorer_passes_score_schema():
    """run_single(schema=SCORE_SCHEMA) —— 子 agent 经 SubagentManager 二次校验。"""
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x",
    }))
    s = SubagentScorer(mgr)
    s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
            "md", ["h1"], "- cat: d")
    assert mgr.calls[0]["schema"] == se.SCORE_SCHEMA


def test_subagent_scorer_tools_subset_readonly():
    """D3/OQ1:tools_subset=['file_read'],不含执行类工具;submit_result 由 _apply_task_mode 注入不显式列。"""
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x",
    }))
    s = SubagentScorer(mgr)
    s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
            "md", ["h1"], "- cat: d")
    ts = mgr.calls[0]["tools_subset"]
    assert ts == ["file_read"]
    for banned in ("code_run", "file_write", "web_scan", "file_patch"):
        assert banned not in ts


def test_subagent_scorer_base_ref_none():
    """OQ2:base_ref=None → 默认 dev HEAD(候选 SKILL.md 全文已在 desc)。"""
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x",
    }))
    s = SubagentScorer(mgr)
    s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
            "md", ["h1"], "- cat: d")
    assert mgr.calls[0]["base_ref"] is None


def test_subagent_scorer_timeout_from_env():
    """timeout_s = GA_SKILL_SCORER_TIMEOUT(默认 600)。"""
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x",
    }))
    s = SubagentScorer(mgr)
    s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
            "md", ["h1"], "- cat: d")
    assert mgr.calls[0]["timeout_s"] == se.GA_SKILL_SCORER_TIMEOUT


# ── Task 3 (3.1, 6.5): desc 独立性硬保证 ─────────────────────────

def test_build_scorer_desc_excludes_op_reason():
    """D3 硬保证:desc 不含 op['reason']。"""
    desc = _build_scorer_desc(
        skill_md="# Skill\n## When\nbody",
        history=["task step 1", "task step 2"],
        catalog="- cat: d",
    )
    # desc 里只应有 skill_md / history / catalog,reason 字段不参与构造
    assert "## When" in desc
    assert "task step 1" in desc
    assert "- cat: d" in desc


def test_build_scorer_desc_no_reason_keyword_when_op_has_reason():
    """构造 desc 时即使 op 有 reason,desc 也不含它(调用方验证)。"""
    desc = _build_scorer_desc("md-body", ["h1"], "catalog-text")
    # 候选 SKILL.md 全文 + history[-40:] + catalog
    assert "md-body" in desc
    assert "h1" in desc
    assert "catalog-text" in desc
    # 结构分隔
    assert desc.count("---") >= 2


def test_subagent_scorer_desc_actually_excludes_reason():
    """端到端断言:SubagentScorer 调 run_single 时 desc 不含 op['reason']。"""
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x",
    }))
    s = SubagentScorer(mgr)
    s.score({"action": "create", "name": "x", "skill_md": "md",
             "reason": "GENERATOR_REASON_MUST_NOT_LEAK"},
            "md", ["h1"], "- cat: d")
    desc = mgr.calls[0]["desc"]
    assert "GENERATOR_REASON_MUST_NOT_LEAK" not in desc


# ── Task 4: distill() gate (6.4) ─────────────────────────────────

class _FakeScoreClient:
    """distill() 用:第一次 chat 返回 op JSON,第二次(inprocess scorer)返回打分 JSON。"""
    def __init__(self, op_content, score_content):
        self._contents = [op_content, score_content]
        self._idx = 0
        self.chat_calls = []

    def chat(self, messages=None, tools=None):
        self.chat_calls.append((messages, tools))
        c = self._contents[self._idx] if self._idx < len(self._contents) else ""
        self._idx += 1
        return _FakeResp(c)


class _DistillHandler:
    """最小 handler:client / history_info / _pending_briefs / cwd / _subagent_mgr。"""
    def __init__(self, client, history=None, subagent_mgr=None):
        self.client = client
        self.history_info = history or ["step"] * 10
        self._pending_briefs = []
        self._subagent_mgr = subagent_mgr
        self.cwd = "."
        self.working = {}


_OP_CREATE = (
    '{"action": "create", "name": "new-skill", '
    '"skill_md": "---\\nname: new-skill\\ndescription: \\"d\\"\\nversion: 0.1\\nauthor: agent\\nevolvable: true\\nevolved_from: null\\nmcp_dependencies: []\\n---\\n# New\\n## When\\nbody", '
    '"reason": "why"}'
)


def _patch_apply_op(monkeypatch):
    """patch se._apply_op 记录调用,避免真落盘。返回 (recorder, restore 默认)。"""
    calls = []

    def _fake_apply_op(handler, op):
        calls.append(dict(op))

    monkeypatch.setattr(se, "_apply_op", _fake_apply_op)
    return calls


def _patch_get_skills_catalog(monkeypatch):
    monkeypatch.setattr(se, "_get_skills_catalog", lambda: ({"cat": ("d", "p")}, None))


def test_distill_gate_passes_scored_pass_to_apply_op(monkeypatch):
    """verdict=pass + score>=阈值 → _apply_op 调用。"""
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    monkeypatch.setattr(se, "GA_SKILL_SCORER_THRESHOLD", 60)
    c = _FakeScoreClient(_OP_CREATE, _VALID_SCORE_JSON)  # score=82 >= 60
    h = _DistillHandler(c)
    se.distill(c, h)
    assert len(apply_calls) == 1
    assert apply_calls[0]["name"] == "new-skill"


def test_distill_gate_blocks_on_reject(monkeypatch):
    """verdict=reject → _apply_op 不调用 + _pending_briefs 有 rejected-by-scorer。"""
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    monkeypatch.setattr(se, "GA_SKILL_SCORER_THRESHOLD", 60)
    reject_json = (
        '{"score": 30, "verdict": "reject", '
        '"dims": {"reusability": 30, "verifiedness": 20, "non_redundancy": 40}, '
        '"rationale": "low quality"}'
    )
    c = _FakeScoreClient(_OP_CREATE, reject_json)
    h = _DistillHandler(c)
    se.distill(c, h)
    assert apply_calls == []                       # 不落盘
    assert any("rejected" in b.lower() or "failed review" in b.lower()
               for b in h._pending_briefs)


def test_distill_gate_blocks_on_low_score_pass(monkeypatch):
    """verdict=pass 但 score<阈值 → 不落盘(score=50 < 60)。"""
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    monkeypatch.setattr(se, "GA_SKILL_SCORER_THRESHOLD", 60)
    low_json = (
        '{"score": 50, "verdict": "pass", '
        '"dims": {"reusability": 50, "verifiedness": 50, "non_redundancy": 50}, '
        '"rationale": "borderline"}'
    )
    c = _FakeScoreClient(_OP_CREATE, low_json)
    h = _DistillHandler(c)
    se.distill(c, h)
    assert apply_calls == []


def test_distill_gate_score_eq_threshold_passes(monkeypatch):
    """score==阈值 → 放行(>=,边界)。"""
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    monkeypatch.setattr(se, "GA_SKILL_SCORER_THRESHOLD", 60)
    eq_json = (
        '{"score": 60, "verdict": "pass", '
        '"dims": {"reusability": 60, "verifiedness": 60, "non_redundancy": 60}, '
        '"rationale": "at threshold"}'
    )
    c = _FakeScoreClient(_OP_CREATE, eq_json)
    h = _DistillHandler(c)
    se.distill(c, h)
    assert len(apply_calls) == 1


def test_distill_gate_off_no_scoring(monkeypatch):
    """GA_SKILL_SCORER 未设 + 无 _subagent_mgr → 走 inprocess 兜底(默认开启打分)。

    注:缺省 _subagent_mgr absent → InProcessScorer,闸门仍跑。
    要测"v1 行为(无打分直接 _apply_op)",设 GA_SKILL_SCORER=off(D5 gate off 语义)。
    """
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")   # 显式 gate off
    c = _FakeScoreClient(_OP_CREATE, _VALID_SCORE_JSON)
    h = _DistillHandler(c, subagent_mgr=None)
    se.distill(c, h)
    assert len(apply_calls) == 1                    # 直接 _apply_op,无打分
    assert len(c.chat_calls) == 1                   # 只调一次 chat(生成 op),无 scorer chat


def test_distill_none_op_skips_gate(monkeypatch):
    """op=none → 不走闸门也不 _apply_op(v1 短路保留)。"""
    _patch_get_skills_catalog(monkeypatch)
    apply_calls = _patch_apply_op(monkeypatch)
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    c = _FakeScoreClient('{"action": "none"}', _VALID_SCORE_JSON)
    h = _DistillHandler(c)
    se.distill(c, h)
    assert apply_calls == []
    assert len(c.chat_calls) == 1                   # 只生成 op,无 scorer 调用


# ── Task 5: _resolve_scorer + fallback (6.3) ─────────────────────

from plugins.skill_evolution import _resolve_scorer, _score_with_fallback  # noqa: E402


class _StubClient:
    def chat(self, messages=None, tools=None):
        return _FakeResp(_VALID_SCORE_JSON)


def test_resolve_scorer_inprocess_explicit(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    h = _DistillHandler(_StubClient(), subagent_mgr=object())  # 有 mgr 也强制 inprocess
    s = _resolve_scorer(h)
    # 用 se.InProcessScorer(模块属性引用)而非直接 InProcessScorer:
    # test_threshold_defaults 调 importlib.reload(se) 会重建 InProcessScorer 类,
    # 直接 import 的 InProcessScorer 变成旧类引用,isinstance 返 False(双类问题)。
    # se.InProcessScorer 对 reload 鲁棒,与 Task 2/3 的 .value / se.ScorerDegraded 风格一致。
    assert isinstance(s, se.InProcessScorer)


def test_resolve_scorer_subagent_with_mgr(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    h = _DistillHandler(_StubClient(), subagent_mgr=_FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x"})))
    s = _resolve_scorer(h)
    assert isinstance(s, se.SubagentScorer)


def test_resolve_scorer_subagent_without_mgr_degrades(monkeypatch, capsys):
    """requested subagent 但 _subagent_mgr absent → inprocess + stderr 一行。"""
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    s = _resolve_scorer(h)
    assert isinstance(s, se.InProcessScorer)
    captured = capsys.readouterr()
    assert "degrading to inprocess" in captured.err


def test_resolve_scorer_default_uses_subagent_when_mgr_present(monkeypatch):
    monkeypatch.delenv("GA_SKILL_SCORER", raising=False)
    mgr = _FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x"}))
    h = _DistillHandler(_StubClient(), subagent_mgr=mgr)
    s = _resolve_scorer(h)
    assert isinstance(s, se.SubagentScorer)


def test_resolve_scorer_default_inprocess_when_mgr_absent(monkeypatch):
    monkeypatch.delenv("GA_SKILL_SCORER", raising=False)
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    s = _resolve_scorer(h)
    assert isinstance(s, se.InProcessScorer)


def test_resolve_scorer_off_returns_none(monkeypatch):
    """显式 off → None(gate off,v1 行为)。"""
    for v in ("off", "none", "false", "0"):
        monkeypatch.setenv("GA_SKILL_SCORER", v)
        h = _DistillHandler(_StubClient(), subagent_mgr=object())
        assert _resolve_scorer(h) is None, f"GA_SKILL_SCORER={v} should be off"


def test_resolve_scorer_unknown_mode_falls_back_inprocess(monkeypatch):
    """未知值 → inprocess 兜底(不静默 v1)。"""
    monkeypatch.setenv("GA_SKILL_SCORER", "weird-value")
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    s = _resolve_scorer(h)
    assert isinstance(s, se.InProcessScorer)


def test_score_with_fallback_degrades_on_subagent_failure(monkeypatch):
    """SubagentScorer failed → 退回 InProcessScorer + Brief 记降级(D6)。"""
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    mgr = _FakeMgr(_WorkerResult("failed", error="boom"))  # run_single failed
    h = _DistillHandler(_StubClient(), subagent_mgr=mgr)
    v = _score_with_fallback(h, {"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                             "md", ["h1"], "- cat: d")
    # 降级到 inprocess,StubClient 返回合规 JSON
    assert v.source == "degraded"
    assert any("degraded" in b.lower() for b in h._pending_briefs)


def test_score_with_fallback_degrades_on_timeout(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    mgr = _FakeMgr(_WorkerResult("timed_out"))
    h = _DistillHandler(_StubClient(), subagent_mgr=mgr)
    v = _score_with_fallback(h, {"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                             "md", ["h1"], "- cat: d")
    assert v.source == "degraded"
    assert any("degraded" in b.lower() for b in h._pending_briefs)


def test_score_with_fallback_passes_through_inprocess(monkeypatch):
    """InProcessScorer 正常返回 → 不降级。"""
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    v = _score_with_fallback(h, {"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                             "md", ["h1"], "- cat: d")
    assert v.source == "inprocess"
    assert h._pending_briefs == []


# ── Task 6: R6 breaker reset (6.6) ───────────────────────────────

# 注:不顶层 `from plugins.skill_evolution import reset_auto_patch_count`(brief 原
# 写法)—— 会致 collect-time ImportError 阻断 TDD RED。所有调用点用 se.reset_auto_patch_count
# (模块属性引用,reload 鲁棒,Task 1 concern),无需顶层 import。


@pytest.fixture(autouse=True)
def _reset_r6_counts_and_registry():
    """Per-test: clear _auto_patch_counts / _consecutive_auto_distills; save/restore
    skill_manage tool registry.

    镜像 test_skill_evolution_plugin.py 的 _reset_counts_and_registry —— R6 测试经
    _install_fake_skill_manage_status 注册 fake skill_manage,需在测试间清理避免污染
    其他文件(如 test_skill_manage_registry)。
    """
    import agent_loop
    se._auto_patch_counts.clear()
    se._consecutive_auto_distills = 0
    saved = agent_loop._TOOL_REGISTRY.get("skill_manage")
    yield
    if saved is None:
        agent_loop._TOOL_REGISTRY.pop("skill_manage", None)
    else:
        agent_loop._TOOL_REGISTRY["skill_manage"] = saved


@contextmanager
def _bg_origin():
    """Preset 'background_review' origin to mirror _safe_distill → distill → _apply_op.

    偏离 brief 逐字测试(brief 测试不 set token 直接调 _apply_op):_apply_op L413 的
    inner set/reset 后,skill_write_origin.get() 在 status check 处回到 caller 设的值
    (未设则默认 'foreground')。brief 的 "background path" 测试若不 set token,会走
    foreground else-branch(reset),无法验证 scored-pass / scoring-off 累加。
    用 _bg_origin() 包裹 _apply_op 调用,镜像 _safe_distill 在线程入口 set
    'background_review' 的生产调用链。
    """
    tok = se.skill_write_origin.set('background_review')
    try:
        yield
    finally:
        se.skill_write_origin.reset(tok)


def test_reset_auto_patch_count_zeros_counter():
    se._auto_patch_counts["s"] = 4
    # 模块属性引用(reload 鲁棒,Task 1 concern):直接 import 的 reset_auto_patch_count
    # 在 test_threshold_defaults 调 importlib.reload(se) 后 __globals__ 指向旧模块 dict,
    # 与 se._auto_patch_counts(新模块 dict)不一致 → 假失败。se.reset_auto_patch_count 鲁棒。
    se.reset_auto_patch_count("s")
    assert se._auto_patch_counts["s"] == 0


def test_reset_auto_patch_count_unknown_name_creates_zero():
    """reset 未知 name → 创建为 0(幂等)。"""
    assert "never" not in se._auto_patch_counts
    se.reset_auto_patch_count("never")
    assert se._auto_patch_counts["never"] == 0


def _install_fake_skill_manage_status(status="ok"):
    """镜像 test_skill_evolution_plugin.py 的 _install_fake_skill_manage,返回 handler。"""
    import agent_loop

    class _FH:
        def __init__(self):
            self.calls = []
            self._pending_briefs = []

        @property
        def _status(self):
            return status

    h = _FH()

    @agent_loop.register_tool("skill_manage")
    def _fn(handler, args, response):
        h.calls.append(dict(args))
        from tests.test_skill_evolution_plugin import _FakeOutcome
        yield "streamed"
        return _FakeOutcome({"status": status, "action": args["action"], "name": args["name"]})

    return h


def test_r6_foreground_patch_resets_counter(monkeypatch):
    """D5(a):foreground do_skill_manage patch 成功 → reset 清零。

    模拟 foreground:不 set background_review token(默认 foreground)→ _apply_op
    内 L413 set/reset 后,skill_write_origin.get()=='foreground' → 走 else-branch
    (foreground 修正 → reset)。
    """
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    se._auto_patch_counts["s"] = 3
    h = _install_fake_skill_manage_status("ok")
    # foreground 路径:skill_write_origin 默认 'foreground'(不 set _bg_origin)
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    assert se._auto_patch_counts["s"] == 0   # foreground 成功 → 清零


def test_r6_scored_pass_background_resets_counter(monkeypatch):
    """D5(b)/OQ3:scored-pass background patch 成功 → 清零(不是不累加)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    se._auto_patch_counts["s"] = 3
    h = _install_fake_skill_manage_status("ok")
    # background path:_bg_origin 预设 'background_review'(镜像 _safe_distill)
    with _bg_origin():
        se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    # background + scoring on → scored-pass → 清零
    assert se._auto_patch_counts["s"] == 0


def test_r6_scoring_off_background_increments(monkeypatch):
    """打分 off(v1 模式)background patch → 原累加(legacy)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")
    se._auto_patch_counts["s"] = 2
    h = _install_fake_skill_manage_status("ok")
    with _bg_origin():
        se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    assert se._auto_patch_counts["s"] == 3   # 累加,非清零


def test_r6_circuit_still_trips_at_max(monkeypatch):
    """5 次后第 6 次 → Brief 不落盘(熔断仍有效,R6 修复不破熔断)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")
    h = _install_fake_skill_manage_status("ok")
    op = {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"}
    with _bg_origin():
        for _ in range(se.MAX_AUTO_PATCH_PER_SKILL):
            se._apply_op(h, op)
        assert se._auto_patch_counts["s"] == se.MAX_AUTO_PATCH_PER_SKILL
        calls_before = len(h.calls)
        briefs_before = len(h._pending_briefs)
        se._apply_op(h, op)   # (MAX+1)th → 熔断
    assert len(h.calls) == calls_before
    assert len(h._pending_briefs) == briefs_before + 1
    assert any("circuit" in b for b in h._pending_briefs)


def test_r6_foreground_reset_unlocks_after_cap(monkeypatch):
    """到达 cap 后 foreground 修正复位 → 后续 auto-patch 不锁死(R6 兑现)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")
    h = _install_fake_skill_manage_status("ok")
    op = {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"}
    with _bg_origin():
        for _ in range(se.MAX_AUTO_PATCH_PER_SKILL):
            se._apply_op(h, op)
        assert se._auto_patch_counts["s"] == se.MAX_AUTO_PATCH_PER_SKILL
    # foreground 修正复位(direct call,不依赖 token)
    se.reset_auto_patch_count("s")
    assert se._auto_patch_counts["s"] == 0
    # 后续 background auto-patch 不锁死
    with _bg_origin():
        se._apply_op(h, op)
    assert len(h.calls) == se.MAX_AUTO_PATCH_PER_SKILL + 1   # 第 6 次成功落盘
    assert se._auto_patch_counts["s"] == 1


# ── Task 6 (production path): tools/skill_manage.py foreground reset ──
#
# 偏离 brief(用户授权):brief Step 3 在 _apply_op 内 foreground else-branch 在
# 生产中是死代码 —— foreground skill_manage(LLM 直调 tool)不经 _apply_op
# (_apply_op 只被 distill()←_safe_distill 后台线程调用)。真正兑现 D5(a) foreground
# 复位:在 tools/skill_manage.py patch 成功路径调 reset_auto_patch_count(name)。
# 用户明确授权:"若 foreground 不经 _apply_op 则需在 tools/skill_manage.py 补"。
# 本测试走真实 tools/skill_manage.skill_manage(不经 _apply_op 的 fake),
# 验证 foreground patch 成功 → counter 清零。


def test_r6_foreground_skill_manage_resets_counter(monkeypatch, tmp_path):
    """D5(a) production path:foreground skill_manage tool patch 成功 → reset 清零。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.delenv("GA_SKILL_SCORER", raising=False)
    se._auto_patch_counts["s"] = 3

    import tools.skill_manage as sm
    skill_path = str(tmp_path / "SKILL.md")
    # mock 磁盘 I/O 依赖(catalog / provenance / backup / validate / cache / sync)
    monkeypatch.setattr(sm, "_get_skills_catalog",
                        lambda: ({"s": ("desc", skill_path)}, None))
    monkeypatch.setattr(sm, "parse_provenance",
                        lambda p: {"author": "agent", "evolvable": True,
                                   "evolved_from": None, "version": "0.1"})
    monkeypatch.setattr(sm, "backup_skill_prev", lambda p: None)
    monkeypatch.setattr(sm, "validate_skill", lambda p: (True, ""))
    monkeypatch.setattr(sm, "_reset_skill_cache", lambda: None)
    monkeypatch.setattr(sm, "sync_skills_to_l1", lambda: None)

    class _H:
        def __init__(self, cwd):
            self.cwd = str(cwd)
            self._pending_briefs = []

        def _get_anchor_prompt(self, skip=False):
            return "anchor"

    h = _H(tmp_path)
    # foreground:不 set background_review token(默认 foreground)
    skill_md = "---\nname: s\ndescription: \"d\"\n---\n# S\n## When\nbody"
    gen = sm.skill_manage(
        h, {"action": "patch", "name": "s", "skill_md": skill_md,
            "reason": "r", "_index": 0}, None)
    try:
        while True:
            next(gen)
    except StopIteration as e:
        outcome = e.value
    assert getattr(outcome, 'data', {}).get('status') == 'ok'
    assert se._auto_patch_counts["s"] == 0   # foreground 成功 → 清零


def test_r6_background_skill_manage_does_not_reset(monkeypatch, tmp_path):
    """对照:background skill_manage(origin='background_review')→ 不在 skill_manage
    内 reset(由 _apply_op status check 处理累加/清零);counter 保持原值。

    防御性测试:确保 tools/skill_manage.py 的 reset 调用只在 foreground 触发,
    不重复 background 路径的计数器处置(避免双重清零/双重累加)。
    """
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.delenv("GA_SKILL_SCORER", raising=False)
    se._auto_patch_counts["s"] = 2

    import tools.skill_manage as sm
    skill_path = str(tmp_path / "SKILL.md")
    monkeypatch.setattr(sm, "_get_skills_catalog",
                        lambda: ({"s": ("desc", skill_path)}, None))
    monkeypatch.setattr(sm, "parse_provenance",
                        lambda p: {"author": "agent", "evolvable": True,
                                   "evolved_from": None, "version": "0.1"})
    monkeypatch.setattr(sm, "backup_skill_prev", lambda p: None)
    monkeypatch.setattr(sm, "validate_skill", lambda p: (True, ""))
    monkeypatch.setattr(sm, "_reset_skill_cache", lambda: None)
    monkeypatch.setattr(sm, "sync_skills_to_l1", lambda: None)

    class _H:
        def __init__(self, cwd):
            self.cwd = str(cwd)
            self._pending_briefs = []

        def _get_anchor_prompt(self, skip=False):
            return "anchor"

    h = _H(tmp_path)
    # background:设 'background_review'(镜像 _apply_op L413 set)
    tok = se.skill_write_origin.set('background_review')
    try:
        skill_md = "---\nname: s\ndescription: \"d\"\n---\n# S\n## When\nbody"
        gen = sm.skill_manage(
            h, {"action": "patch", "name": "s", "skill_md": skill_md,
                "reason": "r", "_index": 0}, None)
        try:
            while True:
                next(gen)
        except StopIteration as e:
            outcome = e.value
    finally:
        se.skill_write_origin.reset(tok)
    assert getattr(outcome, 'data', {}).get('status') == 'ok'
    # background 路径:skill_manage 内不 reset(由 _apply_op status check 处置)
    # counter 保持原值 2(由调用方 _apply_op 决定累加/清零,本测试只验 skill_manage 不染指)
    assert se._auto_patch_counts["s"] == 2


# ── Task 7: ga.py _subagent_mgr wiring (5.1) ────────────────────

def test_subagent_mgr_absent_when_module_not_importable(monkeypatch):
    """subagents 未合并到 dev → ga.py import subagent_manager 失败 → _subagent_mgr=None。

    本测试不 import ga.py(重型 deps),而是验证 _resolve_scorer 在 _subagent_mgr=None 时
    缺省走 InProcessScorer(已在 Task 5 覆盖)。此处仅断言 subagent_manager 模块在当前 dev
    不可 import(防御性,确认 try/except 必要)。
    """
    try:
        import subagent_manager  # noqa: F401
        has_module = True
    except ImportError:
        has_module = False
    if has_module:
        pytest.skip("subagent_manager 已合并到 dev(本 change 落地后真隔离可端到端跑)")
    # 当前 dev 无 subagent_manager → ga.py try/except 走 except 分支 → _subagent_mgr=None
    # → _resolve_scorer 缺省返回 InProcessScorer(InProcessScorer 路径独立端到端跑)
    assert has_module is False
