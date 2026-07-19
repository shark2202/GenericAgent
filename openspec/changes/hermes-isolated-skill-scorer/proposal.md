## Why

hermes v1 的 `distill()`(`plugins/skill_evolution.py`)在任务后把执行路径结晶成 Skill,但**生成与评估由同一次 LLM 调用包办**——生成器既产出 op 又被无条件信任,唯一闸门是结构性 `validate_skill`。darwin-skill 的反模式 #1 明确指出"同一 AI 既改又评"会导致 ~46% 的 self-eval 准确率失效;nuwa/cangjie 生态用 dual-agent blind testing 规避。当前 hermes v1.5 的 R2 计划(`hermes/NOTES_v1.5_recommendations.md`)写的是"同进程第二次 LLM 调用 + separate messages",**进程内仍共享进程状态/日志/潜在的上下文泄漏,独立性是软保证**。本变更把 R2 升级为真隔离子 agent:经 `SubagentManager.run_single()`(`add-first-class-subagents` §1-4)派发独立 worktree + 独立 LLM session 的评判官,实现 independence-by-construction——生成器与评估器是不同进程、不同 session,评估器只拿到候选 SKILL.md + task 历史,**看不到生成器的 reason/CoT**。

同时修复与之纠缠的真实 bug **R6**(`_auto_patch_counts` 永不复位,5 次自动 patch 后技能永久锁死)——打分器引入会改变 patch 流量,R6 不修则打分通过的合理 patch 会被错误熔断锁死。

## What Changes

- 新增 `Scorer` 抽象(`plugins/skill_evolution.py`):接口 `score(op, skill_md, history, catalog) -> Verdict`,两实现:
  - `InProcessScorer`(back-compat 兜底):同进程第二次 LLM 调用(= 当前 R2 notes 写法),保持现有行为
  - `SubagentScorer`(gate on):经 `SubagentManager.run_single()` 派发隔离评判子 agent,`result_schema` 定为打分对象,`tools_subset=['file_read']`(+强制 `submit_result`)
- 改 `distill()`:在 `_parse_op`(`:148`)与 `_apply_op`(`:152`)之间插入打分闸门;score ≥ 阈值才 `_apply_op`,否则入 `handler._pending_briefs` 标 "candidate failed review"
- 新增 gate:`GA_SKILL_SCORER=subagent`(默认,需 SubagentManager present)vs `=inprocess`(兜底);`__init__` 时检测 `handler._subagent_mgr` present 决定默认(无 _subagent_mgr 则强制 inprocess)
- 修 **R6**:暴露 `reset_auto_patch_count(name)`,在 `_apply_op` 当 `skill_write_origin != 'background_review'`(前台人工修正)且 patch 成功时复位;R2 打分通过视为该校验成立——design 阶段定 R6 复位语义的最终规则
- `ga.py` `__init__`:按 `add-first-class-subagents` 的预期接口实例化 `self._subagent_mgr`(gate 检测 present,**不强制**——缺则退回 inprocess 打分)
- 独立性硬保证(由 spec 约束):评判官 `desc` **禁含**生成器 `reason`/CoT;`tools_subset` 限定通读 + 终末提交(不给 `code_run`/`file_write`/`web_scan`)
- **非破坏性**:gate off 或无 `_subagent_mgr` 时行为 = hermes v1(单次 LLM 无打分闸门);无 BREAKING

## Capabilities

### New Capabilities

- `skill-scoring`: 任务后技能蒸馏产物的独立评估闸门——把 distill 生成的候选 op 交给隔离评判子 agent 对 reusable/verified/non-redundancy 维度打分,阈值以下拦截并产出 review Brief。承载 hermes v1.5 R2 的 independence-by-construction 实现与 scorer 抽象(同进程兜底 + subagent 真隔离)。

### Modified Capabilities

- `skill-memory-integration`: 自动写入路径 `distill()→_apply_op()` 之间新增打分闸门——score < 阈值的 patch 不再落盘,改为入 Brief 标注 "candidate failed review";`_auto_patch_counts` 复位语义随 R6 修复变更(前台人工修正 + R2 打分通过触发的复位规则)。

## Impact

- **代码**:`plugins/skill_evolution.py`(+`Scorer` 抽象/两实现/`reset_auto_patch_count`、改 `distill` 插闸门)、`ga.py`(`__init__` 实例化 `_subagent_mgr`,按 subagents `SubagentManager(root)` 接口)、可能的 `skill_loader.py`(若 scorer `desc` 构造复用 catalog 助手)
- **依赖**:新增**软依赖** `add-first-class-subagents` 的 `subagent_manager.py` + `assets/subprocess_worker.py`(经 `run_single()` 接口消费);A2 抽象解耦保证两者未合并到 dev 时本 change 仍可独立落地(跑 inprocess 兜底)
- **API**:新增 `GA_SKILL_SCORER` env gate;`Scorer` 抽象为模块内接口(非 public API)
- **legacy**:hermes v1 行为在 gate off 时完全保留;不触碰既有的 `do_skill_manage`/`do_start_long_term_update`/`turn_end` Brief flush 流程
- **非目标**:不做 R3(test-prompts 经验打分)/R5(ratchet 回退)/完整 evolve loop;不改 subagents §5(`do_task`/`task` 工具);不做 live steering / 中继 / 自动 merge