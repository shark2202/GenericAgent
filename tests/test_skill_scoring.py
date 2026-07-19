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
