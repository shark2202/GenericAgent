# Brainstorm Summary

- Change: add-skill-support
- Date: 2026-07-06

## 确认的技术方案

`skill_loader.py`（独立模块，正则解析 SKILL.md frontmatter）→ `agentmain.py` 的 `get_system_prompt()` 末尾注入 catalog → agent 通过现有 `file_read` 工具按需加载完整 SKILL.md。热加载：每次 `get_system_prompt()` 重新扫描，无缓存。同名 skill 项目级覆盖用户级。

## 关键取舍与风险

- 正则解析而非 yaml 库：零依赖 > 健壮性（SKILL.md frontmatter 极简，够用）
- Catalog 放 prompt 末尾：参考信息定位，attention 权重问题在此场景不关键
- Description 不截断：17 个 skill 约 500-800 tokens，可接受
- Catalog 用 frontmatter `name` 字段作为标识符，目录名仅用于覆盖判定
- 风险：未来 skill 超 50 时 token 膨胀 → 加 `max_skills` 配置

## 测试策略

- 单元：临时目录构造场景（空目录、合法/非法 SKILL.md、优先级覆盖）
- 集成：启动 GA 检查 system_prompt 末尾
- 端到端：agent 用 comet skill 完成一个实际操作
- 边界：目录不存在、空 frontmatter、无 name 字段

## Spec Patch

补充场景：目录名 ≠ frontmatter name 时的行为——catalog 用 frontmatter `name` 作为标识符，目录名仅用于冲突覆盖。
