## Why

GenericAgent 的核心护城河是"记忆（L1-L4）+ 自进化（任务→Skill 结晶）"，但当前工具系统是封闭的 9 个原子工具，无法接入外部生态。MCP（Model Context Protocol）已成为 AI 工具互连的事实标准。增加 MCP Client 能力，让 GenericAgent 能连接任意 MCP Server、调用其工具，并与现有记忆/自进化系统深度融合——既扩展能力边界，又不破坏最小架构哲学。

## What Changes

- 新增 MCP Client 核心库（`mcp_client.py`）：基于官方 `mcp` Python SDK，支持 stdio + SSE/Streamable HTTP 双传输
- 新增 `mcp_call` 工具（`do_mcp_call` in `ga.py` + `tools_schema.json`）：LLM 通过该工具调用 MCP Server 的指定工具
- 新增 `.ga/mcp_servers.json` 配置文件：声明 MCP Server 连接信息，支持热更新（文件变化时增量重载，无效 JSON 不破坏现有连接）
- 新增 Server 生命周期管理：启动连接、健康监控（心跳）、故障自动重连、优雅降级
- 新增 `ga mcp` CLI 子命令族：`list`（列出 Server 及健康状态）、`start`/`stop`/`restart`（启停单个 Server）
- 新增数据闭环：调用审计日志（server/tool/参数/结果摘要/耗时）、Schema 变更感知、错误结构化收集
- **修改** `skill-discovery` 能力：MCP Server 工具列表注入 `get_skill_catalog()` → system prompt，与 Skill 索引同级；热更新触发 L1 索引刷新
- **修改** 记忆/自进化系统：`mcp_call` 结果可触发 L2 记忆蒸馏；使用 MCP 工具完成的任务，结晶的 Skill 自动标注 MCP 依赖

## Capabilities

### New Capabilities
- `mcp-client`: MCP Client 核心能力——连接外部 MCP Server、发现工具、调用工具、生命周期管理（健康监控/故障恢复/热更新/启停控制）、调用审计与错误收集

### Modified Capabilities
- `skill-discovery`: 技能发现能力扩展——MCP Server 工具列表注入 system prompt（与 Skill 索引同级），热更新触发索引刷新，MCP 调用产出 L2 记忆 / L3 自进化 Skill（标注 MCP 依赖）

## Impact

- **新增文件**：`mcp_client.py`（核心库）、`.ga/mcp_servers.json`（配置）、`tests/test_mcp_client.py`（TDD 测试）
- **修改文件**：`ga.py`（新增 `do_mcp_call`）、`assets/tools_schema.json`（新增 `mcp_call` 工具定义）、`agentmain.py`（启动时初始化 MCP Client）、`ga_cli/cli.py`（新增 `mcp` 子命令）、`skill_loader.py`（`get_skill_catalog()` 感知 MCP 工具）
- **新增依赖**：`mcp` Python SDK（`pip install mcp`）
- **记忆系统**：L1 索引刷新机制、L2 蒸馏 hook、L3 Skill 结晶标注 MCP 依赖
- **Agent Loop**：system prompt 每轮反映 MCP 工具增减
