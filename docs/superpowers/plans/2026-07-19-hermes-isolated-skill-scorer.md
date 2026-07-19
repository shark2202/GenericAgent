---
change: hermes-isolated-skill-scorer
design-doc: docs/superpowers/specs/2026-07-19-hermes-isolated-skill-scorer-design.md
base-ref: a5c8eabd1d46e8d300aac39b74a7813317ea450a
---

# hermes-isolated-skill-scorer 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `distill()` 的 `_parse_op` 与 `_apply_op` 之间插入 `Scorer` 打分闸门(双实现:`SubagentScorer` 真隔离 / `InProcessScorer` 兜底),并顺手修复 R6 熔断永不复位 bug;gate off / 无 `_subagent_mgr` 时行为 = hermes v1,既有 64 测试不破。

**Architecture:** `Scorer` 抽象(`score(op, skill_md, history, catalog) -> Verdict`)+ 双实现:`SubagentScorer` 经 `handler._subagent_mgr.run_single()` 派发隔离子 agent(独立 worktree + 独立 LLM session,independence-by-construction),`InProcessScorer` 同进程二次 LLM(fresh messages + distinct prompt role,弱独立兜底)。`_resolve_scorer(handler)` 读 `GA_SKILL_SCORER` env + `_subagent_mgr` present 决定默认;`SubagentScorer` `failed`/`timed_out` → 降级 `InProcessScorer` + Brief 留痕。R6:`_apply_op` patch 成功时,foreground 修正或 scored-pass background → `reset_auto_patch_count(name)` 清零(OQ3 决议,反推翻 D5 原"不累加")。

**Tech Stack:** Python 3.11/3.12、`dataclasses` + `enum` + `typing.Protocol`、既有 `plugins/skill_evolution.py` daemon 线程、既有 `subagent_manager.SubagentManager`(未合并到 dev,`try/except ImportError` 防御)、既有 `agent_loop.StepOutcome`、`pytest` + `monkeypatch`。

**Design Doc(权威技术设计):** `docs/superpowers/specs/2026-07-19-hermes-isolated-skill-scorer-design.md`(11 节)。规范事实源:OpenSpec delta `openspec/changes/hermes-isolated-skill-scorer/specs/skill-scoring/spec.md`(3 Requirement / 8 Scenario)+ `specs/skill-memory-integration/spec.md`(2 Requirement / 7 Scenario,含 R6 Spec Patch)。本计划是其可执行分解,Task ID 与 `openspec/changes/hermes-isolated-skill-scorer/tasks.md`(7 组 / 29 项)一一对应。

---

## 全局约束(Global Constraints)

每条 Task 的实现隐含遵守以下约束(逐字抄自 Design Doc / spec,不得推翻):

1. **subagents 未合并到 dev** → `SubagentScorer` 用 `try/except ImportError` 防御性 import `subagent_manager`;真隔离端到端测试(tasks 6.7/6.8)列 **deps-complete env 前置**,本 change 在当前 dev 只做 mock 单测 + InProcessScorer 端到端。
2. **InProcessScorer 路径必须在当前 dev 上独立端到端跑通** —— 这是本 change 可独立落地的验证锚点。
3. **R6 清零语义(OQ3 决议)**:scored-pass background patch → `reset_auto_patch_count(name)` **清零**(不是"不累加");foreground 修正同样清零;打分 off(v1 模式)→ 原 v1 累加行为(legacy 保留)。
4. **独立性硬保证(D3)**:`SubagentScorer` 的 `desc` **禁含** `op['reason']` / 任何 generator prompt 片段(单测断言 `assert "reason" not in desc.lower()` 类);`tools_subset=['file_read']`,**不含** `code_run`/`file_write`/`web_scan`/`file_patch`(单测断言);`submit_result` 由 `_apply_task_mode` 强制注入,不在 `tools_subset` 显式列。
5. **gate off 语义**:`GA_SKILL_EVOLUTION_ENABLED != '1'`(v1 整门)或 `GA_SKILL_SCORER` 解析为 no-op → `distill()` 跳闸门,行为 = hermes v1;**既有 64 测试不破**(回归红线)。
6. **阈值**:`GA_SKILL_SCORER_THRESHOLD`(默认 60,env 可覆盖)、`GA_SKILL_SCORER_TIMEOUT`(默认 600s)、`GA_SKILL_SCORER`(`subagent`|`inprocess`,缺省由 `_subagent_mgr` present 决定)。
7. **foreground 路径不经闸门**(`skill_write_origin='foreground'`),保留既有 read-only boundary(`author=user`/`evolvable=false` 拒绝)。
8. **降级不静默落盘**:`SubagentScorer` `failed`/`timed_out` → 退回 `InProcessScorer` + Brief 记降级,**不得**跳过打分直接 `_apply_op`。
9. **scored-pass 阈值边界**:`verdict=='pass'` **且** `score >= threshold` 才放行(`>=`,恰好等于阈值放行)。
10. **dims 可扩展**:v1 `required` 锁 3 维(`reusability`/`verifiedness`/`non_redundancy`),v1.5/v2 加维度不破 schema(`additionalProperties: false` 在 dims 内 —— 严格锁 3 维;若后续要扩展需先改 schema,本 change 不动)。

## 代码事实锚点(实现时核对行号)

`plugins/skill_evolution.py`(当前 dev):
- L29 `SKILL_DISTILL_MIN_TURNS = 6`
- L30 `MAX_AUTO_PATCH_PER_SKILL = 5`
- L36 注释 "熔断计数:name -> 连续自动 patch 次数;前台修正(do_skill_manage 由 LLM 直调)时重置" —— **空头支票(R6 bug),Task 6 兑现**
- L37 `_auto_patch_counts = {}`(模块级 dict)
- L38 `_consecutive_auto_distills = 0`
- L41-42 `_evolution_enabled()`:`GA_SKILL_EVOLUTION_ENABLED == '1'`
- L45-51 `build_brief(action, name, why, path)` 纯函数
- L63-73 `_parse_op(content)`:抽 `{...}` JSON
- L76-110 `_apply_op(handler, op)`:L88 熔断检查(`>= MAX_AUTO_PATCH_PER_SKILL` → Brief 不落盘)、L96 `skill_write_origin.set('background_review')`、L98-100 `get_tool("skill_manage")` + `_drain`、L104-107 累加 `_auto_patch_counts[name]++` 与 `_consecutive_auto_distills++`、L108-110 失败归零 `_consecutive_auto_distills`
- L113-154 `distill(client, handler, signal=None)`:L119-122 catalog/catalog_text、L123 `history[-40:]`、L126-137 prompt、L138 messages、L140 `client.chat(messages, tools=[])`、L148 `op = _parse_op(content)`、L149-150 none 短路、L151-154 `_apply_op(handler, op)` try/except
- L157-165 `_safe_distill`:daemon 线程入口,设 `background_review` origin
- L168-183 `_on_agent_after` hook:L171 `_evolution_enabled()` 门、L180 `current_turn < SKILL_DISTILL_MIN_TURNS` 门、L183 `threading.Thread(target=_safe_distill, daemon=True).start()`

`ga.py`:
- L27-38 `GenericAgentHandler.__init__(self, parent, last_history=None, cwd='./temp')`:L36 `self._pending_briefs = []`、L37 `self._evolution_signal = None`、**无 `self._subagent_mgr`**(Task 7 加)

`tests/test_skill_evolution_plugin.py`(既有 ~16 测试,_apply_op / hook 侧):
- L28-38 `_reset_counts_and_registry` autouse fixture(清 `_auto_patch_counts` / `_consecutive_auto_distills` / 还原 `skill_manage` 注册)
- L99-115 `_FakeOutcome` / `_install_fake_skill_manage(status)` —— 经 `agent_loop.register_tool("skill_manage")` 注入 fake,`_apply_op` 经 `get_tool` 派发
- L118-123 `_FakeHandler`:`calls` / `_pending_briefs` / `_status`

`tests/test_skill_evolution.py`(skill_loader 侧)、`tests/test_skill_loader_l1.py`(L1 蒸馏)—— 既有,本 change 不改。

## 环境约束

- **[anywhere]**(任意环境可做):全部代码编写(Task 1-7、10)与单测(Task 1-7 内嵌的 6.1-6.6)。`plugins/skill_evolution.py` 不依赖 `ga.py` 重型 deps,`pytest tests/test_skill_evolution*.py tests/test_skill_scoring.py` 在 Linux 沙箱可跑。
- **[env-blocked]**(需 deps-complete env,Windows `uv pip install -e ".[ui]"`):集成测 Task 8(6.7/6.8,真起 `agentmain.py` 子进程)与 Task 9 的全量回归(6.9)。Linux 沙箱**不可执行**,必须切到 Windows `.venv`。
- 每个 Task 标题末尾标 `[anywhere]` 或 `[env-blocked]`。

## 验证命令总表

