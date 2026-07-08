# Brainstorm Summary

- Change: integrate-skills-with-memory
- Date: 2026-07-08

## Confirmed Technical Approach

Skill 作为 L3 独立子类接入 L1-L4 记忆体系。核心实现：

1. `skill_loader.py` 重写：`get_skill_catalog()` → `sync_skills_to_l1()`
2. L1 新增 `[Skills]` 段，用 HTML 注释 marker（`<!-- auto-skills-start -->` / `<!-- auto-skills-end -->`）分隔自动同步区和手动备注区
3. sync 集成到 `get_global_memory()`（ga.py:583），每次构建 system_prompt 时先 sync 再读 L1
4. 经验文件 `memory/skill_exp_<name>.md` 通过标准自进化路径写入
5. `get_skill_catalog()` 调用从 `agentmain.py:46` 移除
6. opt-in 版本备份通过 `GA_SKILL_PATCH_ENABLED=1` 环境变量控制

## Key Trade-offs and Risks

- **marker 引入新语法**：L1 目前无 HTML 注释，但 Agent 语义透明，可接受
- **L1 膨胀**：50 skills ≈ 50 行，可通过 L1 两层映射策略缓解
- **sync 性能**：每次 `get_global_memory()` 调用都扫描，但 Skill 数量少时开销毫秒级
- **经验文件命名冲突**：`skill_exp_` 前缀 + kebab-case name 避免冲突

## Testing Strategy

- 单元测试：`sync_skills_to_l1()` 正确扫描、格式化、marker 机制
- 集成测试：`get_system_prompt()` 输出无 `## Available Skills` 段
- 集成测试：新 Skill 添加后 L1 `[Skills]` 段更新
- token 预算验证：L1 Skills 段行数 ≤ Skill 数量

## Spec Patches

None — Open 阶段 delta spec 已覆盖所有需求场景。
