# Workflow Spec — 任务后技能蒸馏 (Post-Task Skill Distillation)  [FINAL]

> loop-me workflow. hermes/ga.py 自进化 v1 主回路。
> 状态：**FINAL** — 设计分叉（Q3 自主边界 / Q4 fitness）已解决；实现接口已钉死，构建者无需再问。
> 读者：实现该回路的 agent/开发者。

## 一句话
复杂任务结束后，自动复盘本次会话，把"事实验证成功且可复用"的做法结晶成一个 **Skill**（或改进已有 Skill），带来源标记与 pushed-right Brief；用户自有技能永不被自动改写。

## 词汇对齐（loop-me）
- **Trigger**：事件。`agent_after` 钩子（任务完成）+ 信号触发（v1.5，不满意/错误）+ 手动入口（`do_start_long_term_update`）。非定时。
- **Checkpoint**：人工只审"成品 Brief"。agent 自有技能 patch **无** checkpoint（自主落盘）；仅"全新技能首版 / 触碰用户技能 / 信号纠正"才 checkpoint。
- **Push right**：蒸馏做完所有工作（提取→匹配→起草→校验→落盘）后再请人审一次。
- **Brief**：`📌[Skill蒸馏] <action> <name>` + 1–2 句为什么 + diff 摘要 + `file_read` 链接 + `[approve|edit|revert]`；批量进 `turn%10` nudge 槽。

## 单元：Skill（复用现有底座）
- 存储：复用 `skill_loader` 已有根 `<cwd>/.agents/skills/<name>/` 与 `~/.agents/skills/<name>/`（agentskills.io 标准，与 hermes-agent 同构）。**不新建 `memory/skills/`**。
- 结构：`SKILL.md`（frontmatter + 正文）+ 可选 `references/ scripts/ assets/`。
- frontmatter（在现有 `name/description` 上扩展；loader v1 只强依赖 name+description，其余由 `parse_provenance` 读）：
  ```yaml
  name: <slug>
  description: <≤120 字，progressive-disclosure 摘要>
  version: <0.1 起，patch +0.0.1>
  author: agent | user            # provenance；默认缺省视为 user
  evolvable: true | false        # 是否允许自动回路改进；author=agent 默认 true
  created_at: <ISO8601>
  evolved_from: <parent skill name | null>
  mcp_dependencies: [<server/tool>, ...]   # 任务中用过 mcp_call 时必填
  fitness: null                   # v2 占位
  ```

## Provenance + 自主权（复用 `backup_and_patch_skill` 模式）
- 写来源 ContextVar：`skill_write_origin ∈ {foreground, background_review}`（镜像 hermes-agent）。
- 环境门控：复用现有 `GA_SKILL_PATCH_ENABLED` 思路，新增 `GA_SKILL_EVOLUTION_ENABLED=1` 才允许自动回路落盘；缺省只产 Brief 不落盘（安全默认）。
- 自动回路（background_review）**只能** create/patch `author=agent 且 evolvable!=false` 的技能；`author=user` 一律拒写、改发"建议 Brief"。
- 备份：patch 前写 `<file>.prev`（扩展现有 `backup_and_patch_skill` 的 `.bak` 为轮转 `.prev`）；`revert` = `.prev` 覆盖回。

## Trigger（事件，按优先级）
1. **任务完成（主，v1）**：`plugins/hooks.py` `agent_after`，`exit_reason['result'] ∈ {CURRENT_TASK_DONE, EXITED}` 且 `handler.current_turn ≥ SKILL_DISTILL_MIN_TURNS`（默认 6，复杂度门控）→ spawn 蒸馏线程。
2. **信号触发（v1.5，预留）**：`tool_after`/`turn_after` 捕获——`ask_user` 返回修正、或单轮连续 `code_run/file_patch` 失败 ≥3 次——置 `handler._evolution_signal`，任务结束时带信号触发。
3. **手动入口（v1）**：`do_start_long_term_update`（保留语义）→ `skill_write_origin=foreground` + 调 `distill()`。
- 不用 schedule。定时批量复盘 = v2（`reflect/scheduler.py` 加 job 周期扫 L4）。

