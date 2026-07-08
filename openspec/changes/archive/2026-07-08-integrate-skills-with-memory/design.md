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

## Open Questions

- L1 Skills 段的自动/手动 marker 具体语法（`<!-- auto -->` 注释 vs 分段标题）——在 design phase Design Doc 中确定
- opt-in 版本备份的触发机制（Agent 自主判断 vs 用户显式指令）——在 design phase 中确定