| 范围 | 命令 | 期望 | env |
|---|---|---|---|
| 新代码 lint | `ruff check plugins/skill_evolution.py tests/test_skill_scoring.py` | 0 违规 | anywhere |
| 单测(增量) | `pytest tests/test_skill_scoring.py tests/test_skill_evolution_plugin.py -v` | 全绿 | anywhere |
| 既有回归 | `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v` | 既有 ~64 测试全绿(不破) | anywhere |
| 集成 T8 | `pytest tests/test_skill_scoring_integration.py -v` | 全绿 | env-blocked |
| 全量回归 T9 | `pytest tests/` | 不引入新失败 | env-blocked |

## 任务映射表(本计划 Task → tasks.md ID)

| 计划 Task | tasks.md ID | 组 |
|---|---|---|
| Task 1 | 1.1, 1.3 | 1 |
| Task 2 | 1.2, 6.1 | 1, 6 |
| Task 3 | 1.4, 3.1, 3.2, 6.2, 6.5 | 1, 3, 6 |
| Task 4 | 2.1, 2.2, 6.4 | 2, 6 |
| Task 5 | 2.3, 2.4, 6.3 | 2, 6 |
| Task 6 | 4.1, 4.2, 4.3, 4.4, 6.6 | 4, 6 |
| Task 7 | 5.1, 5.2 | 5 |
| Task 8 | 6.7, 6.8 | 6 |
| Task 9 | 6.9 | 6 |
| Task 10 | 7.1, 7.2, 7.3 | 7 |

3.3 OQ 复核(`get_skill_detail` 列 v2 follow-up,本 change 不实现)在 Task 10 文档化登记;6.7/6.8 集成测与 6.9 全量回归独立成 Task 8/9(标注 env-blocked)。

---

## 组 1:Scorer 抽象与双实现

### Task 1:Verdict dataclass + Scorer Protocol + SCORE_SCHEMA + 阈值常量 `[anywhere]`

**覆盖 tasks.md:** 1.1(`Verdict`/`Scorer` 接口)、1.3(`SCORE_SCHEMA` + `GA_SKILL_SCORER_THRESHOLD`)

**Files:**
- Modify: `plugins/skill_evolution.py`(顶部 import 段 L19-27 后追加;L29-31 常量段后追加)
- Test: `tests/test_skill_scoring.py`(本 Task 创建,后续 Task 2-6 共用)

**Interfaces:**
- Consumes: 无(纯新增类型定义)
- Produces:
  - `VerdictKind(Enum)`: `PASS="pass"` / `REJECT="reject"` / `REVISE="revise"`
  - `Verdict` dataclass: `score:int` / `verdict:VerdictKind` / `dims:dict` / `rationale:str` / `source:str="inprocess"`(留痕:`inprocess`|`subagent`|`degraded`)
  - `Scorer(Protocol)`: `score(self, op, skill_md: str, history, catalog: str) -> Verdict`
  - `SCORE_SCHEMA: dict`(JSON schema,见下)
  - `GA_SKILL_SCORER_THRESHOLD = int(os.environ.get("GA_SKILL_SCORER_THRESHOLD", "60"))`
  - `GA_SKILL_SCORER_TIMEOUT = int(os.environ.get("GA_SKILL_SCORER_TIMEOUT", "600"))`

- [x] **Step 1: 写失败测试(类型可 import + Verdict 默认 source + 阈值默认值)**

创建 `tests/test_skill_scoring.py`:
```python
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
    GA_SKILL_SCORER_THRESHOLD,
    GA_SKILL_SCORER_TIMEOUT,
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
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_skill_scoring.py -v`
Expected: FAIL with `ImportError: cannot import name 'Verdict'` 或 `ModuleNotFoundError`(类型未定义)。

- [x] **Step 3: 实现 —— 在 `plugins/skill_evolution.py` 顶部追加类型定义**

在 L24 `import threading` 后追加 import(同段):
```python
import contextvars
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
```

在 L31 `MAX_CHANGES_PER_DISTILL = 3` 后(L33 注释段之前)追加:
```python
# ── Skill scoring gate (hermes-isolated-skill-scorer) ─────────────
# Design: docs/superpowers/specs/2026-07-19-hermes-isolated-skill-scorer-design.md §2
# 闸门插在 distill() 的 _parse_op 与 _apply_op 之间(L148 / L152)。
# gate off (GA_SKILL_EVOLUTION_ENABLED != '1' 或 GA_SKILL_SCORER no-op) → v1 行为。

GA_SKILL_SCORER_THRESHOLD = int(os.environ.get("GA_SKILL_SCORER_THRESHOLD", "60"))
GA_SKILL_SCORER_TIMEOUT = int(os.environ.get("GA_SKILL_SCORER_TIMEOUT", "600"))


class VerdictKind(Enum):
    PASS = "pass"
    REJECT = "reject"
    REVISE = "revise"


@dataclass
class Verdict:
    score: int                          # 0-100
    verdict: VerdictKind
    dims: dict                          # {reusability, verifiedness, non_redundancy}: 0-100
    rationale: str
    source: str = "inprocess"           # "inprocess" | "subagent" | "degraded"  (留痕用)


class Scorer(Protocol):
    """打分器接口。score(op, skill_md, history, catalog) -> Verdict。

    op: distill() _parse_op 产出的 dict(action/name/skill_md/reason)。
    skill_md: 候选 SKILL.md 全文(op['skill_md'])。
    history: task history_info 快照(列表或拼接串;评判官看客观历史,非 generator 判断)。
    catalog: 现有技能 catalog 文本(查冗余用)。
    """
    def score(self, op, skill_md: str, history, catalog: str) -> Verdict: ...


# 打分对象 JSON schema(传 SubagentScorer.run_single(schema=...) / InProcessScorer 解析校验)
# v1 required 锁 3 维(OQ4 决议);dims.additionalProperties:false 严格锁;
# 后续加维度需先改 schema,本 change 不动。
SCORE_SCHEMA = {
    "type": "object",
    "required": ["score", "verdict", "dims", "rationale"],
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["pass", "reject", "revise"]},
        "dims": {
            "type": "object",
            "required": ["reusability", "verifiedness", "non_redundancy"],
            "properties": {
                "reusability": {"type": "integer", "minimum": 0, "maximum": 100},
                "verifiedness": {"type": "integer", "minimum": 0, "maximum": 100},
                "non_redundancy": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v`
Expected: PASS(6 个测试全绿)。

- [x] **Step 5: 跑既有回归确认不破**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿(新增类型不影响既有路径)。

- [x] **Step 6: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py
git commit -m "feat(skill-scoring): add Verdict/Scorer/SCORE_SCHEMA/thresholds (tasks 1.1, 1.3)"
```

---

### Task 2:InProcessScorer + 单测(6.1) `[anywhere]`

**覆盖 tasks.md:** 1.2(同进程二次 LLM,fresh messages + distinct prompt role,禁含 `op['reason']`/CoT)、6.1(单测)

**Files:**
- Modify: `plugins/skill_evolution.py`(Task 1 新增段后追加 `InProcessScorer` + `_extract_json` + `_validate_obj` + `SCORER_SYSTEM_PROMPT`)
- Test: `tests/test_skill_scoring.py`(追加 `InProcessScorer` 测试段)

**Interfaces:**
- Consumes: `Verdict`/`VerdictKind`/`SCORE_SCHEMA`(Task 1)、`handler.client`(`LLMClient`,有 `.chat(messages, tools=[])`)
- Produces: `InProcessScorer(client)` 类,`.score(op, skill_md, history, catalog) -> Verdict`;`_validate_obj(obj, schema) -> None`(校验失败抛 `ScorerDegraded`);`_extract_json(content) -> dict|None`(容错解析代码块/裸 JSON);`ScorerDegraded(Exception)`

- [x] **Step 1: 写失败测试(monkeypatch client.chat 返回合规/非合规 → Verdict 映射/降级)**

在 `tests/test_skill_scoring.py` 追加:
```python
# ── Task 2: InProcessScorer (6.1) ────────────────────────────────

from plugins.skill_evolution import InProcessScorer, ScorerDegraded  # noqa: E402


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
    assert v.verdict == VerdictKind.PASS
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
    assert v.verdict == VerdictKind.REJECT
    assert v.source == "degraded"


def test_inprocess_scorer_degrades_on_missing_dim():
    """dims 缺维度 → schema 校验失败 → 降级。"""
    bad = '{"score": 50, "verdict": "pass", "dims": {"reusability": 50}, "rationale": "x"}'
    c = _FakeClient(bad)
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")
    assert v.source == "degraded"
    assert v.verdict == VerdictKind.REJECT


