---
comet_change: hermes-isolated-skill-scorer
role: technical-design
canonical_spec: openspec
---

# hermes-isolated-skill-scorer 技术设计

> 本 Design Doc 是 open 阶段 `design.md`(高层框架)的**深度技术细化**,不替代 proposal/spec。7 个已确认决策(D1-D7)承自 `design.md` 勿推翻;本 Doc 细化实现口径、技术风险、测试策略、边界条件,并决议 6 个 Open Questions(§6)。

## 1. 实现总览

收敛点:`distill()`(`plugins/skill_evolution.py:148` `_parse_op` 与 `:152` `_apply_op` 之间)插 `Scorer.score(op, skill_md, history, catalog)` 闸门。闸门 `verdict=='pass'` 且 `score>=GA_SKILL_SCORER_THRESHOLD`(默认 60)→ `_apply_op`(落盘);否则入 `handler._pending_briefs` 标 "candidate failed review",不落盘。

```
distill() daemon 线程:
  client.chat → _parse_op(op)  [:148]
        │
        ▼
  _resolve_scorer(handler) ── GA_SKILL_SCORER + _subagent_mgr present
        │
        ▼
  scorer.score(op, skill_md, history, catalog) → Verdict
        │
   ┌────┴────┐
   ▼         ▼
 pass+≥阈值  else
   │         └→ _pending_briefs("candidate failed review"),不落盘
   ▼
 _apply_op(handler, op)  [:152]  → 写 SKILL.md(.prev backup) + validate_skill
   │
   ▼ (R6: 若 background+scored-pass → reset_auto_patch_count;若 foreground → reset)
 完成
```

foreground 路径(`do_skill_manage` 由 LLM 直接调)不经闸门(spec: "Foreground write path bypasses scoring gate"),保留既有 read-only boundary。

## 2. Scorer 抽象与双实现

### 2.1 Verdict dataclass + Scorer 接口

```python
class VerdictKind(Enum):
    PASS = "pass"
    REJECT = "reject"
    REVISE = "revise"

@dataclass
class Verdict:
    score: int            # 0-100
    verdict: VerdictKind
    dims: dict           # {reusability, verifiedness, non_redundancy}: 0-100
    rationale: str
    source: str = "inprocess"   # "inprocess" | "subagent" | "degraded"  (留痕用)

class Scorer(Protocol):
    def score(self, op, skill_md: str, history, catalog: str) -> Verdict: ...
```

`SCORE_SCHEMA`(JSON schema,传 `run_single(schema=...)` / inprocess 解析校验):

```json
{
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
        "non_redundancy": {"type": "integer", "minimum": 0, "maximum": 100}
      },
      "additionalProperties": false
    },
    "rationale": {"type": "string"}
  },
  "additionalProperties": false
}
```

`dims` 是可扩展 dict(v1.5/v2 加维度不破 schema),但 v1 `required` 锁 3 维(OQ4 决议,§6)。

阈值常量:

```python
GA_SKILL_SCORER_THRESHOLD = int(os.environ.get("GA_SKILL_SCORER_THRESHOLD", "60"))
GA_SKILL_SCORER_TIMEOUT = int(os.environ.get("GA_SKILL_SCORER_TIMEOUT", "600"))
```

### 2.2 InProcessScorer(兜底,弱独立)

```python
class InProcessScorer:
    def __init__(self, client):
        self._client = client

    def score(self, op, skill_md, history, catalog) -> Verdict:
        messages = []                      # fresh messages list(不复用 generator 的 messages)
        messages.append({"role": "system", "content": SCORER_SYSTEM_PROMPT})
        messages.append({"role": "user", "content": _build_scorer_user_msg(skill_md, history, catalog)})
        content = self._client.chat(messages, tools=[])   # tools=[] 强制纯文本打分
        obj = _extract_json(content)        # 容错解析(代码块/裸 JSON)
        _validate_obj(obj, SCORE_SCHEMA)    # 结构校验失败 → Verdict(score=0, REJECT, source="degraded")
        return Verdict(score=obj["score"], verdict=VerdictKind(obj["verdict"]),
                       dims=obj["dims"], rationale=obj["rationale"], source="inprocess")
```

**弱独立性声明(OQ5 决议)**:同 `client`(同 session/backend.history,与 generator 调用同 session),独立性靠 **fresh messages + distinct prompt role**(system role = scorer 职责)软保证。这是 "better than no gate",非 "true independence";`SubagentScorer` 才是真独立。与 v1 既有的 backend.history 污染行为一致(distill generator 调用本就 append)。降级时 `source="degraded"` 留痕。

`SCORER_SYSTEM_PROMPT` 明确评判官职责:"你是技能评审官,只读候选 SKILL.md + task 历史 + catalog,按 reusability/verifiedness/non_redundancy 打分;你不生成 skill,只评估。"

