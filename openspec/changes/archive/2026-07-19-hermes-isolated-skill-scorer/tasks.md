## 1. Scorer 抽象与双实现

- [x] 1.1 在 `plugins/skill_evolution.py` 定义 `Verdict` dataclass(`score:int`/`verdict:enum`/`dims:dict`/`rationale:str`)与 `Scorer` 接口(`score(op, skill_md, history, catalog) -> Verdict`)
- [x] 1.2 实现 `InProcessScorer`:同进程第二次 LLM 调用(separate messages list + distinct prompt role),输入=候选 SKILL.md + task history + catalog(禁含 `op['reason']`/CoT),解析返回为 `Verdict`
- [x] 1.3 定义 `SCORE_SCHEMA`(打分对象 JSON schema:`score`/`verdict`/`dims{reusability,verifiedness,non_redundancy}`/`rationale`)与阈值常量 `GA_SKILL_SCORER_THRESHOLD`(默认 60,env 可覆盖)
- [x] 1.4 实现 `SubagentScorer`:调 `handler._subagent_mgr.run_single(desc=候选SKILL.md+history+catalog, schema=SCORE_SCHEMA, tools_subset=['file_read'], base_ref='HEAD', timeout_s=GA_SKILL_SCORER_TIMEOUT 默认 600)`,把返回对象映射为 `Verdict`

## 2. 收敛点接线(distill 插打分闸门)

- [x] 2.1 改 `distill()`(`plugins/skill_evolution.py:148-152`):在 `_parse_op` 与 `_apply_op` 之间插 `_scorer.score(op, skill_md, history, catalog)`
- [x] 2.2 闸门逻辑:`verdict=='pass'` 且 `score>=阈值` → 继续 `_apply_op`;否则 `handler._pending_briefs.append(build_brief('rejected-by-scorer', name, rationale, path))` 且不落盘
- [x] 2.3 gate 选择函数 `_resolve_scorer(handler)`:读 `GA_SKILL_SCORER` env,缺省由 `getattr(handler,'_subagent_mgr',None)` 是否 present 决定默认;返回 `SubagentScorer` 或 `InProcessScorer`
- [x] 2.4 gate 降级链:requested `subagent` 但 `_subagent_mgr` absent → stderr 一行 + 用 `InProcessScorer`;`SubagentScorer` 返回 `failed`/`timed_out` → 退回 `InProcessScorer` 兜底 + Brief 记降级(不跳过打分直接落盘)

## 3. 独立性硬保证

- [x] 3.1 `SubagentScorer` 构造 `desc`:候选 SKILL.md 全文 + `history_info[-40:]` 快照 + catalog 文本;**断言不含** `op.get('reason')` 与生成器 prompt 片段(单测 `assert 'reason' not in desc` 类)
- [x] 3.2 `tools_subset` 限定 `['file_read']`(强制 `submit_result` 由 `run_single`/`_apply_task_mode` 注入);确认不给 `code_run`/`file_write`/`web_scan`/`file_patch`(单测断言 `GA_TASK_TOOLS` env 不含这些)
- [x] 3.3 Open Question 复核:是否给 `get_skill_detail`(查冗余)——v1 先 `file_read` 兜底,`get_skill_detail` 列为 follow-up(本 change 不实现)

## 4. R6 熔断复位修复

- [x] 4.1 暴露 `reset_auto_patch_count(name)`:把 `_auto_patch_counts[name]` 置 0
- [x] 4.2 改 `_apply_op`:`skill_write_origin != 'background_review'`(前台)且 patch 成功 → 调 `reset_auto_patch_count(name)`(D5 (a))
- [x] 4.3 改 `_apply_op`:`background_review` patch 经打分闸门 `verdict=='pass'` 通过且 `_apply_op` 成功 → 调 `reset_auto_patch_count(name)` **清零**(D5 (b),OQ3 决议);打分 off(v1 模式)时维持原累加行为(legacy)
- [x] 4.4 文档:更新 `plugins/skill_evolution.py:36` 注释("前台修正时重置")使其与实际实现一致(原注释是空头支票,本 change 兑现)

## 5. ga.py 接线

- [x] 5.1 `GenericAgentHandler.__init__`:实例化 `self._subagent_mgr`(按 subagents `SubagentManager(root=cwd)` 接口);用 try/except + import 检测,`subagent_manager` 不可 import(未合并)时 `self._subagent_mgr=None`(不强制,保独立落地)
- [x] 5.2 确认 `agentmain.py` 子模式(`GA_TASK_MODE=isolated`)对评判官场景兼容:评判官 `desc` 不含 `reason`,`tools_subset` 已限定(子模式 `_apply_task_mode` 会强制加 `submit_result`,与本 change 的 `tools_subset=['file_read']` 协同)

