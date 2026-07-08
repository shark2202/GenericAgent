# Comet Design Handoff

- Change: integrate-skills-with-memory
- Phase: design
- Mode: compact
- Context hash: a2ad6b56eb56d9f139c4d54bdc2a760a0ae0f2f793c5abf3e9288052b78d5f0c

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/integrate-skills-with-memory/proposal.md

- Source: openspec/changes/integrate-skills-with-memory/proposal.md
- Lines: 1-29
- SHA256: 3b1bfe562c535de7d3c086bb228eb7f7c77f3e7ed01a7bc6cebd003cfc110607

```md
## Why

当前 Skill 支持（`add-skill-support` change 产物）将 Skill catalog 作为 flat list 全量追加到 system_prompt 末尾，完全旁路了项目的 L1-L4 记忆 + 自进化架构。Skill 不在 L1 索引中注册、不参与自进化闭环、无使用经验回写、token 随 Skill 数量线性膨胀。这导致 Skill 成为一个割裂的平行知识体系，无法享受项目核心的"知识-行动闭环"复利效应。

## What Changes

- **BREAKING**: 移除 `get_skill_catalog()` 对 system_prompt 的全量注入（`agentmain.py:46`），改为通过 L1 索引层路由
- `skill_loader.py` 职责变更：从"格式化 catalog 字符串"改为"扫描 Skill → 同步到 L1 索引"（自动同步 + Agent 可微调）
- L1 索引（`global_mem_insight.txt`）新增 Skills 段：结构化多行，每个 Skill 一行含路径指针
- 新增 Skill 经验文件机制：Agent 用 Skill 后，经验写入 `memory/skill_exp_<name>.md`（L3），L1 单条目同时指向 SKILL.md 和经验文件
- 工作记忆 `related_sop` 复用：Skill 使用记录跨任务继承（`agentmain.py:159-163` 机制）
- 可选增强：opt-in 模式下，Agent 发现 SKILL.md 事实错误 → 备份原版 → patch 修正

## Capabilities

### New Capabilities
- `skill-memory-integration`: Skill 作为 L3 独立子类接入 L1-L4 记忆体系，含 L1 路由、自进化经验回写、工作记忆追踪

### Modified Capabilities
- `skill-discovery`: Skill 发现结果不再全量注入 system_prompt，改为同步到 L1 索引；热加载从"每轮 re-scan + inject"变为"同步到 L1"

## Impact

- `skill_loader.py`：核心重写，从 catalog 格式化改为 L1 同步
- `agentmain.py`：移除 `get_skill_catalog()` 调用（line 46）
- `ga.py`：工作记忆 `related_sop` 机制复用（可能需微调以识别 Skill 路径）
- `memory/global_mem_insight.txt`：新增 Skills 段格式
- `memory/skill_exp_*.md`：新增经验文件类别
- 不影响 `agent_loop.py`、`frontends/`、LLM session 管理

```

## openspec/changes/integrate-skills-with-memory/design.md

- Source: openspec/changes/integrate-skills-with-memory/design.md
- Lines: 1-85
- SHA256: 5dff5b8eed5eb1df04b81895defa3ac8d1bd245cdbbe0125a33180685e2356a9

[TRUNCATED]