## 机制（回路主体，后台 daemon 线程）
关键：蒸馏是**一次性自包含 LLM 调用**，输入 = `handler.history_info`（已折叠摘要）+ `handler.working` + 技能目录（name+description），**不共享 live session**，避免会话竞争。
1. **载入**：读 `handler.history_info` + `handler.working['key_info']` + `_get_skills_catalog()`。
2. **提取**：一次 LLM 调用 → 候选"值得结晶的模式"（每条：类型/适用场景/核心步骤/坑点/mcp 依赖）。只取验证成功且可复用项。
3. **匹配**：`skill_loader.retrieve_skill_by_desc(desc)`（v1 difflib/关键词；v2 embedding）→ 命中 patch，未命中 create。
4. **起草**：生成/改进 `SKILL.md`（含 provenance frontmatter）。create: `author=agent, evolved_from=null, version=0.1`；patch: `version` +0.0.1，保留 `evolved_from` 根。
5. **校验**：`skill_loader.validate_skill(path)`——frontmatter 有 name+description、desc≤120、`get_skill_detail(name)` 可加载、正文含≥1 个 `##`。失败→丢弃、不落盘、Brief 记"候选未通过校验"。
6. **落盘**：经 `do_skill_manage`（`skill_write_origin=background_review`），写前 `.prev` 备份。熔断（D8）：单次蒸馏改≥3 技能、或单技能连续自动 patch≥5 次无人工修正 → 改为"只产 Brief 不落盘"。
7. **fitness 记录**：向 `memory/skill_exp_<name>.md` 追加一条 `(ts, action, source_turns, signal)`（复用现有 experience 文件，驱动 L1 hot/cold）。
8. **Brief**：`build_brief(...)` → 入队 `handler._pending_briefs`，`turn_end_callback` 在 `turn%10` 槽 flush。

## Checkpoint / Push right / Brief 规则（D8 确认）
| 情形 | 是否落盘 | Checkpoint | Brief |
|---|---|---|---|
| 改进 agent 自有技能（patch, evolvable） | 自主落盘 | 无 | 异步事后 Brief + revert |
| 全新技能首版（create） | 先落盘 | 有（审成品） | Brief + `[approve\|edit\|revert]` |
| 触碰用户技能（author=user） | 不落盘 | 有（建议） | 只提建议 diff |
| 信号触发纠正 | 先落盘 | 有 | Brief 标注触发信号 |
- 熔断触发时：所有动作降级为"只产 Brief 不落盘"。
- Brief 批量进 `turn%10` 槽，避免逐技能打断。

## 验收
### L1 机制正确性（pytest fixture，gate）
T1 trigger 命中 / T2 复杂度门控 / T3 提取只留验证成功项 / T4 匹配 patch-or-create / T5 校验失败丢弃 / T6 provenance 拒改 user 技能 / T7 `.prev`+revert / T8 Brief 字段+`turn%10` 入队 / T9 信号触发(v1.5) / T10 幂等（同会话重触不重复）。
### L2 有效性（fitness = D7: A+D+demo）
- **(A) 负信号递减**：`tool_after/turn_after` 记 `(task_class, errors, retries, user_corrections, turns, cost)`；task_class 由 working `key_info`/tag 聚类。"有效"=≥3 个 task_class 蒸馏后滑动窗口指标改善（errors↓或turns↓或corrections↓），每类 before/after 各≥5 实例。
- **(D) Brief 接受率**：前 M 个自动产出技能 `approve-without-edit`≥70%、`revert_rate`<20%。
- **人工 demo**：1 个真实复现任务，before/after 指标，用户确认。

## DoD（完成定义）
- [ ] L1 T1–T10 全绿，并入 `tests/`。
- [ ] `.agents/skills/` 可用、`.prev`+`GA_SKILL_EVOLUTION_ENABLED` 门控生效。
- [ ] `do_skill_manage` 经 `assets/tools_schema.json`(+`_cn`) 注册，LLM 可调用且 provenance 生效。
- [ ] ≥1 真实任务端到端跑通：完成→触发→蒸馏→落盘→Brief→approve→下一同类任务指标改善。
- [ ] L2 (A) 在≥3 task_class 成立或人工 demo 通过；(D) 接受率达标。
- [ ] `memory/memory_management_sop.md`（L0）更新技能生命周期一节。
- [ ] 无回归：`ruff check .` 不引入新违规；`pytest tests/` 现有用例不破。

## 实现接口（钉死，构建者照此实现）

### `skill_loader.py` 新增
```python
def parse_provenance(skill_md_path) -> dict:
    # 读 frontmatter: author(default 'user'), evolvable(default: author=='agent'),
    # evolved_from, version, created_at, mcp_dependencies, fitness

def validate_skill(skill_md_path) -> tuple[bool, str]:
    # (True,'') 当且仅当: frontmatter 有 name+description; desc<=120;
    # get_skill_detail(name) 不返回 status=='error'; 正文含 >=1 个 '## '

def retrieve_skill_by_desc(desc: str, catalog=None) -> str | None:
    # 在 catalog(name->(desc,path)) 上匹配 desc; v1: difflib SequenceMatcher 取 top-1 且 ratio>0.5
    # 否则 None
```

