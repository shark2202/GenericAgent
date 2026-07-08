## 1. skill_loader.py 重写

- [x] 1.1 将 `get_skill_catalog()` 重写为 `sync_skills_to_l1()`：扫描 Skill 目录 → 生成 L1 `[Skills]` 段内容
- [x] 1.2 实现 L1 自动/手动段 marker 机制：自动同步段用 marker 标记，同步时只更新标记段，保留手动备注
- [x] 1.3 实现经验文件路径检测：扫描 `memory/skill_exp_<name>.md` 是否存在，存在则在 L1 条目中追加路径
- [x] 1.4 保留 Skill 发现核心逻辑（frontmatter 解析、项目级覆盖用户级、目录名 vs frontmatter name）
- [x] 1.5 移除 `get_skill_catalog()` 的 catalog 格式化输出逻辑

## 2. agentmain.py 改动

- [x] 2.1 移除 `agentmain.py:46` 的 `get_skill_catalog()` 调用
- [x] 2.2 在 `get_system_prompt()` 中调用 `sync_skills_to_l1()`（或集成到 `get_global_memory()` 流程中）
- [x] 2.3 确认 L1 Skills 段通过 `get_global_memory()` 的现有注入机制进入 system_prompt

## 3. L1 索引格式

- [x] 3.1 定义 `[Skills]` 段格式：每行 `<name>: <SKILL.md_path> | <experience_file_path>`
- [x] 3.2 定义自动/手动 marker 语法（如 `<!-- auto-skills-start -->` / `<!-- auto-skills-end -->`）
- [x] 3.3 首次运行迁移：将现有 Skill 同步到 L1 `[Skills]` 段

## 4. 工作记忆集成

- [x] 4.1 确认 `related_sop` 机制能自然追踪 Skill 路径（无需改动 ga.py，或最小改动）
- [x] 4.2 验证跨任务继承：新 handler 继承旧 handler 的 `key_info` 含 Skill 使用上下文

## 5. 自进化经验文件

- [x] 5.1 确认 `do_start_long_term_update` 无需改动——Agent 通过 L1 自然发现 Skill 经验文件路径
- [x] 5.2 验证 Agent 能通过标准 `file_patch` 写入 `memory/skill_exp_<name>.md`
- [x] 5.3 验证 L1 路由能同时指向 SKILL.md 和经验文件

## 6. 可选增强：版本备份

- [x] 6.1 实现 opt-in 版本备份机制：patch SKILL.md 前备份到 `SKILL.md.bak`
- [x] 6.2 添加用户确认门禁：版本备份操作需显式用户确认

## 7. 测试

- [x] 7.1 单元测试：`sync_skills_to_l1()` 正确扫描并格式化 L1 Skills 段
- [x] 7.2 单元测试：项目级 Skill 覆盖用户级 Skill
- [x] 7.3 单元测试：自动同步保留手动备注（marker 机制）
- [x] 7.4 单元测试：经验文件存在时 L1 条目含双路径，不存在时仅含 SKILL.md 路径
- [x] 7.5 集成测试：`get_system_prompt()` 输出中无 `## Available Skills` 段，Skill 路由走 L1
- [x] 7.6 集成测试：新 Skill 添加后，下次 `get_system_prompt()` 调用时 L1 `[Skills]` 段更新
- [x] 7.7 验证 token 预算：L1 Skills 段行数 ≤ Skill 数量，不随 description 长度膨胀