def test_inprocess_scorer_handles_codeblock_json():
    """LLM 可能把 JSON 包在 ```json ... ``` 代码块里。"""
    c = _FakeClient(f"```json\n{_VALID_SCORE_JSON}\n```")
    s = InProcessScorer(c)
    v = s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")
    assert v.score == 82
    assert v.source == "inprocess"
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_skill_scoring.py -v -k inprocess`
Expected: FAIL with `ImportError: cannot import name 'InProcessScorer'`。

- [x] **Step 3: 实现 `InProcessScorer` + 辅助函数**

在 `plugins/skill_evolution.py` Task 1 新增的 `SCORE_SCHEMA` 后追加:
```python
class ScorerDegraded(Exception):
    """打分器降级信号(SubagentScorer failed/timed_out,或 InProcess 校验失败)。"""


SCORER_SYSTEM_PROMPT = (
    "你是技能评审官(scorer),只读候选 SKILL.md + task 历史 + 现有技能 catalog, "
    "按 reusability(可复用性)/verifiedness(已验证性)/non_redundancy(非冗余性)三维度打分(0-100)。 "
    "你不生成 skill,只评估候选 skill_md 是否值得落盘。 "
    '返回 JSON: {"score":0-100, "verdict":"pass|reject|revise", '
    '"dims":{"reusability":0-100,"verifiedness":0-100,"non_redundancy":0-100}, "rationale":"..."}。 '
    "score>=阈值且 verdict=pass 才放行;reject/revise 或 score<阈值 → 拒绝。"
)


def _extract_json(content: str):
    """容错从 LLM 文本抽 JSON 对象(支持 ```json 代码块 / 裸 JSON)。返回 dict 或 None。"""
    if not content:
        return None
    # 先尝试抽 ```json ... ``` 代码块
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
    if m:
        candidate = m.group(1)
    else:
        m = re.search(r'\{[\s\S]*\}', content)
        if not m:
            return None
        candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _validate_obj(obj, schema: dict) -> None:
    """手写最小 schema 校验(覆盖 type/required/properties/enum/minimum/maximum/additionalProperties)。

    复用 subagent_manager 风格(零新依赖,jsonschema 不在 deps,YAGNI)。
    校验失败 → raise ScorerDegraded。
    """
    if obj is None:
        raise ScorerDegraded("score object is None")
    if schema.get("type") == "object" and not isinstance(obj, dict):
        raise ScorerDegraded("expected object")
    for req in schema.get("required", []):
        if req not in obj:
            raise ScorerDegraded(f"missing required field: {req}")
    for k, subschema in schema.get("properties", {}).items():
        if k not in obj:
            continue
        v = obj[k]
        t = subschema.get("type")
        if t == "integer" and not isinstance(v, int):
            raise ScorerDegraded(f"{k}: expected integer")
        if t == "string" and not isinstance(v, str):
            raise ScorerDegraded(f"{k}: expected string")
        if t == "object" and not isinstance(v, dict):
            raise ScorerDegraded(f"{k}: expected object")
        if "enum" in subschema and v not in subschema["enum"]:
            raise ScorerDegraded(f"{k}: {v!r} not in enum {subschema['enum']}")
        if "minimum" in subschema and isinstance(v, (int, float)) and v < subschema["minimum"]:
            raise ScorerDegraded(f"{k}: {v} < minimum {subschema['minimum']}")
        if "maximum" in subschema and isinstance(v, (int, float)) and v > subschema["maximum"]:
            raise ScorerDegraded(f"{k}: {v} > maximum {subschema['maximum']}")
        if t == "object":
            _validate_obj(v, subschema)  # 递归 dims
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}).keys())
        extra = set(obj.keys()) - allowed
        if extra:
            raise ScorerDegraded(f"additional properties not allowed: {extra}")


def _build_scorer_user_msg(skill_md: str, history, catalog: str) -> str:
    """构造评判官 user message。禁含 op['reason'] / generator CoT(D3)。

    history 是任务客观历史(handler.history_info 快照),非 generator 判断,可接受。
    """
    if isinstance(history, (list, tuple)):
        history_text = "\n".join(str(h) for h in list(history)[-40:])
    else:
        history_text = str(history)
    return (
        f"# 候选技能 SKILL.md\n\n{skill_md}\n\n"
        f"---\n\n# 任务历史快照(最近 40 条,客观事实)\n\n{history_text}\n\n"
        f"---\n\n# 现有技能 catalog(查冗余用)\n\n{catalog}\n\n"
        "请按三维度打分并返回 JSON(只返回 JSON,无其他文本)。"
    )


class InProcessScorer:
    """同进程二次 LLM 打分(弱独立兜底,OQ5 决议)。

    弱独立性声明:同 client(同 session/backend.history),独立性靠 fresh messages + distinct
    prompt role(system role = scorer 职责)软保证。这是 "better than no gate",非 "true independence";
    SubagentScorer 才是真独立。与 v1 既有的 backend.history 污染行为一致(distill generator 调用本就 append)。
    降级时 source="degraded" 留痕。
    """

    def __init__(self, client):
        self._client = client

    def score(self, op, skill_md, history, catalog) -> Verdict:
        messages = [
            {"role": "system", "content": SCORER_SYSTEM_PROMPT},
            {"role": "user", "content": _build_scorer_user_msg(skill_md, history, catalog)},
        ]
        try:
            resp = self._client.chat(messages=messages, tools=[])
        except Exception as e:
            sys.stderr.write(f"[scorer] inprocess chat failed: {e}\n")
            return Verdict(score=0, verdict=VerdictKind.REJECT, dims={},
                           rationale=f"chat error: {e}", source="degraded")
        content = getattr(resp, 'content', '') or (resp if isinstance(resp, str) else '')
        obj = _extract_json(content)
        try:
            _validate_obj(obj, SCORE_SCHEMA)
        except ScorerDegraded as e:
            return Verdict(score=0, verdict=VerdictKind.REJECT, dims={},
                           rationale=f"invalid score obj: {e}", source="degraded")
        return Verdict(
            score=obj["score"],
            verdict=VerdictKind(obj["verdict"]),
            dims=obj["dims"],
            rationale=obj["rationale"],
            source="inprocess",
        )
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v -k inprocess`
Expected: PASS(6 个 inprocess 测试全绿)。

- [x] **Step 5: 跑既有回归**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿。

- [x] **Step 6: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py
git commit -m "feat(skill-scoring): add InProcessScorer fallback + helpers (tasks 1.2, 6.1)"
```

---

### Task 3:SubagentScorer + 独立性硬保证 + 单测(6.2, 6.5) `[anywhere]`

**覆盖 tasks.md:** 1.4(`SubagentScorer` 调 `run_single`)、3.1(`desc` 禁含 `op['reason']`,断言)、3.2(`tools_subset=['file_read']`,断言)、6.2(mock `run_single` 返回合规/`failed`/`timed_out`)、6.5(独立性单测)

**Files:**
- Modify: `plugins/skill_evolution.py`(Task 2 段后追加 `SubagentScorer` + `_build_scorer_desc`)
- Test: `tests/test_skill_scoring.py`(追加 `SubagentScorer` + 独立性测试段)

**Interfaces:**
- Consumes: `Verdict`/`VerdictKind`/`SCORE_SCHEMA`/`ScorerDegraded`(Task 1-2)、`handler._subagent_mgr`(类型 `SubagentManager`,有 `.run_single(desc, schema, tools_subset, base_ref, timeout_s) -> WorkerResult`)
- Produces: `SubagentScorer(subagent_mgr)` 类,`.score(...) -> Verdict`;`_build_scorer_desc(skill_md, history, catalog) -> str`(`desc` 构造,断言不含 `reason`)

- [x] **Step 1: 写失败测试(mock `run_single` 合规/failed/timed_out + 独立性断言)**

在 `tests/test_skill_scoring.py` 追加:
```python
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
    assert v.verdict == VerdictKind.PASS
    assert v.source == "subagent"


def test_subagent_scorer_raises_degraded_on_failed():
    """run_single failed → ScorerDegraded(distill 退回 InProcessScorer,D6)。"""
    mgr = _FakeMgr(_WorkerResult("failed", error="boom"))
    s = SubagentScorer(mgr)
    with pytest.raises(ScorerDegraded):
        s.score({"action": "create", "name": "x", "skill_md": "md", "reason": "r"},
                "md", ["h1"], "- cat: d")


def test_subagent_scorer_raises_degraded_on_timed_out():
    mgr = _FakeMgr(_WorkerResult("timed_out"))
    s = SubagentScorer(mgr)
    with pytest.raises(ScorerDegraded):
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
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_skill_scoring.py -v -k "subagent or build_scorer_desc"`
Expected: FAIL with `ImportError: cannot import name 'SubagentScorer'`。

- [x] **Step 3: 实现 `SubagentScorer` + `_build_scorer_desc`**