### `plugins/skill_evolution.py`（新；被 `discover_and_load` 自动加载）
```python
import contextvars, threading
from plugins.hooks import register
from skill_loader import (retrieve_skill_by_desc, validate_skill,
    parse_provenance, _get_skills_catalog, backup_and_patch_skill)

SKILL_DISTILL_MIN_TURNS = 6
skill_write_origin = contextvars.ContextVar('skill_write_origin', default='foreground')  # foreground|background_review

@register('agent_after')
def _on_agent_after(ctx):
    er = ctx.get('exit_reason')
    if not er or er.get('result') not in ('CURRENT_TASK_DONE','EXITED'): return
    h = ctx['handler']
    if h.current_turn < SKILL_DISTILL_MIN_TURNS: return
    if not _evolution_enabled(): return
    threading.Thread(target=_safe, args=(distill, ctx['client'], h, h._evolution_signal), daemon=True).start()

@register('tool_after')
def _on_tool_after(ctx): ...           # 累计连续失败 -> h._evolution_signal
@register('turn_after')
def _on_turn_after(ctx): ...           # 捕获 ask_user 修正 -> h._evolution_signal

def distill(client, handler, signal=None):
    # §机制 1-8; 一次性 LLM 调用(prompt 自含 history_info+catalog);
    # 落盘走 handler.dispatch('skill_manage', args, dummy_response) 或直接调 do_skill_manage
def build_brief(action, name, why, diff_summary, path) -> str
def _evolution_enabled() -> bool: return os.environ.get('GA_SKILL_EVOLUTION_ENABLED')=='1'
```

### `ga.py` 改动
```python
def do_skill_manage(self, args, response):
    # action: create|patch|retire|list_evolvable
    # args: name, skill_md(完整 SKILL.md，create/patch 用), reason, dry_run
    # provenance gate: 若 skill_write_origin=='background_review':
    #     prov = parse_provenance(path); 若 prov.author=='user' 或 evolvable==False -> 拒绝，返回 suggest brief
    # patch/create 前调 backup_skill_prev(path) 写 .prev
    # 完成后 _reset_skill_cache(); sync_skills_to_l1()
    # 返回 StepOutcome(result, next_prompt=self._get_anchor_prompt(...))
```
- `GenericAgentHandler.__init__` 加 `self._pending_briefs=[]; self._evolution_signal=None`。
- `do_start_long_term_update`：设 `skill_write_origin='foreground'` 后调 `distill(client, self, signal='manual')`（同步或 spawn）。
- `turn_end_callback`：`turn%10` 分支前 `next_prompt += ''.join(self._pending_briefs); self._pending_briefs.clear()`。

### `assets/tools_schema.json` + `tools_schema_cn.json`
新增 `skill_manage` 条目：`action(enum: create|patch|retire|list_evolvable), name, skill_md, reason, dry_run`。写动作需 `GA_SKILL_EVOLUTION_ENABLED=1`（list_evolvable/dry_run 豁免）。

## 改动落点
| 文件 | 改动 |
|---|---|
| `skill_loader.py` | +`parse_provenance`/`validate_skill`/`retrieve_skill_by_desc`；扩展 `backup_and_patch_skill` 为轮转 `.prev` |
| `plugins/skill_evolution.py`（新） | 钩子 + provenance ContextVar + `distill`/`build_brief`/`spawn` |
| `ga.py` | +`do_skill_manage`；`__init__` 加 pending_briefs/signal；重构 `do_start_long_term_update`；`turn_end_callback` flush brief |
| `assets/tools_schema{,_cn}.json` | +`skill_manage` schema（`action/name/skill_md/reason/dry_run`） |
| `memory/memory_management_sop.md` | L0 增"技能生命周期"一节，替换"直接 file_patch L3"指引 |
| `assets/sys_prompt.txt` | 注明 `skill_manage` + "任务结束自动蒸馏，无需手动 file_patch 记忆" |
| `tests/test_skill_evolution.py`（新） | L1 T1–T10 |

## v1 不做（边界）
fitness 评分(score_state) / FTS5 跨会话搜索 / 定时复盘(reflect job) / Swarm+SwarmFlow / darwinian 进化搜索 → v2/v3 或独立回路。