### 2.3 SubagentScorer(真隔离)

```python
class SubagentScorer:
    def __init__(self, subagent_mgr):
        self._mgr = subagent_mgr

    def score(self, op, skill_md, history, catalog) -> Verdict:
        desc = _build_scorer_desc(skill_md, history, catalog)   # 见 §3 独立性硬保证
        res = self._mgr.run_single(
            desc=desc,
            schema=SCORE_SCHEMA,
            tools_subset=["file_read"],     # D3:只读 + 强制 submit_result(_apply_task_mode 注入)
            base_ref=None,                  # OQ2:None → 默认 dev HEAD
            timeout_s=GA_SKILL_SCORER_TIMEOUT,
        )
        if res.state in ("failed", "timed_out"):
            raise ScorerDegraded(res.state)   # distill 退回 InProcessScorer(D6)
        _validate_obj(res.result, SCORE_SCHEMA)
        v = Verdict(score=res.result["score"], verdict=VerdictKind(res.result["verdict"]),
                    dims=res.result["dims"], rationale=res.result["rationale"], source="subagent")
        return v
```

`run_single` 返回 `WorkerResult(uuid, state, result, error, worktree_path)`。`state` 为 `completed`/`failed`/`timed_out`。`validate(obj, schema)`(`subagent_manager.py:175`)已做 schema 校验,二次 `_validate_obj` 防御性兜底。

### 2.4 _resolve_scorer gate + 降级链

```python
def _resolve_scorer(handler) -> Optional[Scorer]:
    mode = os.environ.get("GA_SKILL_SCORER", "").lower()
    mgr = getattr(handler, "_subagent_mgr", None)
    if mode == "inprocess":
        return InProcessScorer(handler.client)
    if mode == "subagent":
        if mgr is None:
            sys.stderr.write("[scorer] GA_SKILL_SCORER=subagent but _subagent_mgr absent; degrading to inprocess\n")
            return InProcessScorer(handler.client)
        return SubagentScorer(mgr)
    # 缺省:由 _subagent_mgr present 决定
    if mgr is not None:
        return SubagentScorer(mgr)
    return InProcessScorer(handler.client)

def _score_with_fallback(handler, op, skill_md, history, catalog) -> Verdict:
    scorer = _resolve_scorer(handler)
    try:
        return scorer.score(op, skill_md, history, catalog)
    except ScorerDegraded as e:
        # D6: SubagentScorer failed/timed_out → inprocess 兜底 + Brief 记降级
        handler._pending_briefs.append(build_brief("scorer-degraded-inprocess", e.state, ...))
        fallback = InProcessScorer(handler.client)
        v = fallback.score(op, skill_md, history, catalog)
        v.source = "degraded"
        return v
```

gate off:`GA_SKILL_EVOLUTION_ENABLED != '1'` 或 `GA_SKILL_SCORER` 解析为 no-op → `distill()` 跳闸门,v1 行为(spec: "Scorer disabled reverts to v1 behavior")。

## 3. 收敛点接线 + 独立性硬保证

### 3.1 distill 闸门插入

```python
# distill() L148-152 之间:
op = _parse_op(content)                                    # [:148]
if _scoring_enabled(handler):
    verdict = _score_with_fallback(handler, op, skill_md, history_info, catalog_text)
    if verdict.verdict != VerdictKind.PASS or verdict.score < GA_SKILL_SCORER_THRESHOLD:
        handler._pending_briefs.append(
            build_brief("rejected-by-scorer", name, verdict.rationale, path))
        return   # 不落盘
_apply_op(handler, op)                                     # [:152]
```

### 3.2 独立性硬保证(SubagentScorer 的 desc 构造,D3)

```python
def _build_scorer_desc(skill_md: str, history_info: list, catalog: str) -> str:
    # 候选 SKILL.md 全文 + history_info[-40:] 快照 + catalog
    parts = [
        "# 候选技能 SKILL.md\n\n" + skill_md,
        "# 任务历史快照(最近 40 条)\n\n" + "\n".join(history_info[-40:]),
        "# 现有技能 catalog(查冗余用)\n\n" + catalog,
    ]
    desc = "\n\n---\n\n".join(parts)
    # 硬断言:desc 不含生成器 reason / CoT 片段
    assert "reason" not in desc.lower() or _reason_keyword_safe(desc)   # 单测覆盖见 §8.5
    return desc
```

**禁含** `op['reason']` 与任何 generator prompt 片段。`history_info` 是任务客观历史(非 generator 判断),可接受(D3 Risks)。

### 3.3 tools 限定(D3 + OQ1)

