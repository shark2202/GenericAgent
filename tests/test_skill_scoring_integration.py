"""Integration tests for skill scoring gate (hermes-isolated-skill-scorer).

env-blocked: requires deps-complete env (Windows `uv pip install -e ".[ui]"`).
6.7 (true isolation e2e) further requires subagents merged to dev —— skipif
subagent_manager not importable. 6.8 (fallback chain) runnable on current dev.
"""

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    import ga  # noqa: F401
    _GA_IMPORTABLE = True
except Exception:
    _GA_IMPORTABLE = False

try:
    import subagent_manager  # noqa: F401
    _SUBAGENT_IMPORTABLE = True
except Exception:
    _SUBAGENT_IMPORTABLE = False

pytestmark = pytest.mark.skipif(not _GA_IMPORTABLE,
                                reason="ga.py not importable (need deps-complete env)")


# ── 6.7: true isolation e2e (deps + subagents merged) ────────────

@pytest.mark.skipif(not _SUBAGENT_IMPORTABLE,
                    reason="subagent_manager not merged to dev yet (6.7 true isolation)")
def test_distill_full_chain_with_real_subagent_scorer(monkeypatch, tmp_path):
    """6.7:monkeypatch 子 agent 产出合规打分 → distill 全链跑通(deps-complete + subagents merged)。

    真起 agentmain.py 子进程,SubagentScorer 经 run_single 派发,子 agent submit_result
    合规打分对象 → 闸门放行 → _apply_op 落盘。
    """
    # TODO: 实现细节待 subagents 合并后据 SubagentManager.run_single 真实接口补全
    # 当前 skipif 拦截,不会执行
    pytest.skip("requires subagents merged to dev (6.7)")


# ── 6.8: fallback chain e2e (runnable on current dev) ────────────

def test_distill_falls_back_to_inprocess_on_subagent_failure(monkeypatch):
    """6.8:打分子 agent 失败 → distill 降级 inprocess + Brief 记降级(当前 dev 可跑)。

    不真起子进程:mock handler._subagent_mgr.run_single 返回 failed/timed_out,
    验证 _score_with_fallback 退回 InProcessScorer + Brief 留痕。
    """
    import plugins.skill_evolution as se

    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    monkeypatch.setattr(se, "GA_SKILL_SCORER_THRESHOLD", 60)

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeClient:
        def __init__(self, op_content, score_content):
            self._contents = [op_content, score_content]
            self._idx = 0

        def chat(self, messages=None, tools=None):
            c = self._contents[self._idx] if self._idx < len(self._contents) else ""
            self._idx += 1
            return _FakeResp(c)

    class _FakeMgr:
        def run_single(self, **kw):
            class R:
                state = "failed"
                error = "subprocess crashed"
            return R()

    class _H:
        def __init__(self, client, mgr):
            self.client = client
            self.history_info = ["s"] * 10
            self._pending_briefs = []
            self._subagent_mgr = mgr
            self.cwd = "."
            self.working = {}

    monkeypatch.setattr(se, "_get_skills_catalog", lambda: ({"cat": ("d", "p")}, None))
    apply_calls = []

    def _fake_apply(handler, op):
        apply_calls.append(dict(op))

    monkeypatch.setattr(se, "_apply_op", _fake_apply)

    op_json = ('{"action": "create", "name": "x", "skill_md": "md", "reason": "r"}')
    score_json = (
        '{"score": 82, "verdict": "pass", '
        '"dims": {"reusability": 80, "verifiedness": 85, "non_redundancy": 81}, '
        '"rationale": "ok"}'
    )
    c = _FakeClient(op_json, score_json)
    h = _H(c, _FakeMgr())
    se.distill(c, h)

    # 降级到 inprocess,inprocess 返回 pass(82>=60)→ _apply_op 调用
    assert len(apply_calls) == 1
    # Brief 记降级
    assert any("degraded" in b.lower() for b in h._pending_briefs)
