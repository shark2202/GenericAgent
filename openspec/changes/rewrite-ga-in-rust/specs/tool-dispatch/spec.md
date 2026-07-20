## MODIFIED Requirements

### Requirement: Self-registration without handler modification

**BREAKING（rewrite-ga-in-rust）**：drop-in `tools/*.py` 自注册模型在 Rust 引擎中变更为 MCP server 协议。外部用户工具 SHALL 作为 MCP server 经 `tools/list`+`tools/call` 协议发现与调用，核心工具（file/code/web/mcp + skill_manage）SHALL 编译期注册。此修改仅影响 Rust 引擎；Python 版 drop-in 自注册保留（双轨共存）。

#### Scenario: 外部工具作为 MCP server
- **WHEN** Rust 引擎启动时扫描 tools/ 目录
- **THEN** 发现的外部 MCP server（stdio）经 rmcp `tools/list` 注册其工具到 ExternalTool 表，LLM 可经 `tools/call` 调用，无需重编译引擎

#### Scenario: 现有 drop-in .py 工具迁移
- **WHEN** 现有 `tools/skill_manage.py` 等 drop-in 工具迁到 Rust 引擎
- **THEN** `skill_manage` 因调引擎内部 distill/skill_loader 归 EngineCap 编译期注册（非外部 MCP）；其它纯工具改写为 stdio MCP server 脚本

### Requirement: Dual-track dispatch with method priority

**MODIFIED（rewrite-ga-in-rust）**：Rust 引擎双表 dispatch 改为 EngineCap（持 &Engine，对应原 method-track）优先 → ExternalTool（含 MCP，对应原 registry-track）→ 未知工具。Rust 引擎 SHALL dispatch 工具调用时先查 EngineCap 表，未命中再查 ExternalTool 表，仍未命中则按未知工具处理；hook（tool_before/tool_after）SHALL 两层包裹所有 dispatch 路径。此修改仅影响 Rust 引擎，Python 版 method-track/registry-track dual dispatch 保留（双轨共存）。

#### Scenario: EngineCap takes precedence over ExternalTool on name collision
- **WHEN** EngineCap 表与 ExternalTool 表都有同名工具 `<name>`
- **THEN** 引擎 SHALL dispatch EngineCap（优先），SHALL NOT 调用 ExternalTool

#### Scenario: ExternalTool fallback when no EngineCap exists
- **WHEN** EngineCap 表无 `<name>` 但 ExternalTool 表有
- **THEN** 引擎 SHALL dispatch ExternalTool（含 McpTool wrapper 走 rmcp tools/call）