`tools_subset=['file_read']`(读候选/查 catalog)。`_apply_task_mode`(`subagent_manager.py:235`)过滤到 `GA_TASK_TOOLS` 白名单 + 强制 `submit_result`(终末提交打分对象)。**不给** `code_run`/`file_write`/`web_scan`/`file_patch`。

## 4. R6 熔断复位(D5 + OQ3 决议)

```python
def reset_auto_patch_count(self, name: str):
    self._auto_patch_counts[name] = 0      # L37 dict
```

`_apply_op` 改动(L76-110):

```python
# patch 成功后(L104-107 现状是累加):
if skill_write_origin == "background_review":
    if _scoring_on_and_passed(handler, op):
        reset_auto_patch_count(handler, name)      # OQ3: 清零(独立校验事件)
    else:
        handler._auto_patch_counts[name] = handler._auto_patch_counts.get(name, 0) + 1   # v1 累加(legacy)
else:  # foreground do_skill_manage
    reset_auto_patch_count(handler, name)          # D5(a): 前台人工修正复位
```

`_scoring_on_and_passed`:`GA_SKILL_SCORER` 解析非 no-op 且闸门 `verdict=='pass'` 通过。打分 off → 走 else 累加分支(legacy 保留,spec: "Unscored or rejected background patch behavior unchanged")。

L36 注释更新:"前台修正或打分通过时重置 _auto_patch_counts(独立校验事件),兑现 R6"。

**OQ3 决议(反推翻 D5 原"不累加")**:scored-pass = 独立校验事件 → **清零**(与 foreground 同处置),非"仅不累加"。理由:breaker 语义是"连续**未独立校验**的自动 patch",scored-pass 是独立校验,应复位 streak;"不累加"保留历史 streak、永不因校验复位,语义偏弱。代价:打分器误判通过会让历史 churn 信号丢失——缓解:`rationale` 留痕 + R5 ratchet(v2)基于 R8 fitness-log before/after 比较。已回写 delta spec(§10)。

## 5. ga.py 接线

```python
# GenericAgentHandler.__init__:
try:
    from subagent_manager import SubagentManager   # subagents §1-4(未合并到 dev 时 ImportError)
    self._subagent_mgr = SubagentManager(root=self.cwd)
except ImportError:
    self._subagent_mgr = None
```

`agentmain.py` 子模式(`GA_TASK_MODE=isolated`)对评判官场景兼容:`desc` 不含 `reason`,`tools_subset=['file_read']`,`_apply_task_mode` 强制加 `submit_result`(spec §5.2 / D1)。

## 6. 6 个 Open Questions 决议

| OQ | 决议 | 理由 |
|----|------|------|
| **1 scorer tools** | 仅 `file_read`,不加 `get_skill_detail` | catalog 已在 `desc`(distill L122),冗余检查靠它 + 按需 `file_read`;`get_skill_detail` 列 v2 follow-up(YAGNI) |
| **2 base_ref** | `None` → 默认 dev HEAD | scorer 子 agent 不需 SubagentManager,候选 SKILL.md 全文已在 `desc`;dev HEAD 是稳定基线,与父 agent 同基线 |
| **3 R6 清零** ⚠️ | **清零**(反推翻 D5 原"不累加") | scored-pass = 独立校验事件 → 复位 streak,与 ratchet reset 同构;"不累加"语义偏弱。代价见 §4。已回写 spec |
| **4 维度裁剪** | 3 维(reusability/verifiedness/non_redundancy)for v1 | 3 维是 darwin 反模式#1 规避核心;`dims` 是可扩展 dict,v1.5/v2 加维度不破 schema。9 维/5 维 YAGNI |
| **5 InProcessScorer 形态** | 同 client + fresh messages + scorer role-play in user/system | 承认**弱独立**(同 session/backend.history);`SubagentScorer` 才是真独立。fallback 职责 "better than no gate"。与 v1 backend.history 污染一致 |
| **6 降级 Brief 时序** | `_pending_briefs`,flush turn%10/task-end | 与既有 Brief 批处理(L393-395)一致;文案 `rejected-by-scorer` / `scorer-degraded-inprocess` 经 `build_brief` 生成 |

## 7. 技术风险与缓解

| 风险 | 缓解 |
|------|------|
| subagents 未合并,`SubagentScorer` 端到端不可测 | A2 抽象解耦;inprocess 路径独立端到端覆盖;subagent mock 单测覆盖;subagents 合并后无需改本 change 代码即启用真隔离 |
| 评判子 agent 不调 `submit_result`/超时 | D6 降级 inprocess + Brief 记降级,不静默落盘 |
| R6 清零误判通过丢历史 churn 信号 | `rationale` 留痕;R5 ratchet(v2)基于 R8 fitness-log before/after 比较,本 change 预留 `dims` 结构 |
| InProcessScorer 弱独立(同 session) | 文档声明;`SubagentScorer` 才是真独立;gate 默认 `_subagent_mgr` present 走真隔离 |
| daemon 线程 `run_single` 阻塞 | D7:`distill()` 已在 daemon 线程(`_safe_distill` L183),`proc.wait(timeout_s)` 不卡主循环;timeout 600s 短于 distill 整体容忍 |
| 评判子 agent 读到哪版 catalog | OQ2:`base_ref=None`→dev HEAD,与父 agent 同基线 |

