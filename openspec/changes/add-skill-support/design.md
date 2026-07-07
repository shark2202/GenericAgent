## Context

GenericAgent 当前的 agent 能力来自两个渠道：
1. **L3 记忆系统**：`memory/*.md` 的 SOP 文件，全量注入 system_prompt
2. **工具集**：`ga.py` 的 `do_*()` 方法

社区已形成以 SKILL.md（YAML frontmatter + markdown body）为标准的 skill 生态。本设计在不动 `agent_loop.py` 的前提下，通过 system_prompt 注入 + 现有 `file_read` 工具实现渐进式 skill 加载。

## Goals / Non-Goals

**Goals:**
- 扫描 `$HOME/.agents/skills/` 和 `$CWD/.agents/skills/`，自动发现 SKILL.md
- 解析 YAML frontmatter（name、description），忽略非法 skill
- 将 skill catalog（name + description + 路径）注入 system_prompt
- Agent 通过 `file_read` 按需加载完整 SKILL.md
- 热加载：每次 `get_system_prompt()` 重新扫描
- 同名 skill 项目级覆盖用户级

**Non-Goals:**
- 不执行 skill 附带的脚本
- 不提供 skill 商店/远程下载
- 不做 skill 依赖/版本管理
- 不做 skill 权限/沙箱
- 不替代 L3 SOP 机制

## Decisions

### D1: 注入点选在 `get_system_prompt()`

**选择**：在 `agentmain.py:153` 的 `sys_prompt` 拼接处追加 skill catalog。

**替代方案**：
- Hook 系统（`agent_before`）：过于间接，且 catalog 每轮都会变（热加载），hook 改 ctx 不够直观
- `extra_sys_prompts` 列表：可以用，但 `get_system_prompt()` 更内聚——catalog 和 global_memory 一样是 prompt 构建的固有部分

**理由**：`get_system_prompt()` 已经是 prompt 构建的中央函数（base + date + global_memory），skill catalog 是同一层次的 prompt 增强，放这里最自然。

### D2: 渐进式披露策略

**选择**：system_prompt 只注入 catalog（每条 skill 一行），不注入 body。Agent 需要时用 `file_read` 加载。

**理由**：
- L3 SOP 全量注入已经消耗大量 context。skill 数量可能很多（用户 `.agents/skills/` 已有 17 个），全量注入不可行
- `file_read` 是 GA 已有工具，无需新增
- 这符合 skill 生态的"渐进式披露"惯例

### D3: Skill 合法性判定

**选择**：只认文件名 `SKILL.md`，必须有 YAML frontmatter（`---` 开头），必须含 `name` 字段。任何不满足的静默跳过。

**理由**：最小信任边界。不要求 `description`（用空字符串），不校验 body 格式。这确保兼容性最大化，同时避免非 skill 的 markdown 文件被误识别。

### D4: 同名冲突策略

**选择**：项目级覆盖用户级。先扫用户级建索引，再扫项目级覆盖。

**理由**：符合"就近原则"——项目相关的 skill 优先级应高于全局。也符合用户指定的路径优先级（CWD > HOME）。

### D5: 热加载实现

**选择**：每次 `get_system_prompt()` 调用时重新扫描。不做缓存、不做文件监听。

**理由**：
- `get_system_prompt()` 在每轮任务开始前调用（`agentmain.py:153`）
- 扫描两个目录的开销极低（毫秒级）
- 不需要新增 watcher 线程或文件系统事件监听
- 简单可靠，无状态同步问题

### D6: 新增模块 `skill_loader.py`

**选择**：独立模块，暴露 `get_skill_catalog() -> str` 单一接口。

**理由**：
- 职责单一，易于测试
- `agentmain.py` 只需一行 `from skill_loader import get_skill_catalog`
- 未来如需扩展（MCP tool 注册、skill 脚本执行），在模块内扩展即可

## Risks / Trade-offs

- **Context 膨胀**：skill catalog 本身也占用 token。17 个 skill 的 catalog 约 500-800 tokens。风险低，远小于全量注入。若未来 skill 数量超过 50，考虑按目录分桶
- **SKILL.md 解析脆弱性**：手动解析 YAML frontmatter 可能遇到复杂 YAML 值（多行 description）。用简单的正则匹配 name/description 字段，不做完整 YAML 解析
- **目录不存在时无提示**：`.agents/skills/` 不存在时不应该报错——静默跳过是合理行为