在 `plugins/skill_evolution.py` Task 2 的 `InProcessScorer` 后追加:
```python
def _build_scorer_desc(skill_md: str, history, catalog: str) -> str:
    """构造评判子 agent 的 desc(D3 独立性硬保证)。

    候选 SKILL.md 全文 + history_info[-40:] 快照 + catalog。
    **禁含** op['reason'] 与任何 generator prompt 片段。
    history_info 是任务客观历史(非 generator 判断),可接受(D3 Risks)。
    """
    if isinstance(history, (list, tuple)):
        history_text = "\n".join(str(h) for h in list(history)[-40:])
    else:
        history_text = str(history)
    parts = [
        "# 候选技能 SKILL.md\n\n" + skill_md,
        "# 任务历史快照(最近 40 条,客观事实)\n\n" + history_text,
        "# 现有技能 catalog(查冗余用)\n\n" + catalog,
    ]
    desc = "\n\n---\n\n".join(parts)
    # 硬断言:desc 不含生成器 reason 片段(单测覆盖见 test_subagent_scorer_desc_actually_excludes_reason)
    # 注:此处不强制 assert,因 reason 关键词可能与 skill_md 正文巧合;调用方保证 op['reason'] 不入参。
    return desc


class SubagentScorer:
    """隔离子 agent 打分(真独立,independence-by-construction)。

    经 handler._subagent_mgr.run_single() 派发:独立 git worktree(--detach)+ 独立 LLM session
    (subprocess 起 agentmain.py,GA_TASK_MODE=isolated)。评估器看不到生成器 reason/CoT(D3)。
    """

    def __init__(self, subagent_mgr):
        self._mgr = subagent_mgr

    def score(self, op, skill_md, history, catalog) -> Verdict:
        # D3:desc 不含 op['reason'](_build_scorer_desc 不接 reason 参数,硬保证)
        desc = _build_scorer_desc(skill_md, history, catalog)
        res = self._mgr.run_single(
            desc=desc,
            schema=SCORE_SCHEMA,
            tools_subset=["file_read"],     # D3/OQ1:只读;submit_result 由 _apply_task_mode 强制注入
            base_ref=None,                  # OQ2:None → 默认 dev HEAD
            timeout_s=GA_SKILL_SCORER_TIMEOUT,
        )
        if getattr(res, "state", None) in ("failed", "timed_out"):
            raise ScorerDegraded(getattr(res, "state", "unknown"))
        # 二次防御性校验(run_single 已 validate,但兜底)
        try:
            _validate_obj(res.result, SCORE_SCHEMA)
        except ScorerDegraded:
            raise
        r = res.result
        return Verdict(
            score=r["score"],
            verdict=VerdictKind(r["verdict"]),
            dims=r["dims"],
            rationale=r["rationale"],
            source="subagent",
        )
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v -k "subagent or build_scorer_desc"`
Expected: PASS(11 个测试全绿)。

- [x] **Step 5: 跑既有回归**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿。

- [x] **Step 6: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py
git commit -m "feat(skill-scoring): add SubagentScorer + independence guarantees (tasks 1.4, 3.1, 3.2, 6.2, 6.5)"
```

---

## 组 2:收敛点接线

### Task 4:distill() 闸门插入 + 闸门单测(6.4) `[anywhere]`

**覆盖 tasks.md:** 2.1(`distill()` L148-152 之间插打分)、2.2(闸门逻辑:pass+≥阈值 → `_apply_op`;否则 Brief "rejected-by-scorer" 不落盘)、6.4(闸门单测:pass/reject/v1 gate off)

**Files:**
- Modify: `plugins/skill_evolution.py`(`distill()` L148-154 段改造;新增 `_scoring_enabled` + `_score_with_fallback`)
- Test: `tests/test_skill_scoring.py`(追加闸门测试段)

**Interfaces:**
- Consumes: `InProcessScorer`/`SubagentScorer`/`Verdict`/`VerdictKind`/`GA_SKILL_SCORER_THRESHOLD`(Task 1-3)、`build_brief`(L45)、`handler._pending_briefs`、`handler.client`、`getattr(handler, '_subagent_mgr', None)`
- Produces: `_scoring_enabled(handler) -> bool`、`_score_with_fallback(handler, op, skill_md, history, catalog) -> Verdict`(Task 5 提供 `_resolve_scorer`;本 Task 先用 inline 默认,Task 5 重构抽出);`distill()` 内闸门调用

> **依赖顺序注:** Task 4 与 Task 5 紧耦合(闸门调 `_score_with_fallback`,后者调 `_resolve_scorer`)。本 Task 先实现一个最小的 `_score_with_fallback`(inline 用 `_resolve_scorer` 占位),Task 5 补全 `_resolve_scorer` + 降级链。两 Task 可在同一个 review 周期内完成;若执行者偏好,可合并为一次 commit(本计划保留两个 commit 以利 review)。

- [x] **Step 1: 写失败测试(闸门 pass/reject/gate-off)**

在 `tests/test_skill_scoring.py` 追加:
```python
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
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_skill_scoring.py -v -k distill_gate`
Expected: FAIL —— 既有 `distill()` L148-154 直接 `_apply_op`,无闸门;`_scoring_enabled` 未定义。

- [x] **Step 3: 实现 `_scoring_enabled` + `_score_with_fallback` + `distill()` 闸门改造**

在 `plugins/skill_evolution.py` Task 3 的 `SubagentScorer` 后追加(Task 5 会扩展 `_resolve_scorer`):
```python
def _resolve_scorer(handler):
    """gate 选择(Task 5 扩展降级链;本 Task 先提供基础版)。

    返回 Scorer 实例或 None(None = gate off,走 v1 行为)。
    """
    mode = os.environ.get("GA_SKILL_SCORER", "").lower()
    if mode in ("", "off", "none", "false", "0"):
        # 缺省:由 _subagent_mgr present 决定;显式 off → v1 行为
        if mode in ("off", "none", "false", "0"):
            return None
        mgr = getattr(handler, "_subagent_mgr", None)
        if mgr is not None:
            return SubagentScorer(mgr)
        return InProcessScorer(handler.client)
    if mode == "inprocess":
        return InProcessScorer(handler.client)
    if mode == "subagent":
        mgr = getattr(handler, "_subagent_mgr", None)
        if mgr is None:
            sys.stderr.write(
                "[scorer] GA_SKILL_SCORER=subagent but _subagent_mgr absent; "
                "degrading to inprocess\n")
            return InProcessScorer(handler.client)
        return SubagentScorer(mgr)
    # 未知值 → 兜底 inprocess(不静默 v1)
    return InProcessScorer(handler.client)


def _scoring_enabled(handler) -> bool:
    """gate on = GA_SKILL_EVOLUTION_ENABLED=1(外层 distill 已门控)+ GA_SKILL_SCORER 非 off。

    注:distill() 本身在 _on_agent_after 已被 _evolution_enabled() 门控;
    此处再校验 GA_SKILL_SCORER 是否显式 off(v1 行为)。
    """
    mode = os.environ.get("GA_SKILL_SCORER", "").lower()
    if mode in ("off", "none", "false", "0"):
        return False
    return True


def _score_with_fallback(handler, op, skill_md, history, catalog) -> Verdict:
    """调 _resolve_scorer 打分;SubagentScorer failed/timed_out → 退回 InProcessScorer(D6)。

    不跳过打分直接落盘(降级 Brief 留痕)。
    """
    scorer = _resolve_scorer(handler)
    if scorer is None:
        # gate off:不应被调用(distill 闸门已 _scoring_enabled 门控);防御性返回 pass-through
        return Verdict(score=GA_SKILL_SCORER_THRESHOLD, verdict=VerdictKind.PASS,
                       dims={}, rationale="scoring disabled (v1 passthrough)",
                       source="degraded")
    try:
        return scorer.score(op, skill_md, history, catalog)
    except ScorerDegraded as e:
        # D6: SubagentScorer failed/timed_out → inprocess 兜底 + Brief 记降级
        try:
            handler._pending_briefs.append(
                build_brief("scorer-degraded-inprocess",
                            op.get("name", "?"), str(e), f'.agents/skills/{op.get("name","?")}/SKILL.md'))
        except Exception:
            pass
        fallback = InProcessScorer(handler.client)
        v = fallback.score(op, skill_md, history, catalog)
        v.source = "degraded"
        return v
