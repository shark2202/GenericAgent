## Context

hermes v1 的 `distill()`(`plugins/skill_evolution.py:113`)在 `agent_after` hook 的 daemon 线程里跑,流程:`client.chat` 生成 op → `_parse_op` → `_apply_op`(直接落盘)。生成与评估同属一次 LLM 调用,唯一闸门是 `validate_skill` 的结构性检查。R2 计划在 parse→apply 之间插打分器,当前 notes 写法是"同进程第二次 LLM 调用 + separate messages list + distinct prompt role"——独立性靠 prompt 工程软保证。

`add-first-class-subagents`(build 45%)已实现 `SubagentManager.run_single(desc, schema, base_ref, model, tools, timeout_s)`(`subagent_manager.py:122`):独立 git worktree(`--detach`)+ subprocess 起 `agentmain.py`(`assets/subprocess_worker.py:spawn_agentmain`,注入 `GA_TASK_MODE=isolated` + `GA_TASK_RESULT_SCHEMA` + `GA_TASK_TOOLS`)+ `validate(obj, schema)` 校验结果对象 + 进程组 kill + 失败保留 worktree。子 agent 强制以 `submit_result` 终末工具提交 schema-校验对象。

本变更的收敛点:`distill()` parse op(`:148`)与 apply op(`:152`)之间插 `Scorer.score(...)`,subagent 实现经 `run_single()` 派发评判子 agent。关键事实:`run_single()` 是**程序化派发**(插件代码内部调用),不经 `task` 工具(subagents §5,未做)——后者是父 agent 的 LLM 扇出路径,与本收敛正交。故本变更**软依赖** subagents §1-4 的 `SubagentManager` 模块,不依赖 §5。

## Goals / Non-Goals

**Goals:**
- R2 打分器以隔离子 agent 实现:独立 worktree + 独立 LLM session,评估器看不到生成器 reason/CoT(independence-by-construction)。
- `Scorer` 抽象双实现:`SubagentScorer`(gate on,真隔离)+ `InProcessScorer`(兜底,= 当前 R2 notes 写法)。
- gate 检测 `handler._subagent_mgr` present 决定默认;缺则强制 inprocess,本变更在无 subagents 的 dev 上仍可独立落地。
- 顺手修 R6(`_auto_patch_counts` 永不复位):打分器引入会改变 patch 流量,R6 不修则合理 patch 被错误锁死。
- 非破坏:gate off / 无 `_subagent_mgr` 时行为 = hermes v1。

**Non-Goals:**
- 不做 R3(test-prompts 经验打分)/R5(ratchet 回退)/完整 darwin evolve loop(依赖 R1 信号 + R8 fitness-log + clustering,超范围)。
- 不改 subagents §5(`do_task`/`task` 工具/`GA_SUBAGENT_ENABLED` gate)——经代码验证为收敛 red herring。
- 不做 live steering / 通信中继 / 自动 merge / `GA_TASK_MODE=judge` 轻量模式(v2+)。
- 不做 darwin 全 9 维 rubric / INDEX graph / embedding(v2+)。
- R7(死计数器清理)/R8(fitness-log)/R9(validate 缺 loadability)不并入——正交或仅 R5 才需要,另立 change。

## Decisions

**D1 — 收敛走 `run_single()` 程序化路径,非 `task` 工具**
打分器是 `distill()` 内部程序化派发,不是父 agent 的 LLM 工具选择。`SubagentScorer` 直接调 `handler._subagent_mgr.run_single(...)`。备选:经 `task` 工具(需 subagents §5,未做,且语义不符——`task` 是父 LLM 扇出,非插件内部调用),否决。

**D2 — `Scorer` 抽象 + 双实现,默认由 gate 选**
接口 `score(op, skill_md, history, catalog) -> Verdict(score, verdict, dims, rationale)`。`SubagentScorer` 经 `run_single`;`InProcessScorer` = 同进程二次 LLM(= 当前 notes)。gate `GA_SKILL_SCORER=subagent|inprocess`,缺省由 `_subagent_mgr` 是否 present 决定。备选:只做 subagent 实现(无 subagents 时整个 R2 不可用,退化为 v1 无打分,否决——破坏独立落地);只做 inprocess(不满足 independence-by-construction,否决)。

**D3 — 评判官独立性硬保证 = desc 内容 + tools 双约束**
`SubagentScorer.run_single()` 的 `desc` = 候选 SKILL.md 全文 + task history 快照(复用 `distill()` 已有的 `history_info[-40:]`)+ catalog(供查冗余)。**禁含**生成器 `op['reason']` 与任何 chain-of-thought。`tools_subset=['file_read']`(读候选/查 catalog) + 强制 `submit_result`(终末提交打分对象);**不给** `code_run`/`file_write`/`web_scan`(评判官只读,不执行)。备选:给评判官 `get_skill_detail`(查冗余更准)——v1 先 `file_read` 兜底,`get_skill_detail` 留 design 开放项。