```md
## Context

当前项目 GA 的核心架构是 L1-L4 记忆 + 自进化闭环：
- L1（`global_mem_insight.txt`，≤30 行）每轮注入，作为能力路由表
- L3（`memory/*.md` + `*.py`）按需 `file_read`，Agent 通过 `do_start_long_term_update` 自进化写入
- 闭环：任务执行 → L1 路由 → L3 SOP 指导 → 验证 → 经验回写 L3 → L1 更新指针

现有 Skill 支持（`skill_loader.py` + `agentmain.py:46`）将 Skill catalog 全量追加到 system_prompt 末尾，完全旁路了上述架构。Skill 不在 L1 中注册、不可自进化、无使用追踪、token 线性膨胀。

## Goals / Non-Goals

**Goals:**
- Skill 作为 L3 独立子类接入 L1 路由（结构化多行指针，含 SKILL.md 路径 + 经验文件路径）
- `skill_loader.py` 自动同步 Skill 到 L1（启动时 + 热加载），Agent 可通过自进化微调
- Skill 使用经验通过标准自进化路径回写 `memory/skill_exp_<name>.md`
- 工作记忆 `related_sop` 复用，Skill 使用跨任务继承
- Token 预算对齐 L1 哲学（指针式，不随 Skill 数量膨胀）

**Non-Goals:**
- 不重新设计 L1-L4 记忆系统本身
- 不改变 `.agents/skills/` 目录结构
- 不修改 `agent_loop.py`（引擎层保持无关）
- 不构建 Skill 市场/远程分发

## Decisions

### D1: Skill 作为 L3 独立子类（非合并到 L3 SOP）

**选择**：独立子类，L1 新增 Skills 段
**理由**：信任边界不同——L3 SOP 由 Agent 自进化写入（L0 公理保证），Skill 由外部作者提供（skill pack 安装）。合并会混淆信任模型，且 Skill 可被外部更新（git pull），Agent 直接 patch 会冲突。
**替代方案**：合并为 L3 同类别——被否决，因信任边界和更新源不同。

### D2: L1 Skills 段格式——结构化多行

**选择**：每个 Skill 一行，含 name + SKILL.md 路径 + 经验文件路径（如有）
**格式示例**：
```
[Skills]
comet-open: /path/to/SKILL.md | memory/skill_exp_comet-open.md
comet-design: /path/to/SKILL.md
```
**理由**：与 L1 现有的 `浏览器特殊操作:` / `键鼠:` 段风格一致；单行含双路径，Agent 一眼看到 Skill 和经验文件。

### D3: get_skill_catalog() 完全移除

**选择**：移除 `agentmain.py:46` 的 `get_skill_catalog()` 调用，Skill 路由完全走 L1
**理由**：双通道（L1 + catalog）会混淆 Agent 的知识来源，且 catalog 全量注入违背 L1 ≤30 行哲学。
**替代方案**：保留精简 catalog 作为补充——被否决，因双通道引入一致性维护负担。

### D4: 自进化策略——经验文件为主 + 版本备份 opt-in

**选择**：默认写 `memory/skill_exp_<name>.md`（L3 经验），不碰 SKILL.md；opt-in 模式下可备份+patch SKILL.md
**理由**：经验文件在 Agent 自己的 `memory/` 领地，符合 L0 公理（行动验证、最小局部修改）；不碰外部产物避免冲突。opt-in 版本备份处理 Skill 本身的事实错误。
**替代方案**：纯经验文件（方案 B）——被否决，因无法修正 Skill 本身的错误。纯版本备份（方案 A）——被否决，因外部更新冲突风险。

### D5: L1 自动同步 + Agent 可微调

**选择**：`skill_loader.py` 启动时扫描 Skill → 同步到 L1 Skills 段；Agent 通过自进化可微调 L1 中的 Skill 指针（如添加使用备注）
**理由**：新 Skill 安装后 Agent 不需要手动发现；但 Agent 可能积累"Skill X 适合场景 Y"的经验，需要能微调 L1 指针。
**同步规则**：以 Skill 文件系统为 source of truth，同步时保留 Agent 添加的备注（用 marker 区分自动生成段和手动备注）。

### D6: do_start_long_term_update 不改动

**选择**：不修改该工具，Agent 通过 L1 自然发现 Skill 经验文件路径
**理由**：L1 已包含 Skill 经验文件指针，Agent 调 `start_long_term_update` 后按 L0 规则更新 L1/L2/L3，Skill 经验文件属于 L3，自然覆盖。

## Risks / Trade-offs

- **[L1 同步冲突]** 自动同步可能覆盖 Agent 手动微调的 L1 Skills 段 → 用 marker 区分自动段和手动段，同步只更新自动段
- **[L1 膨胀]** Skill 数量多时 L1 Skills 段可能超预算 → 每行极简（name + 路径），50 skills ≈ 50 行，可通过 L1 两层映射策略（高频全路径，低频仅 name）缓解
- **[迁移]** 现有 `get_skill_catalog()` 调用移除后，依赖 catalog 的行为变化 → 一次性迁移：首次运行时 `skill_loader.py` 将现有 Skill 同步到 L1
- **[经验文件命名冲突]** `skill_exp_<name>.md` 可能与现有 `memory/` 文件冲突 → Skill name 是 kebab-case，`skill_exp_` 前缀避免冲突

## Migration Plan

1. `skill_loader.py` 重写：`get_skill_catalog()` → `sync_skills_to_l1()`
2. `agentmain.py:46` 移除 `get_skill_catalog()` 调用
3. 首次运行时 `sync_skills_to_l1()` 扫描现有 Skill → 写入 L1 Skills 段
4. Agent 后续通过自进化自然积累 `memory/skill_exp_*.md`
5. 回滚：恢复 `agentmain.py:46` 的 `get_skill_catalog()` 调用 + 旧 `skill_loader.py`

```

Full source: openspec/changes/integrate-skills-with-memory/design.md

## openspec/changes/integrate-skills-with-memory/tasks.md

- Source: openspec/changes/integrate-skills-with-memory/tasks.md
- Lines: 1-45
- SHA256: 2086015f9530b53d67de25ebc25e489cc84bfedd62e81956efbffba56f753195

```md
## 1. skill_loader.py 重写

- [ ] 1.1 将 `get_skill_catalog()` 重写为 `sync_skills_to_l1()`：扫描 Skill 目录 → 生成 L1 `[Skills]` 段内容
- [ ] 1.2 实现 L1 自动/手动段 marker 机制：自动同步段用 marker 标记，同步时只更新标记段，保留手动备注
- [ ] 1.3 实现经验文件路径检测：扫描 `memory/skill_exp_<name>.md` 是否存在，存在则在 L1 条目中追加路径
- [ ] 1.4 保留 Skill 发现核心逻辑（frontmatter 解析、项目级覆盖用户级、目录名 vs frontmatter name）
- [ ] 1.5 移除 `get_skill_catalog()` 的 catalog 格式化输出逻辑

## 2. agentmain.py 改动

- [ ] 2.1 移除 `agentmain.py:46` 的 `get_skill_catalog()` 调用
- [ ] 2.2 在 `get_system_prompt()` 中调用 `sync_skills_to_l1()`（或集成到 `get_global_memory()` 流程中）
- [ ] 2.3 确认 L1 Skills 段通过 `get_global_memory()` 的现有注入机制进入 system_prompt

## 3. L1 索引格式

- [ ] 3.1 定义 `[Skills]` 段格式：每行 `<name>: <SKILL.md_path> | <experience_file_path>`
- [ ] 3.2 定义自动/手动 marker 语法（如 `<!-- auto-skills-start -->` / `<!-- auto-skills-end -->`）
- [ ] 3.3 首次运行迁移：将现有 Skill 同步到 L1 `[Skills]` 段

## 4. 工作记忆集成

- [ ] 4.1 确认 `related_sop` 机制能自然追踪 Skill 路径（无需改动 ga.py，或最小改动）
- [ ] 4.2 验证跨任务继承：新 handler 继承旧 handler 的 `key_info` 含 Skill 使用上下文

## 5. 自进化经验文件

- [ ] 5.1 确认 `do_start_long_term_update` 无需改动——Agent 通过 L1 自然发现 Skill 经验文件路径
- [ ] 5.2 验证 Agent 能通过标准 `file_patch` 写入 `memory/skill_exp_<name>.md`
- [ ] 5.3 验证 L1 路由能同时指向 SKILL.md 和经验文件

## 6. 可选增强：版本备份

- [ ] 6.1 实现 opt-in 版本备份机制：patch SKILL.md 前备份到 `SKILL.md.bak`
- [ ] 6.2 添加用户确认门禁：版本备份操作需显式用户确认

## 7. 测试

- [ ] 7.1 单元测试：`sync_skills_to_l1()` 正确扫描并格式化 L1 Skills 段
- [ ] 7.2 单元测试：项目级 Skill 覆盖用户级 Skill
- [ ] 7.3 单元测试：自动同步保留手动备注（marker 机制）
- [ ] 7.4 单元测试：经验文件存在时 L1 条目含双路径，不存在时仅含 SKILL.md 路径
- [ ] 7.5 集成测试：`get_system_prompt()` 输出中无 `## Available Skills` 段，Skill 路由走 L1
- [ ] 7.6 集成测试：新 Skill 添加后，下次 `get_system_prompt()` 调用时 L1 `[Skills]` 段更新
- [ ] 7.7 验证 token 预算：L1 Skills 段行数 ≤ Skill 数量，不随 description 长度膨胀

```

## openspec/changes/integrate-skills-with-memory/specs/skill-discovery/spec.md

- Source: openspec/changes/integrate-skills-with-memory/specs/skill-discovery/spec.md
- Lines: 1-73
- SHA256: 0e59da0957d4c123458ae4ccaa8f098e250129511c9ee70efd96ab7b4dcecbfd

```md
## MODIFIED Requirements

### Requirement: Skill Catalog Discovery

The system SHALL scan `$HOME/.agents/skills/` and `$CWD/.agents/skills/` for directories containing a `SKILL.md` file, parse their YAML frontmatter, and build a catalog of available skills. The discovered skills SHALL be synced to the L1 index `[Skills]` section instead of being formatted as a catalog string for system prompt injection.

#### Scenario: Skills directory exists with valid skills
- **WHEN** `$HOME/.agents/skills/` contains directories with `SKILL.md` files that have valid YAML frontmatter with `name` field
- **THEN** each valid skill is synced to the L1 `[Skills]` section with its name, description, and absolute path

#### Scenario: Skills directory does not exist
- **WHEN** `$HOME/.agents/skills/` or `$CWD/.agents/skills/` does not exist
- **THEN** the directory is silently skipped, no error is raised

#### Scenario: SKILL.md without valid frontmatter
- **WHEN** a `SKILL.md` file has no YAML frontmatter or no `name` field
- **THEN** the skill is silently excluded from the L1 sync

#### Scenario: Non-SKILL.md files in skill directories
- **WHEN** a skill directory contains files other than `SKILL.md`
- **THEN** those files are ignored during discovery

#### Scenario: Directory name differs from frontmatter name
- **WHEN** a skill directory is named `foo` but its `SKILL.md` frontmatter has `name: bar`
- **THEN** the L1 entry SHALL use `bar` as the skill identifier, and the directory name `foo` SHALL only be used for conflict resolution (project-over-user override)

### Requirement: Skill Catalog Injection into System Prompt

**REMOVED**: The system SHALL NO LONGER append the skill catalog to the system prompt as a flat list. Skill discovery results are routed exclusively through the L1 index `[Skills]` section, which is already injected into the system prompt via `get_global_memory()`.

#### Scenario: No skill catalog appended to system prompt
- **WHEN** `get_system_prompt()` is called
- **THEN** no `## Available Skills` section is appended; skill routing is handled by L1 `[Skills]` section within `get_global_memory()` output

### Requirement: Agent On-Demand Skill Loading

The agent SHALL be able to read the full content of a skill's `SKILL.md` file using the existing `file_read` tool, using the path provided in the L1 `[Skills]` section.

#### Scenario: Agent loads a skill by path from L1
- **WHEN** the agent reads L1, finds a skill entry, and invokes `file_read` with the SKILL.md path
- **THEN** the full `SKILL.md` content is returned, including frontmatter and body

#### Scenario: Agent follows skill instructions
- **WHEN** the agent reads a `SKILL.md` that contains actionable instructions
- **THEN** the agent processes the markdown and executes the instructions using its available tools

#### Scenario: Agent reads skill experience file
- **WHEN** the L1 skill entry includes an experience file path and the agent invokes `file_read` with that path
- **THEN** the experience file content is returned, containing past learnings about using this skill

### Requirement: Hot Reloading

The system SHALL re-scan skill directories on every `get_system_prompt()` call and sync changes to the L1 `[Skills]` section, so that adding or removing skills takes effect without restarting the agent.

#### Scenario: New skill added at runtime
- **WHEN** a new `SKILL.md` is added to a scanned directory while the agent is running
- **THEN** on the next `get_system_prompt()` call, the new skill is synced to the L1 `[Skills]` section

#### Scenario: Skill removed at runtime
- **WHEN** a previously-available `SKILL.md` is deleted while the agent is running
- **THEN** on the next `get_system_prompt()` call, the skill entry is removed from the L1 `[Skills]` auto-synced section

### Requirement: Skill Priority (Project over User)

The system SHALL resolve name conflicts between project-level (`$CWD/.agents/skills/`) and user-level (`$HOME/.agents/skills/`) skills by preferring the project-level version. The L1 `[Skills]` section SHALL reflect the resolved path.

#### Scenario: Same skill name in both paths
- **WHEN** `$CWD/.agents/skills/foo/SKILL.md` and `$HOME/.agents/skills/foo/SKILL.md` both exist with name "foo"
- **THEN** the L1 `[Skills]` section includes only the project-level version with its path to `$CWD/.agents/skills/foo/SKILL.md`

#### Scenario: Different skill names in both paths
- **WHEN** project and user paths contain skills with different names
- **THEN** all skills are included in the L1 `[Skills]` section

```

## openspec/changes/integrate-skills-with-memory/specs/skill-memory-integration/spec.md

- Source: openspec/changes/integrate-skills-with-memory/specs/skill-memory-integration/spec.md
- Lines: 1-77
- SHA256: 85c13a07b726feb4e848c9419a49662247752b65b8cd361503b9f42286af7a85

```md
## ADDED Requirements

### Requirement: Skill as L3 Subcategory with L1 Routing

Skill SHALL be treated as an independent L3 subcategory, distinct from Agent-authored SOPs. L1 index (`global_mem_insight.txt`) SHALL include a `[Skills]` section with structured multi-line entries, one per skill, containing the skill name, SKILL.md absolute path, and optional experience file path.

#### Scenario: L1 Skills section format
- **WHEN** skills are discovered and synced to L1
- **THEN** L1 contains a `[Skills]` section where each line follows the format: `<name>: <SKILL.md_path> | <experience_file_path>` (experience file path omitted if no experience file exists)

#### Scenario: Agent discovers skill via L1
- **WHEN** the agent reads L1 and finds a skill entry
- **THEN** the agent can `file_read` the SKILL.md path to load the full skill content

#### Scenario: Skill entry with experience file
- **WHEN** a skill has a corresponding `memory/skill_exp_<name>.md` file
- **THEN** the L1 entry includes both the SKILL.md path and the experience file path, separated by ` | `

#### Scenario: Skill entry without experience file
- **WHEN** a skill has no corresponding experience file
- **THEN** the L1 entry includes only the SKILL.md path, with no trailing ` | `

### Requirement: Skill Experience Files

The system SHALL support skill experience files at `memory/skill_exp_<name>.md` as L3 knowledge. These files are written by the agent through the standard self-evolution mechanism (`do_start_long_term_update` + `file_patch`), recording action-validated learnings from using a skill.

#### Scenario: Agent writes skill experience after usage
- **WHEN** the agent uses a skill and determines there are valuable learnings to record
- **THEN** the agent calls `start_long_term_update`, follows L0 rules, and writes experience to `memory/skill_exp_<name>.md`

#### Scenario: Experience file influences future tasks
- **WHEN** a future task is similar to a previous task that used a skill
- **THEN** L1 routes the agent to both the SKILL.md and the experience file, allowing the agent to read past learnings before executing

#### Scenario: Experience file naming convention
- **WHEN** a skill named `comet-open` has experience recorded
- **THEN** the experience file is named `memory/skill_exp_comet-open.md`

### Requirement: Skill Usage Tracking in Working Memory

The system SHALL track skill usage in the working memory `related_sop` field, enabling cross-task inheritance through the existing `agentmain.py:159-163` mechanism.

#### Scenario: Skill usage recorded in related_sop
- **WHEN** the agent uses a skill during a task
- **THEN** the skill path is recorded in `working['related_sop']`

#### Scenario: Skill usage inherited across tasks
- **WHEN** a new task starts and the previous handler had skill paths in `related_sop`
- **THEN** the new handler inherits the `key_info` containing skill usage context, following the existing cross-task inheritance mechanism

### Requirement: L1 Auto-Sync with Manual Override

The system SHALL auto-sync discovered skills to the L1 `[Skills]` section on startup and on directory change. Auto-synced entries SHALL be marked to distinguish from agent-manual annotations. The agent MAY add manual annotations (e.g., usage notes) to L1 skill entries through self-evolution; auto-sync SHALL preserve manual annotations.

#### Scenario: New skill auto-synced to L1
- **WHEN** a new SKILL.md is added to a scanned directory and the agent starts or `get_system_prompt()` is called
- **THEN** the new skill appears in the L1 `[Skills]` section on the next sync

#### Scenario: Removed skill auto-removed from L1
- **WHEN** a SKILL.md is deleted from a scanned directory
- **THEN** on the next sync, the corresponding entry is removed from the L1 `[Skills]` auto-synced section

#### Scenario: Manual annotations preserved during sync
- **WHEN** the agent has added manual annotations to a skill entry in L1, and auto-sync runs
- **THEN** the manual annotations are preserved; only the auto-synced portion (path, name) is updated

### Requirement: Optional Skill Version Backup

The system SHALL support an opt-in mechanism for the agent to fix factual errors in SKILL.md by backing up the original version before patching. This mechanism requires explicit user confirmation before execution.

#### Scenario: Agent discovers factual error in SKILL.md
- **WHEN** the agent identifies a factual error in a SKILL.md and the opt-in mode is enabled
- **THEN** the system backs up the original SKILL.md to `SKILL.md.bak` (or versioned backup) before applying the patch

#### Scenario: Opt-in mode disabled by default
- **WHEN** the opt-in mode is not explicitly enabled
- **THEN** the agent writes experience notes to `memory/skill_exp_<name>.md` instead of modifying SKILL.md directly

```
