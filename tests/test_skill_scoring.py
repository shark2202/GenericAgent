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