## 8. 测试策略

| 测试 | 覆盖 | 路径 |
|------|------|------|
| 8.1 | `InProcessScorer`:monkeypatch `client.chat` 返回打分 JSON → `Verdict` 映射;非合规 → 降级 | inprocess 端到端 |
| 8.2 | `SubagentScorer`:mock `run_single` 返回合规/`failed`/`timed_out` → `Verdict`/降级路径 | mock(真隔离 deps-complete) |
| 8.3 | `_resolve_scorer`:env 各值 + `_subagent_mgr` present/absent → 返回实现类 | gate 路由 |
| 8.4 | 闸门:pass→`_apply_op`;reject→不调用 + `_pending_briefs` 有 "rejected-by-scorer";v1(gate off)→ 无打分直接 `_apply_op` | 闸门逻辑 |
| 8.5 | 独立性:`SubagentScorer` 构造的 `desc` 不含 `op['reason']`;`tools_subset` 不含执行类工具 | D3 硬保证 |
| 8.6 | R6:foreground patch 成功→归零;scored-pass background→清零;gate off→原累加;5 次后第 6 次→产 Brief 不落盘(熔断仍有效) | R6 修复 |
| 8.7 | 集成(deps-complete env,Windows `uv pip install -e ".[ui]"`):monkeypatch 子 agent 合规打分 → distill 全链跑通 | 真隔离端到端(deps 前置) |
| 8.8 | 集成:打分子 agent 不调 `submit_result`/超时 → distill 降级 inprocess + Brief 记降级 | 降级链 |
| 8.9 | `ruff check` 0 违规;`pytest tests/test_skill_evolution*.py tests/test_skill_scoring*.py` 不引入新失败(含既有 64) | 代码质量 |

**测试策略关键约束**:subagents 未合并到 dev,故 `SubagentScorer` 真隔离路径本 change 落地后**只能在 subagents 合并后端到端跑**;本 change 可独立验证 InProcessScorer 路径 + SubagentScorer 的 mock 单测。两条路径都有验证。

## 9. 边界条件

- `client.chat` 返回非 JSON / 缺字段 → `_validate_obj` 失败 → `Verdict(score=0, REJECT, source="degraded")` → 入 Brief,不落盘。
- `dims` 缺维度 → schema 校验失败 → 同上。
- `score` 恰好 = 阈值 → pass(`>=`)。
- foreground 路径不经闸门(`skill_write_origin='foreground'`),保留既有 read-only boundary(`author=user`/`evolvable=false` 拒绝)。
- `_subagent_mgr` present 但 `GA_SKILL_SCORER=inprocess` → 强制 inprocess(显式 override)。
- v1 模式(`GA_SKILL_EVOLUTION_ENABLED != '1'`)→ 整个 distill 不跑,闸门天然不触发。
- `MAX_AUTO_PATCH_PER_SKILL`(5)已满 + foreground 修正复位 → 后续 auto-patch 不锁死,直到计数再次达 cap(spec: "Breaker no longer permanently locks")。

## 10. Spec Patch(回写 delta spec)

回写 `specs/skill-memory-integration/spec.md` 的 `Auto-Patch Breaker Reset`:

- **prose (b)**:`SHALL NOT increment _auto_patch_counts` → `SHALL reset _auto_patch_counts[name] to zero via reset_auto_patch_count(name)`(an independently-verified patch resets the unverified-patch streak, the same treatment as a foreground correction)。
- **scenario**:`Scored-pass background patch does not increment breaker` → `Scored-pass background patch resets breaker counter`(THEN = `reset_auto_patch_count(name)` called, zeroing counter)。

`skill-scoring` spec 无需 patch(打分闸门场景与 R6 复位独立,OQ3 只动 R6 语义)。

## 11. 非目标(承自 proposal)

不做 R3(test-prompts 经验打分)/R5(ratchet 回退)/完整 darwin evolve loop;不改 subagents §5(`do_task`/`task` 工具/`GA_SUBAGENT_ENABLED` gate,D1 经代码验证为 red herring);不做 live steering / 通信中继 / 自动 merge / `GA_TASK_MODE=judge` 轻量模式(v2+);不做 darwin 全 9 维 / INDEX graph / embedding(v2+);R7(死计数器清理)/R8(fitness-log)/R9(validate 缺 loadability)不并入,另立 change。