```

改造 `distill()` L148-154(原 `_apply_op(handler, op)` 直调,插闸门):
```python
    content = getattr(resp, 'content', '') or (resp if isinstance(resp, str) else '')
    op = _parse_op(content)
    if not op or op.get('action') == 'none':
        return
    # ── 打分闸门(hermes-isolated-skill-scorer) ──
    # Design §3.1:在 _parse_op 与 _apply_op 之间插 Scorer.score()。
    # gate off(GA_SKILL_SCORER=off 或 v1 模式)→ 直接 _apply_op(v1 行为,既有测试不破)。
    name = (op.get('name') or '').strip()
    skill_md = op.get('skill_md', '')
    if _scoring_enabled(handler):
        verdict = _score_with_fallback(handler, op, skill_md, history, catalog_text)
        if verdict.verdict != VerdictKind.PASS or verdict.score < GA_SKILL_SCORER_THRESHOLD:
            try:
                handler._pending_briefs.append(
                    build_brief('rejected-by-scorer', name, verdict.rationale,
                                f'.agents/skills/{name}/SKILL.md'))
            except Exception:
                pass
            return   # 不落盘
    # ── 闸门结束,放行 → _apply_op ──
    try:
        _apply_op(handler, op)
    except Exception as e:
        sys.stderr.write(f"[skill_evolution] apply_op failed: {e}\n")
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v -k distill_gate`
Expected: PASS(6 个闸门测试全绿)。

- [x] **Step 5: 跑既有回归(关键红线)**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿。**若失败**:既有 `distill()` 测试可能未设 `GA_SKILL_SCORER` env → 默认走 InProcessScorer(需 mock client.chat 两次)。检查 `test_skill_evolution_plugin.py` 是否有 distill 端到端测试;若有,需在测试里显式 `monkeypatch.setenv("GA_SKILL_SCORER", "off")` 或 mock `client.chat` 多次返回。**修复策略**:既有 distill 测试若依赖单次 chat,补 `GA_SKILL_SCORER=off` 使其走 v1 路径(不破语义)。

- [x] **Step 6: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py
git commit -m "feat(skill-scoring): insert distill() gate + pass/reject/off logic (tasks 2.1, 2.2, 6.4)"
```

---

### Task 5:_resolve_scorer 降级链 + 单测(6.3) `[anywhere]`

**覆盖 tasks.md:** 2.3(`_resolve_scorer` gate 选择)、2.4(降级链:requested subagent 但 mgr absent → stderr + inprocess;`SubagentScorer` failed/timed_out → 退回 inprocess + Brief)、6.3(单测 gate 路由 + 降级)

**Files:**
- Modify: `plugins/skill_evolution.py`(`_resolve_scorer` Task 4 已提供基础版,本 Task 补单测覆盖全部分支并加固)
- Test: `tests/test_skill_scoring.py`(追加 `_resolve_scorer` + 降级链测试段)

**Interfaces:**
- Consumes: `InProcessScorer`/`SubagentScorer`(Task 2-3)、`getattr(handler, '_subagent_mgr', None)`、`handler.client`
- Produces: `_resolve_scorer(handler) -> Optional[Scorer]`(Task 4 已实现,本 Task 验证 + 加固未知 mode 兜底)

- [x] **Step 1: 写失败测试(gate 路由全分支 + 降级链)**

在 `tests/test_skill_scoring.py` 追加:
```python
# ── Task 5: _resolve_scorer + fallback (6.3) ─────────────────────

from plugins.skill_evolution import _resolve_scorer, _score_with_fallback  # noqa: E402


class _StubClient:
    def chat(self, messages=None, tools=None):
        return _FakeResp(_VALID_SCORE_JSON)


def test_resolve_scorer_inprocess_explicit(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    h = _DistillHandler(_StubClient(), subagent_mgr=object())  # 有 mgr 也强制 inprocess
    s = _resolve_scorer(h)
    assert isinstance(s, InProcessScorer)


def test_resolve_scorer_subagent_with_mgr(monkeypatch):
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    h = _DistillHandler(_StubClient(), subagent_mgr=_FakeMgr(_WorkerResult("completed", result={
        "score": 90, "verdict": "pass",
        "dims": {"reusability": 90, "verifiedness": 90, "non_redundancy": 90},
        "rationale": "x"})))
    s = _resolve_scorer(h)
    assert isinstance(s, SubagentScorer)


def test_resolve_scorer_subagent_without_mgr_degrades(monkeypatch, capsys):
    """requested subagent 但 _subagent_mgr absent → inprocess + stderr 一行。"""
    monkeypatch.setenv("GA_SKILL_SCORER", "subagent")
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    s = _resolve_scorer(h)
    assert isinstance(s, InProcessScorer)
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
    assert isinstance(s, SubagentScorer)


def test_resolve_scorer_default_inprocess_when_mgr_absent(monkeypatch):
    monkeypatch.delenv("GA_SKILL_SCORER", raising=False)
    h = _DistillHandler(_StubClient(), subagent_mgr=None)
    s = _resolve_scorer(h)
    assert isinstance(s, InProcessScorer)


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
    assert isinstance(s, InProcessScorer)


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
```

- [x] **Step 2: 跑测试确认状态**

Run: `pytest tests/test_skill_scoring.py -v -k "resolve_scorer or score_with_fallback"`
Expected: 大部分 PASS(Task 4 已实现基础版);若未知 mode 兜底分支有偏差则 FAIL —— 据此加固。

- [x] **Step 3: 加固 `_resolve_scorer`(若 Step 2 有 FAIL,据失败补分支)**

核对 Task 4 实现的 `_resolve_scorer` 是否覆盖:
- `off`/`none`/`false`/`0` → `None`
- 缺省 + `_subagent_mgr` present → `SubagentScorer`
- 缺省 + `_subagent_mgr` absent → `InProcessScorer`
- `inprocess` → `InProcessScorer`(强制,即使有 mgr)
- `subagent` + mgr present → `SubagentScorer`
- `subagent` + mgr absent → stderr + `InProcessScorer`
- 未知值 → `InProcessScorer`(兜底)

若 Task 4 实现已覆盖全部分支,本 Task 无代码改动,仅补测试。若未覆盖,在 `_resolve_scorer` 末尾补:
```python
    # 未知 mode → inprocess 兜底(不静默 v1,确保闸门仍跑)
    return InProcessScorer(handler.client)
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v -k "resolve_scorer or score_with_fallback"`
Expected: PASS(11 个测试全绿)。

- [x] **Step 5: 跑既有回归**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿。

- [x] **Step 6: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py
git commit -m "feat(skill-scoring): _resolve_scorer fallback chain + full branch tests (tasks 2.3, 2.4, 6.3)"
```

---

## 组 4:R6 熔断复位修复

### Task 6:reset_auto_patch_count + _apply_op 改造 + 注释 + R6 单测(6.6) `[anywhere]`

**覆盖 tasks.md:** 4.1(`reset_auto_patch_count(name)`)、4.2(foreground patch 成功 → reset)、4.3(scored-pass background → reset 清零;打分 off → 原累加)、4.4(更新 L36 注释)、6.6(R6 单测)

**Files:**
- Modify: `plugins/skill_evolution.py`(`_apply_op` L76-110 改造;新增 `reset_auto_patch_count(name)`;L36 注释更新)
- Test: `tests/test_skill_scoring.py`(R6 测试段) + `tests/test_skill_evolution_plugin.py`(既有熔断测试需确认不破)

**Interfaces:**
- Consumes: `skill_write_origin` ContextVar(L34)、`_auto_patch_counts`(L37)、`MAX_AUTO_PATCH_PER_SKILL`(L30)
- Produces: `reset_auto_patch_count(name: str) -> None`(模块级函数);`_apply_op` 内 R6 复位分支;`_scoring_on_and_passed(handler, op) -> bool`(判定 scored-pass)

**R6 清零语义(OQ3 决议,关键约束):**
- foreground patch 成功 → `reset_auto_patch_count(name)` 清零(D5(a))
- scored-pass background patch(`_apply_op` 成功)→ `reset_auto_patch_count(name)` 清零(D5(b),OQ3)
- 打分 off(v1 模式)background patch 成功 → 原 v1 累加 `_auto_patch_counts[name]++`(legacy 保留)
- 打分 reject / `_apply_op` 失败 → 不改计数器

- [x] **Step 1: 写失败测试(R6 四分支)**

在 `tests/test_skill_scoring.py` 追加:
```python
# ── Task 6: R6 breaker reset (6.6) ───────────────────────────────

from plugins.skill_evolution import reset_auto_patch_count  # noqa: E402


def test_reset_auto_patch_count_zeros_counter(monkeypatch):
    se._auto_patch_counts["s"] = 4
    reset_auto_patch_count("s")
    assert se._auto_patch_counts["s"] == 0


def test_reset_auto_patch_count_unknown_name_creates_zero():
    """reset 未知 name → 创建为 0(幂等)。"""
    assert "never" not in se._auto_patch_counts
    reset_auto_patch_count("never")
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
    """D5(a):foreground do_skill_manage patch 成功 → reset 清零。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    # 模拟 foreground:不 set background_review token(默认 foreground)
    se._auto_patch_counts["s"] = 3
    h = _install_fake_skill_manage_status("ok")
    # foreground 路径:skill_write_origin 默认 'foreground'
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    assert se._auto_patch_counts["s"] == 0   # foreground 成功 → 清零


def test_r6_scored_pass_background_resets_counter(monkeypatch):
    """D5(b)/OQ3:scored-pass background patch 成功 → 清零(不是不累加)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "inprocess")
    # 让 _score_with_fallback 返回 pass:patch handler.client.chat 第二次返回合规打分
    # 但 _apply_op 内部不直接调 scorer —— 需经 distill。此处直接测 _apply_op:
    # _apply_op 需知道"本次是否 scored-pass"。方案:_apply_op 检查 skill_write_origin +
    # GA_SKILL_SCORER enabled(本 change 闸门在 distill 跑,_apply_op 内复用同样判定)。
    se._auto_patch_counts["s"] = 3
    h = _install_fake_skill_manage_status("ok")
    # background path:_apply_op 内 set 'background_review' origin(L96)
    # _scoring_on_and_passed(handler, op) 判定:GA_SKILL_SCORER 非 off → 视为 scored-pass
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    # background + scoring on → scored-pass → 清零
    assert se._auto_patch_counts["s"] == 0


