## Why

GenericAgent 当前的能力扩展完全依赖自进化 L3 记忆系统（agent 自行总结 SOP markdown）。社区已有海量经过验证的 AGENT SKILL（SKILL.md 格式），无法直接复用。增加标准 skill 加载能力后，GA 可以零成本接入 opcteam-code、Claude Code 等生态的 skill 库，大幅降低能力扩展门槛。

## What Changes

- 新增 `skill_loader.py`：扫描指定目录，解析 SKILL.md 的 YAML frontmatter，构建 skill catalog
- 修改 `agentmain.py` 的 `get_system_prompt()`：在 system_prompt 末尾注入 skill catalog（渐进式披露：仅 name + description + 路径）
- Agent 通过现有 `file_read` 工具按需加载完整 SKILL.md 内容
- 支持热加载：每次 `get_system_prompt()` 调用时重新扫描，运行时可感知 skill 目录变化
- 加载路径优先级：`$CWD/.agents/skills/`（项目级）> `$HOME/.agents/skills/`（用户级），同名 skill 项目级覆盖

## Capabilities

### New Capabilities

- `skill-discovery`: 扫描 `$HOME/.agents/skills/` 和 `$CWD/.agents/skills/`，解析 SKILL.md 的 YAML frontmatter（name、description），构建 skill catalog 并注入 system_prompt

### Modified Capabilities

<!-- No existing specs to modify -->

## Impact

- 影响文件：`agentmain.py`（`get_system_prompt()` 注入点）、新增 `skill_loader.py`
- 不影响：`agent_loop.py`（循环引擎）、`ga.py`（工具集）、`memory/`（L3 记忆系统）、现有 reflect 脚本
- 依赖：无新外部依赖，纯标准库实现（`os`、`glob`、`yaml` 或手动解析 frontmatter）