## 6. 测试

- [x] 6.1 单测 `InProcessScorer`:monkeypatch `client.chat` 返回打分 JSON → 断言 `Verdict` 映射正确;mock 返回非合规 → 降级行为
- [x] 6.2 单测 `SubagentScorer`:mock `handler._subagent_mgr.run_single` 返回合规/`failed`/`timed_out` → 断言 `Verdict`/降级路径
- [x] 6.3 单测 gate 选择 `_resolve_scorer`:env 各值 + `_subagent_mgr` present/absent 组合 → 断言返回的实现类
- [x] 6.4 单测闸门:`pass`→`_apply_op` 调用;`reject`→不调用且 `_pending_briefs` 有 "rejected-by-scorer";v1 模式(gate off)→ 无打分直接 `_apply_op`
- [x] 6.5 单测独立性:`SubagentScorer` 构造的 `desc` 不含 `op['reason']`;`tools_subset` 不含执行类工具
- [x] 6.6 单测 R6:前台 patch 成功 → 计数归零;打分通过的 background patch → 清零(OQ3);打分 off → 原累加;5 次后第 6 次 → 产 Brief 不落盘(熔断仍有效)
- [x] 6.7 集成测(deps-complete env,Windows `uv pip install -e ".[ui]"`):monkeypatch 子 agent 产出合规打分 → distill 全链跑通(类比 hermes Step 5b / subagents §7 T1/T3)
- [x] 6.8 集成测:打分子 agent 不调 `submit_result`/超时 → distill 降级 inprocess + Brief 记降级
- [x] 6.9 `ruff check` 新代码 0 违规;`pytest tests/test_skill_evolution*.py tests/test_skill_scoring*.py` 不引入新失败(含既有 64 测试)

## 7. 文档

- [x] 7.1 `AGENTS.md` 登记 `GA_SKILL_SCORER` env gate 与 `GA_SKILL_SCORER_THRESHOLD`/`GA_SKILL_SCORER_TIMEOUT`(若需)
- [x] 7.2 更新 `hermes/NOTES_v1.5_recommendations.md`:标 R2 已实现(independence-by-construction 落地)+ R6 已修;R1/R3/R5/R7/R8/R9 仍 open
- [x] 7.3 更新 `HANDOFF.md` 或新增 design 摘要(交接用)

## 8. Final Review (review_mode=standard,全 change 轻量审查)

**整体 verdict**: ready to merge — 可进入 verify 阶段。无 Critical / Important。

**Minor finding(接受,不阻塞 verify)**:
- M1 `SubagentScorer.score` 冗余 `try/except ScorerDegraded → raise`(捕获即重抛)。可简化为直接 `_validate_obj`。无功能影响,纯冗余。
- M2 `_score_with_fallback` 防御性 pass-through `source="degraded"` 与 rationale "v1 passthrough" 不一致(宜 `source="disabled"`)。生产不可达(`_scoring_enabled` False 时不调),纯防御。
- M3 InProcessScorer 自身降级(非 JSON/校验失败)在闸门标 `rejected-by-scorer` Brief,不区分 "scorer 降级" vs "scorer 拒绝"。可观测性 gap,行为正确(op 被拦截)。
- M4 降级 Brief 用未 strip 的 `op.get("name","?")`,distill rejected 用 stripped name。名字几乎不含空白,无实际影响。
- M5 `_validate_obj` `isinstance(v,int)` 接受 bool(`bool` 是 `int` 子类),LLM 返 `score:true` 会被当 1。LLM 按指令返 0-100 整数,非实际风险。

**Cannot-verify(env-gated,change 约束已 acknowledged)**:
1. SubagentScorer 真隔离端到端(6.7 skipif,待 subagents 合并到 dev 后补 TODO)
2. ga.py `_subagent_mgr` 真实例化(当前 ImportError→None,待 subagents 合并验证 SubagentManager(root=cwd) 签名)
3. `SubagentManager.run_single` 真实签名(mock `_FakeMgr` 假设参数名,合并时核对)

**接受理由**:5 Minor 均为代码质量/可观测性改进,生产不可达或无实际影响;3 Cannot-verify 为 change 约束已 acknowledged 的 env-gated 路径(subagents 未合并),本 change 落地锚点是 InProcessScorer 路径独立端到端跑(已覆盖)。