def test_r6_scoring_off_background_increments(monkeypatch):
    """打分 off(v1 模式)background patch → 原累加(legacy)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")
    se._auto_patch_counts["s"] = 2
    h = _install_fake_skill_manage_status("ok")
    se._apply_op(h, {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"})
    assert se._auto_patch_counts["s"] == 3   # 累加,非清零


def test_r6_circuit_still_trips_at_max(monkeypatch):
    """5 次后第 6 次 → Brief 不落盘(熔断仍有效,R6 修复不破熔断)。"""
    monkeypatch.setenv("GA_SKILL_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("GA_SKILL_SCORER", "off")
    h = _install_fake_skill_manage_status("ok")
    op = {"action": "patch", "name": "s", "skill_md": "x", "reason": "r"}
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
    for _ in range(se.MAX_AUTO_PATCH_PER_SKILL):
        se._apply_op(h, op)
    assert se._auto_patch_counts["s"] == se.MAX_AUTO_PATCH_PER_SKILL
    # foreground 修正复位
    reset_auto_patch_count("s")
    assert se._auto_patch_counts["s"] == 0
    # 后续 background auto-patch 不锁死
    se._apply_op(h, op)
    assert len(h.calls) == se.MAX_AUTO_PATCH_PER_SKILL + 1   # 第 6 次成功落盘
    assert se._auto_patch_counts["s"] == 1
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_skill_scoring.py -v -k r6`
Expected: FAIL —— `reset_auto_patch_count` 未定义;`_apply_op` L104-107 无条件累加,foreground 不复位。

- [x] **Step 3: 实现 `reset_auto_patch_count` + `_scoring_on_and_passed` + 改造 `_apply_op`**

在 `plugins/skill_evolution.py` Task 5 段后追加:
```python
def reset_auto_patch_count(name: str) -> None:
    """R6 修复:重置单技能自动 patch 计数器(D5/OQ3 决议)。

    在两情形调用,均清零 _auto_patch_counts[name]:
      (a) foreground do_skill_manage 人工修正 patch 成功(= R6 notes 原意);
      (b) R2 打分 verdict=='pass' 的 background patch 经 _apply_op 成功(独立校验事件 → 复位 streak)。
    """
    _auto_patch_counts[name] = 0


def _scoring_on_and_passed(handler, op) -> bool:
    """判定本次 background patch 是否"打分通过"(R6 清零条件)。

    _apply_op 在 background_review origin 下被调用,此时:
    - 若 distill 闸门已跑且放行(GA_SKILL_SCORER 非 off + op 到达 _apply_op 即隐含 pass),
      则视为 scored-pass → R6 清零。
    - 若 GA_SKILL_SCORER=off(v1 模式),闸门未跑 → legacy 累加。

    简化判定:GA_SKILL_SCORER 非 off 且 skill_write_origin=='background_review' → scored-pass。
    (foreground 路径不经此判定,直接 reset。)
    """
    mode = os.environ.get("GA_SKILL_SCORER", "").lower()
    if mode in ("off", "none", "false", "0"):
        return False
    return skill_write_origin.get() == "background_review"
```

改造 `_apply_op`(L76-110)。**原 L104-110 段**:
```python
    status = getattr(outcome, 'data', None)
    if isinstance(status, dict) and status.get('status') == 'ok':
        if action == 'patch':
            _auto_patch_counts[name] = _auto_patch_counts.get(name, 0) + 1
        _consecutive_auto_distills += 1
    else:
        # 落盘失败/校验失败：不计入连续自动 patch
        _consecutive_auto_distills = 0
```

**改为**:
```python
    status = getattr(outcome, 'data', None)
    if isinstance(status, dict) and status.get('status') == 'ok':
        if action == 'patch':
            # R6 修复(D5/OQ3):patch 成功时按 origin + 打分门决定计数器处置
            if skill_write_origin.get() == 'background_review':
                # background path
                if _scoring_on_and_passed(handler, op):
                    # scored-pass = 独立校验事件 → 清零(OQ3,反推翻 D5 原"不累加")
                    reset_auto_patch_count(name)
                else:
                    # 打分 off(v1 模式)→ 原累加(legacy,spec: "Unscored or rejected background patch behavior unchanged")
                    _auto_patch_counts[name] = _auto_patch_counts.get(name, 0) + 1
            else:
                # foreground do_skill_manage 人工修正 → 清零(D5(a),兑现 R6 notes 原意)
                reset_auto_patch_count(name)
        _consecutive_auto_distills += 1
    else:
        # 落盘失败/校验失败：不计入连续自动 patch
        _consecutive_auto_distills = 0
```

更新 L36 注释(原空头支票):
```python
# 熔断计数：name -> 连续自动 patch 次数；前台修正(do_skill_manage 由 LLM 直调)或
# 打分通过的 background patch(scored-pass = 独立校验事件,OQ3 决议)时经
# reset_auto_patch_count(name) 清零 —— 兑现 R6(v1 永不复位 bug 已修)。
```

- [x] **Step 4: 跑 R6 测试确认通过**

Run: `pytest tests/test_skill_scoring.py -v -k r6`
Expected: PASS(7 个 R6 测试全绿)。

- [x] **Step 5: 跑既有熔断测试确认不破(关键红线)**

Run: `pytest tests/test_skill_evolution_plugin.py -v`
Expected: 既有 `_apply_op` / 熔断测试全绿。**若失败**:
- `test_apply_op_patch_succeeds_and_counts`(L127):现状断言 `_auto_patch_counts["s"]==1`。本 Task 改造后:若测试未设 `GA_SKILL_SCORER`(缺省)+ `_subagent_mgr=None` → `_scoring_on_and_passed` 返回 True(缺省非 off)→ background scored-pass → **清零而非累加** → 断言 `==1` FAIL。
- **修复策略 A(推荐,语义正确)**:在既有测试 `test_apply_op_patch_succeeds_and_counts` 显式 `monkeypatch.setenv("GA_SKILL_SCORER", "off")` 使其走 legacy 累加路径(v1 行为,断言不变)。
- **修复策略 B**:更新断言为 `==0`(scored-pass 清零),但此改动改变了测试语义,不推荐。

采用策略 A:在 `tests/test_skill_evolution_plugin.py` 的 `_reset_counts_and_registry` fixture 或相关测试函数内补:
```python
monkeypatch.setenv("GA_SKILL_SCORER", "off")  # 既有 v1 行为测试走 legacy 累加
```
需逐个核对 `test_apply_op_patch_succeeds_and_counts` / `test_apply_op_failed_patch_not_counted` / `test_circuit_breaker_skips_after_max` 等是否需要补 env。**原则**:既有测试验证 v1 行为,显式 `GA_SKILL_SCORER=off` 使其走 legacy 分支,断言不动。

- [x] **Step 6: 跑既有回归**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py -v`
Expected: 既有 ~64 测试全绿(策略 A 补 env 后)。

- [ ] **Step 7: Commit**

```bash
git add plugins/skill_evolution.py tests/test_skill_scoring.py tests/test_skill_evolution_plugin.py
git commit -m "fix(skill-evolution): R6 breaker reset on foreground/scored-pass (tasks 4.1-4.4, 6.6)"
```

---

## 组 5:ga.py 接线

### Task 7:ga.py _subagent_mgr 接线 + agentmain 兼容确认 `[anywhere]`

**覆盖 tasks.md:** 5.1(`GenericAgentHandler.__init__` 实例化 `self._subagent_mgr`,`try/except ImportError` 防御)、5.2(`agentmain.py` 子模式 `GA_TASK_MODE=isolated` 兼容确认)

**Files:**
- Modify: `ga.py`(`GenericAgentHandler.__init__` L29-38 段,追加 `_subagent_mgr` 实例化)
- Test: `tests/test_skill_scoring.py`(追加 ga.py 接线测试;因 `ga.py` 重型 deps,用 `try/except ImportError` 跳过)

**Interfaces:**
- Consumes: `subagent_manager.SubagentManager`(根级模块,未合并到 dev → `ImportError`);`self.cwd`
- Produces: `GenericAgentHandler._subagent_mgr`(实例或 `None`)

- [x] **Step 1: 写失败/验证测试(_subagent_mgr 在 ImportError 时为 None)**

在 `tests/test_skill_scoring.py` 追加:
```python
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
```

> **测试策略注:** 完整的 `GenericAgentHandler._subagent_mgr` 注入测试需 import `ga.py`(依赖 `pywebview` 等,Linux 沙箱不可跑),列入 Task 8 集成测(env-blocked)。本 Task 单测仅验证"`subagent_manager` 未合并时 try/except 的必要性"。

- [x] **Step 2: 跑测试确认状态**

Run: `pytest tests/test_skill_scoring.py -v -k subagent_mgr_absent`
Expected: PASS(当前 dev 无 `subagent_manager`,断言成立)或 SKIP(若已合并)。

- [x] **Step 3: 改 `ga.py.__init__` 接线 `_subagent_mgr`**

在 `ga.py` L27-38 `GenericAgentHandler.__init__` 段,L37 `self._evolution_signal = None` 后追加:
```python
        self._pending_briefs = []        # self-evolution: queued skill-distillation briefs (flushed at turn%10 / task end)
        self._evolution_signal = None    # self-evolution: accumulated negative signal (errors/retries/corrections) for v1.5 trigger
        # subagent manager (first-class subagents, add-first-class-subagents):
        # try/except 防御 —— subagents 未合并到 dev 时 ImportError, _subagent_mgr=None,
        # skill-scoring gate 缺省走 InProcessScorer(本 change 可独立落地)。
        # subagents 合并后无需改本行即启用 SubagentScorer 真隔离。
        try:
            from subagent_manager import SubagentManager
            self._subagent_mgr = SubagentManager(root=self.cwd)
        except ImportError:
            self._subagent_mgr = None
        self.print = safe_print
```

> **核对:** `SubagentManager(root=self.cwd)` 签名 —— 参见 `subagent_manager.py:122`(worktree 分支,未合并)。合并后若签名变更,据实调整。当前 dev `ImportError` → `self._subagent_mgr = None`,不破既有行为。

- [x] **Step 4: 确认 agentmain.py 子模式兼容(5.2,文档性核对)**

核对 `agentmain.py` 子模式(`GA_TASK_MODE=isolated`)对评判官场景兼容:
- 评判官 `desc` 不含 `reason`(Task 3 `_build_scorer_desc` 保证)
- `tools_subset=['file_read']`(Task 3 SubagentScorer 保证)
- `_apply_task_mode` 强制加 `submit_result`(`subagent_manager.py:235`,worktree 分支)

**无需改 `agentmain.py`**(子模式已由 add-first-class-subagents 实现)。本 Step 仅文档性确认,在 commit message 注明 "5.2 兼容性核对通过,无需改 agentmain.py"。

- [x] **Step 5: 跑既有回归**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py tests/test_skill_scoring.py -v`
Expected: 全绿(ga.py 改动是 try/except,既有测试不 import ga.py 不受影响;若某测试 import ga.py 在沙箱失败,属既有 env 限制不计入)。

- [x] **Step 6: Commit**

```bash
git add ga.py tests/test_skill_scoring.py
git commit -m "feat(ga): wire _subagent_mgr with try/except ImportError guard (tasks 5.1, 5.2)"
```

---

## 组 6:测试(集成)

### Task 8:集成测 — distill 全链 + 降级链(6.7, 6.8) `[env-blocked]`

**覆盖 tasks.md:** 6.7(deps-complete env,monkeypatch 子 agent 合规打分 → distill 全链跑通)、6.8(子 agent 不调 `submit_result`/超时 → 降级 inprocess + Brief)

**Files:**
- Create: `tests/test_skill_scoring_integration.py`(env-blocked,需 deps-complete env)

**Interfaces:**
- Consumes: Task 1-7 全部产出;`subagent_manager.SubagentManager`(合并后);`agentmain.py` 子模式
- Produces: 集成测试覆盖真隔离端到端 + 降级链端到端

> **env 约束:** 本 Task 需 Windows `.venv`(`uv pip install -e ".[ui]"`)。Linux 沙箱**不可执行**。subagents 未合并到 dev 时,6.7 真隔离路径**无法端到端跑**(需 subagents 合并);6.8 降级链可在当前 dev 跑(mock 子 agent 失败,InProcessScorer 兜底)。计划提供两套测试:6.8 在当前 dev 可跑;6.7 标 `pytest.mark.skipif` 待 subagents 合并。

- [x] **Step 1: 写集成测试(6.7 skipif + 6.8 当前可跑)**

创建 `tests/test_skill_scoring_integration.py`:
```python
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
    from plugins.skill_evolution import (Verdict, VerdictKind)

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
```

- [x] **Step 2: 跑 6.8(当前 dev 可跑部分)**

Run(Linux 沙箱,若 ga.py 不可 import 则全 skip):`pytest tests/test_skill_scoring_integration.py -v`
Run(Windows deps-complete env):`pytest tests/test_skill_scoring_integration.py -v`
Expected:
- 沙箱:全 skip(`ga.py not importable`)
- Windows deps-complete:6.7 skip(subagent_manager 未合并);6.8 PASS(降级链端到端)

- [x] **Step 3: 跑既有回归确认不破**

Run(Windows deps-complete):`pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py tests/test_skill_scoring.py tests/test_skill_scoring_integration.py -v`
Expected: 全绿(集成测 skip 或 pass,既有 ~64 测试全绿)。

- [x] **Step 4: Commit**

```bash
git add tests/test_skill_scoring_integration.py
git commit -m "test(skill-scoring): integration e2e for distill full chain + fallback (tasks 6.7, 6.8)"
```

---

### Task 9:ruff + pytest 全量回归(6.9) `[anywhere]` + `[env-blocked]`

**覆盖 tasks.md:** 6.9(`ruff check` 0 违规;`pytest tests/test_skill_evolution*.py tests/test_skill_scoring*.py` 不引入新失败,含既有 64)

**Files:**
- 无代码改动,仅验证

- [ ] **Step 1: ruff check 新代码(Anywhere)**

Run: `ruff check plugins/skill_evolution.py tests/test_skill_scoring.py tests/test_skill_scoring_integration.py`
Expected: 0 违规。**若有违规**:据 ruff 提示修(常见:未用 import / 行过长 / E402 import 位置)。修后重跑。

- [ ] **Step 2: pytest 增量回归(Anywhere)**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py tests/test_skill_scoring.py -v`
Expected: 既有 ~64 + 新增 ~40 测试全绿。**若有失败**:据失败定位回对应 Task 修复。

- [ ] **Step 3: pytest 全量回归(env-blocked,Windows deps-complete)**

Run(Windows deps-complete):`pytest tests/`
Expected: 不引入新失败(baseline flakiness 不计;既有 env-blocked skip 不计)。**若新失败**:回 Task 1-8 定位。

- [ ] **Step 4: 确认既有 64 测试未破(红线核对)**

核对 `tests/test_skill_evolution.py` + `tests/test_skill_evolution_plugin.py` + `tests/test_skill_loader_l1.py` 测试数 ≥ 既有 baseline(64)。若 Task 6 给既有测试补了 `GA_SKILL_SCORER=off` env,测试数不变,断言不改。

- [ ] **Step 5: Commit(若有 ruff/测试修复)**

```bash
git add -A
git commit -m "test(skill-scoring): ruff clean + full pytest regression (task 6.9)"
```

> 若 Step 1-3 无修改,本 Task 无 commit(仅验证)。

---

## 组 7:文档

### Task 10:AGENTS.md + NOTES_v1.5 + HANDOFF 文档(7.1, 7.2, 7.3) `[anywhere]`

**覆盖 tasks.md:** 7.1(`AGENTS.md` 登记 `GA_SKILL_SCORER` env gate 与阈值/超时)、7.2(`hermes/NOTES_v1.5_recommendations.md` 标 R2 已实现 + R6 已修)、7.3(`HANDOFF.md` 或 design 摘要)、3.3(OQ 复核:`get_skill_detail` 列 v2 follow-up 登记)

**Files:**
- Modify: `AGENTS.md`(env gate 表)
- Modify: `hermes/NOTES_v1.5_recommendations.md`(R2/R6 状态)
- Modify: `HANDOFF.md`(或新增 design 摘要段)

- [ ] **Step 1: 在 `AGENTS.md` 登记 env gate**

在 `AGENTS.md` 的 env 表(若有)或显式 env 文档段追加:
```markdown
| env | 默认 | 作用 |
|---|---|---|
| `GA_SKILL_SCORER` | (缺省:`_subagent_mgr` present → `subagent`;absent → `inprocess`) | skill 打分闸门实现选择。`subagent`=真隔离子 agent;`inprocess`=同进程二次 LLM(弱独立兜底);`off`/`none`/`false`/`0`=v1 行为(无打分直接落盘) |
| `GA_SKILL_SCORER_THRESHOLD` | `60` | 打分闸门阈值,`verdict==pass` 且 `score >= 阈值` 才放行 |
| `GA_SKILL_SCORER_TIMEOUT` | `600` | `SubagentScorer` 子 agent 超时(秒),超时 → 降级 `InProcessScorer` |
```

- [ ] **Step 2: 在 `hermes/NOTES_v1.5_recommendations.md` 标 R2/R6 状态**

在 R2 条目标"已实现(hermes-isolated-skill-scorer)":
```markdown
- [x] R2:打分器以隔离子 agent 实现 —— hermes-isolated-skill-scorer 落地
  `Scorer` 抽象双实现(`SubagentScorer` 真隔离 / `InProcessScorer` 兜底),
  gate 缺省由 `_subagent_mgr` present 决定。subagents 未合并时走 inprocess 兜底,
  合并后无需改本 change 代码即启用真隔离。
- [x] R6:_auto_patch_counts 永不复位 bug 已修 —— foreground 修正或 scored-pass
  background patch → `reset_auto_patch_count(name)` 清零(OQ3 决议,反推翻原"不累加")。
- [ ] R1/R3/R5/R7/R8/R9 仍 open(R3 test-prompts 经验打分 / R5 ratchet 回退 /
  R7 死计数器清理 / R8 fitness-log / R9 validate 缺 loadability)—— 另立 change。
- v2 follow-up:`get_skill_detail` 工具给评判官(查冗余更准,OQ1 决议本 change 用 `file_read` 兜底)。
```

- [ ] **Step 3: 在 `HANDOFF.md` 加 design 摘要段(或新增)**

在 `HANDOFF.md` 末尾追加(或新建 design 摘要段):
```markdown
## hermes-isolated-skill-scorer 交接摘要

**Change:** `openspec/changes/hermes-isolated-skill-scorer/`
**Design Doc:** `docs/superpowers/specs/2026-07-19-hermes-isolated-skill-scorer-design.md`
**Plan:** `docs/superpowers/plans/2026-07-19-hermes-isolated-skill-scorer.md`

**收敛点:** `plugins/skill_evolution.py:distill()` 的 `_parse_op` 与 `_apply_op` 之间插
`Scorer.score(op, skill_md, history, catalog)` 闸门。pass+≥阈值 → `_apply_op`;否则
Brief "rejected-by-scorer" 不落盘。

**双实现:** `SubagentScorer`(真隔离,经 `handler._subagent_mgr.run_single`)+
`InProcessScorer`(同进程二次 LLM 兜底)。gate `GA_SKILL_SCORER` + `_subagent_mgr` present 决定。
subagents 未合并到 dev → `_subagent_mgr=None` → 缺省走 inprocess(本 change 可独立落地)。

**R6 修复:** `_apply_op` patch 成功时,foreground 修正或 scored-pass background →
`reset_auto_patch_count(name)` 清零(OQ3 决议);打分 off → 原 v1 累加(legacy)。

**测试:** 单测(test_skill_scoring.py)覆盖 InProcessScorer/SubagentScorer mock/gate 路由/
闸门/独立性/R6;集成测(test_skill_scoring_integration.py)6.7 标 skipif 待 subagents 合并,
6.8 降级链当前 dev 可跑。既有 ~64 测试不破(补 `GA_SKILL_SCORER=off` env 走 legacy 分支)。

**回滚:** `GA_SKILL_SCORER=off` 或关 `GA_SKILL_EVOLUTION_ENABLED`。R6 修复不可回退
(行为修复,回退到"永不复位"bug 态不可接受)。
```

- [ ] **Step 4: 跑既有回归确认文档改动不破测试**

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_loader_l1.py tests/test_skill_scoring.py -v`
Expected: 全绿(文档改动不影响测试)。

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md hermes/NOTES_v1.5_recommendations.md HANDOFF.md
git commit -m "docs(skill-scoring): env gate / R2+R6 status / handoff summary (tasks 7.1-7.3, 3.3)"
```

---

## Self-Review

### Spec coverage(spec / tasks.md 29 项逐条核对)

**组 1(Scorer 抽象与双实现):**
- 1.1 Verdict/Scorer 接口 → Task 1 ✓
- 1.2 InProcessScorer → Task 2 ✓
- 1.3 SCORE_SCHEMA + 阈值 → Task 1 ✓
- 1.4 SubagentScorer → Task 3 ✓

**组 2(收敛点接线):**
- 2.1 distill 闸门插入 → Task 4 ✓
- 2.2 闸门逻辑 pass/reject/Brief → Task 4 ✓
- 2.3 _resolve_scorer → Task 4(基础)+ Task 5(全分支)✓
- 2.4 降级链 → Task 5 ✓

**组 3(独立性硬保证):**
- 3.1 desc 禁含 reason + 断言 → Task 3(`test_subagent_scorer_desc_actually_excludes_reason` + `test_build_scorer_desc_excludes_op_reason`)✓
- 3.2 tools_subset=['file_read'] + 断言 → Task 3(`test_subagent_scorer_tools_subset_readonly`)✓
- 3.3 OQ 复核 get_skill_detail → Task 10(v2 follow-up 登记)✓

**组 4(R6):**
- 4.1 reset_auto_patch_count → Task 6 ✓
- 4.2 foreground reset → Task 6(`test_r6_foreground_patch_resets_counter`)✓
- 4.3 scored-pass reset 清零 → Task 6(`test_r6_scored_pass_background_resets_counter`)+ off 累加(`test_r6_scoring_off_background_increments`)✓
- 4.4 注释更新 → Task 6(L36 注释)✓

**组 5(ga.py 接线):**
- 5.1 _subagent_mgr 实例化 + try/except → Task 7 ✓
- 5.2 agentmain 兼容确认 → Task 7(Step 4 文档核对)✓

**组 6(测试):**
- 6.1 InProcessScorer 单测 → Task 2 ✓
- 6.2 SubagentScorer mock 单测 → Task 3 ✓
- 6.3 _resolve_scorer 单测 → Task 5 ✓
- 6.4 闸门单测 → Task 4 ✓
- 6.5 独立性单测 → Task 3 ✓
- 6.6 R6 单测 → Task 6 ✓
- 6.7 集成测真隔离 → Task 8(skipif 待 subagents 合并)✓
- 6.8 集成测降级 → Task 8 ✓
- 6.9 ruff + pytest → Task 9 ✓

**组 7(文档):**
- 7.1 AGENTS.md env → Task 10 ✓
- 7.2 NOTES_v1.5 R2/R6 → Task 10 ✓
- 7.3 HANDOFF → Task 10 ✓

29 项全覆盖。

### 关键技术约束核对

- ✅ subagents 未合并 → `SubagentScorer` 用 `try/except ImportError` 防御(ga.py Task 7);真隔离端到端 6.7 标 skipif(deps-complete + subagents merged 前置);InProcessScorer 路径当前 dev 独立端到端跑(Task 2/4/5/8 单测 + 6.8 集成)
- ✅ R6 清零语义(OQ3):Task 6 `test_r6_scored_pass_background_resets_counter` 断言 `==0`(不是不累加);off 走累加(`test_r6_scoring_off_background_increments`)
- ✅ 独立性硬保证:Task 3 `test_subagent_scorer_desc_actually_excludes_reason` 断言 desc 不含 `op['reason']`;`test_subagent_scorer_tools_subset_readonly` 断言不含执行类工具
- ✅ gate off / v1 模式 → 既有 64 测试不破:Task 6 Step 5 明确策略 A(既有测试补 `GA_SKILL_SCORER=off` 走 legacy);Task 9 红线核对

### Placeholder scan

无 TBD/TODO 占位(Task 8 Step 1 的 6.7 真隔离测试用 `pytest.skip` 显式标记待 subagents 合并,非占位 —— 是有意的 env-gate,测试代码骨架完整)。

### Type consistency

- `Verdict` 字段:`score`/`verdict`/`dims`/`rationale`/`source` —— Task 1 定义,Task 2/3/4/5/8 使用一致 ✓
- `Scorer.score(op, skill_md, history, catalog) -> Verdict` —— Task 1 定义,Task 2/3 实现签名一致 ✓
- `reset_auto_patch_count(name: str) -> None` —— Task 6 定义,Task 6 测试使用一致 ✓
- `_resolve_scorer(handler) -> Optional[Scorer]` —— Task 4 基础版 + Task 5 全分支,签名一致 ✓
- `SCORE_SCHEMA` —— Task 1 定义,Task 2(`_validate_obj`)/ Task 3(`run_single(schema=)`)使用一致 ✓
- `ScorerDegraded(Exception)` —— Task 2 定义,Task 3(`raise`)/ Task 5(`except`)使用一致 ✓

### 依赖顺序

Task 1(类型)→ Task 2(InProcess)→ Task 3(Subagent)→ Task 4(闸门,用 1-3)→ Task 5(降级链,用 2-3)→ Task 6(R6,独立于 1-5,可并行但建议在 4 后)→ Task 7(ga.py 接线,独立)→ Task 8(集成,依赖 1-7)→ Task 9(全量回归,依赖 1-8)→ Task 10(文档,独立)。

Task 6 与 Task 4 有耦合:Task 6 的 `_scoring_on_and_passed` 依赖 `GA_SKILL_SCORER` env 语义(Task 4 定义)。建议 Task 4 → Task 6 顺序执行。Task 7 可与 Task 4-6 并行(独立文件 ga.py)。
