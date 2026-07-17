# Hermes — NOTES (the user's world)

> loop-me workspace for the task: "把 ga.py 改造成功能完整的 AI agent hermes，参考 hermes-agent / jiuwenswarm，增强自进化能力".
> Canonical terms are **bolded** on first use; thereafter used verbatim.

## The two reference projects

### hermes-agent (Nous Research) — "the only agent with a built-in learning loop"
Self-evolution = a **closed learning loop**: experience → skills → skill self-improvement → cross-session recall → user modeling.
Canonical mechanisms:
- **Skill** = procedural memory (a directory: `SKILL.md` frontmatter + `references/ templates/ scripts/ assets/`). Distinct from declarative memory.
- **skill_manage** tool — agent-authored create/edit/patch/delete/write_file/remove_file on skills, written to `~/.hermes/skills/`.
- **write-origin provenance** — ContextVar distinguishing **foreground** (user-directed) vs **background_review** (agent-autonomous) skill writes. Curator only consolidates skills *it* created.
- **background review fork** — `_spawn_background_review` / `spawn_background_review_thread`: a persistence-isolated autonomous fork that reviews the just-finished session and evolves agent-created skills/memory. Event-style trigger after a task.
- **darwinian-evolver** — evolutionary search loop (organism + evaluator + mutator + fitness fn) to optimize a prompt/regex/SQL/snippet against a scorer. Used by the agent as a tool.
- **trajectory_compressor** — compress sessions into training trajectories for next-gen tool-calling models.
- **learn nudge / memory provider** — periodic prompts to persist knowledge; `USER.md` dialectic user modeling (Honcho).
- **FTS5 session search** + LLM summarization → cross-session recall.

### jiuwenswarm (openJiuwen) — "Understands Your Intent, Evolves Autonomously"
Self-evolution = signal-driven: detect error/dissatisfaction → optimize skill definitions. Swarm collaboration for complex tasks.
Canonical mechanisms:
- **Swarm Skill** — multi-role extension of the Skills standard: `SKILL.md` + `roles/` + `workflow.md` + `bind.md` + `dependencies.yaml`, optionally `scripts/workflow.py` (**SwarmFlow** executable orchestration).
- **swarmskill-creator** — authored skill that creates/converts/modifies swarm skills via a staged pipeline + validator.
- **Skill self-evolution** — "automatically detects error signals and user dissatisfaction, then optimizes Skill definitions."
- **Auto Harness** — eval-driven, end-to-end automated harness optimization (not model weights; the harness/skill learns in practice). Backed by `symphony/`: `experience/`, `skill_retrieval/`, `score_state`, `score_storage`, `orchestration/`.
- **Swarm** — Leader decomposes tasks, assembles **Teammate** agents across processes/machines; multi-stage **Swarmflow** handoff.
- **Rails** — `team_member_skill_toolkit_rail`, `team_shared_skill_link_refresh_rail`, `team_skill_storage_policy_rail`, `team_permission_policy_rail` → governance around shared skills.

## GenericAgent's existing self-evolution assets (the seed we build on)
- `do_start_long_term_update` (ga.py) — manual, agent-invoked trigger to crystallize experience into L3 SOPs via `file_patch`. No skill structure, no provenance, no validation, no feedback signal.
- **L1–L4 memory**: L1 index (`global_mem_insight.txt`) → L2 facts (`global_mem.txt`) → L3 task SOPs/skills (`memory/*.md|*.py`) → L4 raw sessions (`L4_raw_sessions/`).
- **skill_loader** — `sync_skills_to_l1()` + `get_skill_detail()` → progressive disclosure; skill catalog injected into system prompt.
- `morphling_sop.md` — absorb external projects into skills (skill import).
- **reflect/** — scheduled/autonomous loops: `scheduler.py` (120s poll), `autonomous.py`, `goal_mode.py`. No skill-evolution reflect yet.
- **plugins/hooks.py** — event hooks (`tool_before/after`, `turn_before/after`, `llm_before/after`, `agent_before/after`) — a clean place to emit self-evolution signals.
- `turn_end_callback` (ga.py) — already injects periodic nudges (turn%7→checkpoint, turn%10→global memory, turn%25→write findings to file, turn%175→ask_user). Seed of a nudge system.
- **MCP** — `mcp_call` + `mcp_client.py`; MCP tools merged into skill catalog.

## Gap (what ga.py lacks vs the two references)
1. No first-class **Skill** object — L3 is loose `.md`/`.py` files edited by raw `file_patch`; no provenance/version/fitness.
2. No **SkillManager** tool (structured create/patch/delete + provenance + validation).
3. No **background/autonomous review loop** that evolves agent-created skills after a task.
4. No **error/dissatisfaction signal capture** (jiuwenswarm's driver).
5. No **experience store + retrieval + scoring** (symphony) nor **trajectory compression** (hermes-agent).
6. No **cross-session search** over L4.
7. No **skill self-improvement during use** (skills that mutate themselves).

## Resolved decisions (grilling)
- **D1 (hermes 身份/范围)**：in-place 增强 ga.py 的自进化（不先做 standalone-runnable 重命名；standalone `hermes.py` 留作后续步）。工作假设，用户未反对。
- **D2 (v1 主回路)**：loop (a) 任务后技能蒸馏。其它回路 (b)定时复盘 / (d)经验轨迹管线 / (e)跨会话搜索 / (f)swarm / (c)darwinian 进化 = 后续 v2/v3 或独立回路。
- **D3 (自进化单元)**：Skill 一等公民。**更正**：复用现有 `.agents/skills/<name>/`（user=`~/.agents/skills`，project=`<cwd>/.agents/skills`，agentskills.io 标准，与 hermes-agent 同构），不新建 `memory/skills/`。frontmatter 在现有 name/description 上加 `author(agent|user)/evolvable/evolved_from/version/created_at/mcp_dependencies/fitness?`。旧 `memory/*_sop.md` 维持 L3 SOP，不被 skill_loader 索引。
- **D4 (provenance + 自主权)**：写来源 ContextVar `skill_write_origin∈{foreground,background_review}`。复用并扩展现有 `skill_loader.backup_and_patch_skill` + `GA_SKILL_PATCH_ENABLED` 门控模式（新增 `GA_SKILL_EVOLUTION_ENABLED`）。自动回路只能动 `author=agent 且 evolvable!=false` 的技能。
- **D5 (trigger)**：事件驱动——`agent_after` 任务完成钩子（复杂度门控）为主，信号触发（不满意/错误，v1.5）为辅，`do_start_long_term_update` 手动入口统一。不用 schedule。
- **D6 (验收/DoD)**：两层——L1 机制正确性（pytest T1–T10，gate）+ L2 有效性（fitness）。fitness 默认 (A) 负信号递减 + (D) Brief 接受率 + 1 个人工端到端 demo。per-skill fitness 落到现有 `memory/skill_exp_<name>.md`（驱动 L1 hot/cold，复用）。
- **D7 (fitness 主信号 = Q4)**：(A)+(D)+人工 demo，采纳推荐。
- **D8 (自主写入边界 = Q3)**：(i) agent 自有 patch 全自主落盘 + (iii) 熔断（单次改≥3 技能、或单技能连续自动 patch≥5 次无人工修正 → 强制 Brief）。

## Open (grilling 进行中)
- 设计分叉（Q3/Q4）已全部解决，spec 转 FINAL、可构建。下一步 = 实现接口已写入 spec。
- 待定（v2/v3）：fitness 评分（score_state 式）；FTS5 跨会话搜索；定时复盘（reflect/scheduler job）；replay 基准；per-skill scorer。
