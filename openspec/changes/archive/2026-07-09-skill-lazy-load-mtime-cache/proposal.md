# Proposal: Skill 懒加载 + mtime 缓存

## 问题

GenericAgent 的 skill 加载机制存在两个缺陷：

1. **无懒加载**：`sync_skills_to_l1()` 每次全量扫描所有 skill 目录、解析所有 SKILL.md frontmatter、把全部名字写入 L1。但 L1 段只存 skill 名字（cold skills 逗号列表，无 description），AI 只能靠名字猜功能，然后 file_read 全文了解内容。没有"按需取 skill 摘要"的接口。

2. **无 mtime 缓存**：每个新 user query 重建 sys_prompt 时都会全量 rescan（`agentmain.py:172`→`ga.py:605`→`sync_skills_to_l1`）。47 个 skill 目录的 listdir + 47 个 SKILL.md 的 open + 47 次 frontmatter 解析，即使 skill 无任何变更也重复执行。

## 根因

- `skill_loader.py` 只有 `_discover_skills`/`sync_skills_to_l1`/`backup_and_patch_skill`，无 `get_skill_detail` 类懒加载函数，无 search 函数。
- `_discover_skills`/`sync_skills_to_l1` 均无缓存，每次调用全量扫描。
- L1 段 cold skills 只存名字（token 预算设计，`test_token_budget` 验证），AI 无法从 L1 判断 skill 相关性。

## 修复目标

1. **懒加载**：新增 `get_skill_detail(name)` 工具，AI 按需获取单个 skill 的 frontmatter 摘要（name/description/可选 license 等）+ 资源目录概览（has_scripts/references/assets）+ SKILL.md 路径。对齐 agentskills.io spec 的 progressive disclosure 三层模型。
2. **mtime 缓存**：新增 `_get_skills_catalog()` 模块级缓存，检查 skill 根目录 + SKILL.md 的 mtime，未变则复用缓存 catalog 跳过 rescan，变了才全量 rescan。`sync_skills_to_l1` 和 `get_skill_detail` 共享缓存。