**D4 — 打分 schema 与阈值**
`result_schema` = `{score:int 0-100, verdict:enum['pass','reject','revise'], dims:{reusability,verifiedness,non_redundancy}:int 0-100, rationale:string}`。闸门:`verdict=='pass'` 且 `score>=THRESHOLD`(默认 60,env `GA_SKILL_SCORER_THRESHOLD`)→ `_apply_op`;否则入 Brief 标 "candidate failed review"。备选:纯数值阈值无 verdict(丢失 reject/revise 语义,否决)。

**D5 — R6 复位语义:打分通过视为独立校验事件→清零;前台人工修正复位**(OQ3 决议,反推翻原"不累加",详见 Design Doc §4/§6)
- `_auto_patch_counts[name]` 在 `_apply_op` patch 成功时累加(现状,保留;仅在打分 off 的 legacy 分支)。
- **复位时机**:`reset_auto_patch_count(name)` 在两情形调用,均清零 `_auto_patch_counts[name]`:(a) `skill_write_origin != 'background_review'`(前台 `do_skill_manage` 人工修正,= R6 notes 原意);(b) R2 打分 `verdict=='pass'` 的 background patch 经 `_apply_op` 成功 → 视为独立校验事件 → **清零** 计数(打分通过 = 独立校验,复位"连续未校验"streak,与前台修正同处置)。
- 备选:打分通过仍累加(打分器引入后 5 次好 patch 仍锁死,R6 未真修,否决);打分通过仅"不累加"不清零(保留历史 streak、语义偏弱,design 阶段 OQ3 复核后否决,选清零)。

**D6 — gate 降级链**
`GA_SKILL_SCORER=subagent` 但 `_subagent_mgr` absent → 自动降级 inprocess + stderr 记一次。`SubagentScorer.run_single` 返回 `failed`/`timed_out` → distill 退回 inprocess 兜底打分 + Brief 记录降级(不直接跳过打分落盘,否则退化回 v1 的无条件信任)。

**D7 — daemon 线程阻塞**
`distill()` 已在 daemon 线程(`_safe_distill`),`run_single` 的 `proc.wait(timeout_s)` 阻塞不卡主 agent 循环。scorer `timeout_s` 默认 600s(env `GA_SKILL_SCORER_TIMEOUT`),短于 distill 整体容忍。v1 单次只评 1 op,不涉及 `run_batch`。

## Risks / Trade-offs

- [独立性软肋:desc 仍含 task history,生成器与评估器看同一段历史] → 评估器看的是**任务客观历史**,不含生成器的**判断**(reason/CoT 已剔除);这是 nuwa/cangjie dual-agent blind test 的同款隔离强度,可接受。
- [subagents 未合并到 dev,`SubagentManager` 不可 import] → D2 gate 自动降级 inprocess;本 change 可独立验证 inprocess 路径;subagents 合并后无需改本 change 代码即启用真隔离。
- [评判子 agent 不调 `submit_result`/超时] → D6 降级 inprocess 兜底 + Brief 记录;不静默落盘。
- [R5 依赖的 R8 fitness-log 未做,R2 打分结果无处留痕] → 本 change 仅在 `_pending_briefs` 记 review Brief;fitness-log 是 R5 的前置,本 change 出范围,但 R2 的打分结果 schema 已为 R5 的 before/after 比较预留 `dims` 结构。
- [评判子 agent 读到哪版 catalog(`base_ref` 选择)] → Open Question;倾向用 dev HEAD(`run_single(base_ref='HEAD')`),与父 agent 同基线。
- [起整 agentmain.py 进程只为评一份 SKILL.md,偏重] → v1 接受(T5);`GA_TASK_MODE=judge` 轻量模式留 v2。

## Migration Plan

纯新增 + feature gate,无数据迁移。`GA_SKILL_EVOLUTION_ENABLED=1`(hermes v1 门)+ `GA_SKILL_SCORER=subagent`(本 change 门,缺省由 `_subagent_mgr` present 决定)。rollback = `GA_SKILL_SCORER=inprocess` 或关 `GA_SKILL_EVOLUTION_ENABLED`。R6 修复随打分器一同落地,无独立 rollback(前台修正复位是行为修复,不可回退到"永不复位"的 bug 态)。

## Open Questions(design 阶段已决议,详见 Design Doc §6)

- ✅ 评判官是否给 `get_skill_detail` 工具 → **仅 `file_read`**;catalog 已在 desc,`get_skill_detail` 列 v2 follow-up(YAGNI)。
- ✅ `base_ref` 选择 → **`None`(默认 dev HEAD)**;scorer 子 agent 不需 SubagentManager,候选 SKILL.md 全文已在 desc。
- ✅ R6 复位时机 (b) 是否清零 → **清零**(反推翻 D5 原"不累加",见 Design Doc §4/§6);已回写 delta spec Spec Patch。
- ✅ 打分维度裁剪 → **3 维**(reusability/verifiedness/non_redundancy);`dims` 是可扩展 dict,v1.5/v2 加维度不破 schema。
- ✅(隐含)InProcessScorer 独立性形态 → 同 client + fresh messages + role-play(弱独立,见 Design Doc §2.2)。
- ✅(隐含)降级 Brief 时序 → `_pending_briefs` 走 turn%10/task-end flush(见 Design Doc §2.4)。
