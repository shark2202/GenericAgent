## 1. 核心模块

- [x] 1.1 创建 `skill_loader.py`，实现 `get_skill_catalog() -> str`
  - 扫描 `$HOME/.agents/skills/` 和 `$CWD/.agents/skills/`
  - 查找每个子目录下的 `SKILL.md`
  - 解析 YAML frontmatter：提取 `name`（必需）和 `description`（可选）
  - 无 frontmatter 或无 `name` 的静默跳过
  - 同名 skill 项目级覆盖用户级
  - 目录不存在时静默跳过，不报错

- [x] 1.2 编写 catalog 格式化输出
  - 每行格式：`- **{name}**: {description} ({path})`
  - 无 skill 时返回空字符串
  - 有 skill 时以固定标题开头：`## Available Skills`

## 2. 集成到 agentmain.py

- [x] 2.1 在 `get_system_prompt()` 末尾追加 skill catalog
  - `import` skill_loader
  - 调用 `get_skill_catalog()` 并追加到 prompt 末尾

- [x] 2.2 验证 `file_read` 工具可直接加载 SKILL.md 路径
  - 确认 catalog 中的路径是绝对路径，`file_read` 可访问

## 3. 验证

<!-- review skipped: skill unavailable -->

- [x] 3.1 启动 GA，检查 system_prompt 是否包含 skill catalog
  - 预期：`$HOME/.agents/skills/` 下的 skill 出现在 prompt 中

- [x] 3.2 测试热加载：运行时新增/删除 skill 目录
  - 新增 skill → 下一轮 system_prompt 出现新 skill
  - 删除 skill → 下一轮 system_prompt 移除该 skill

- [x] 3.3 测试优先级覆盖
  - 创建同名 skill 在项目级和用户级，确认 catalog 中只有项目级版本

- [x] 3.4 测试边界情况
  - `.agents/skills/` 目录不存在 → 不报错
  - `SKILL.md` 无 frontmatter → 跳过
  - `SKILL.md` 有 frontmatter 但无 `name` → 跳过
