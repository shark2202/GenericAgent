# mcp-client Specification

## Purpose

定义 GenericAgent MCP Client 的核心行为——连接 MCP Server、发现工具、调用工具、生命周期管理、可观测性。

## Requirements

### Requirement: MCP Server Configuration Loading

系统 SHALL 从项目根目录的 `.ga/mcp_servers.json` 加载 MCP Server 配置。每个 Server 配置 SHALL 包含 `name`（唯一标识）、`transport`（`stdio` 或 `sse`）、以及传输相关的连接参数。系统 SHALL 在 Agent 启动时解析配置，并建立到所有已配置 Server 的连接。

#### Scenario: 配置文件存在且格式正确

- **WHEN** `.ga/mcp_servers.json` 存在且包含有效的 JSON，包含至少一个 MCP Server 的完整配置
- **THEN** 系统解析所有 Server 配置，并为每个 Server 建立连接

#### Scenario: 配置文件不存在

- **WHEN** `.ga/mcp_servers.json` 不存在
- **THEN** 系统静默跳过，不报错，不阻断 Agent 启动

#### Scenario: 配置文件 JSON 格式错误

- **WHEN** `.ga/mcp_servers.json` 包含无效的 JSON
- **THEN** 系统记录错误日志，不使用任何 MCP Server，不阻断 Agent 启动

### Requirement: stdio Transport Connection

系统 SHALL 支持通过 stdio 传输协议连接 MCP Server。配置中 `transport: "stdio"` 时，系统 SHALL 以子进程方式启动 Server（使用 `command` 和 `args` 字段），通过标准输入/输出进行 JSON-RPC 通信。

#### Scenario: stdio Server 启动成功

- **WHEN** 配置包含 `{"name": "my-server", "transport": "stdio", "command": "node", "args": ["server.js"]}`，且目标进程能正常启动
- **THEN** 系统建立 stdio 连接，完成 MCP 握手，Server 状态变为 `connected`

#### Scenario: stdio Server 启动失败

- **WHEN** 配置中的 `command` 不存在或启动失败
- **THEN** Server 状态为 `error`，记录错误日志（含原因），不影响其他 Server

#### Scenario: stdio Server 进程意外退出

- **WHEN** 已连接的 stdio Server 进程异常退出
- **THEN** Server 状态变为 `disconnected`，系统按重连策略自动尝试重连

### Requirement: SSE Transport Connection

系统 SHALL 支持通过 SSE/Streamable HTTP 传输协议连接远程 MCP Server。配置中 `transport: "sse"` 时，系统 SHALL 使用 `url` 字段建立 SSE 连接进行 JSON-RPC 通信。

#### Scenario: SSE Server 连接成功

- **WHEN** 配置包含 `{"name": "remote-server", "transport": "sse", "url": "http://localhost:3001/mcp"}`，且目标 URL 可访问
- **THEN** 系统建立 SSE 连接，完成 MCP 握手，Server 状态变为 `connected`

#### Scenario: SSE Server 连接超时

- **WHEN** 目标 URL 不可达导致连接超时
- **THEN** Server 状态为 `error`，记录超时错误，按重连策略处理

### Requirement: Tool Discovery via MCP

系统 SHALL 在连接成功后调用 MCP `tools/list` 发现 Server 提供的工具列表。每个工具 SHALL 包含 `name`、`description` 和 `inputSchema`。

#### Scenario: Server 提供多个工具

- **WHEN** MCP Server 返回 `tools/list` 结果包含 3 个工具
- **THEN** 系统缓存这 3 个工具的定义，供 `mcp_call` 使用

#### Scenario: Server 无工具

- **WHEN** MCP Server 返回 `tools/list` 结果为空数组
- **THEN** Server 保持 `connected` 状态，但不向 LLM 暴露任何可用工具

### Requirement: mcp_call Tool

系统 SHALL 提供 `mcp_call` 工具，允许 LLM 调用指定 MCP Server 的指定工具。`mcp_call` 参数 SHALL 包含 `server`（Server 名称）、`tool`（工具名称）、`arguments`（工具参数，JSON 对象）。

#### Scenario: 成功调用 MCP 工具

- **WHEN** LLM 调用 `mcp_call`，指定有效的 `server`、`tool` 和合法的 `arguments`
- **THEN** 系统将调用转发到对应 MCP Server，返回工具的执行结果

#### Scenario: 调用不存在的 Server

- **WHEN** LLM 调用 `mcp_call`，指定的 `server` 不在已连接列表中
- **THEN** 返回错误信息："Server '<name>' 未连接或不存在"

#### Scenario: 调用不存在的工具

- **WHEN** LLM 调用 `mcp_call`，指定的 `tool` 在目标 Server 的工具列表中不存在
- **THEN** 返回错误信息："工具 '<name>' 在 Server '<server>' 中不存在"

### Requirement: Tool Schema Injection into System Prompt

系统 SHALL 将已连接 MCP Server 的工具列表注入 system prompt。展示格式 SHALL 与 Skill 索引一致：`- **<server>/<tool>**: <description>`。热更新后，下一个 `get_system_prompt()` 调用 SHALL 反映工具增减。

#### Scenario: 多个 Server 均有工具

- **WHEN** Server A 有工具 x、y，Server B 有工具 z
- **THEN** system prompt 中 MCP 工具部分列出 A/x、A/y、B/z，与 Skill 索引格式一致

#### Scenario: Server 热更新后工具变化

- **WHEN** 热更新导致新增 Server C（有工具 w）
- **THEN** 下一个 `get_system_prompt()` 调用生成的 system prompt 包含 C/w

### Requirement: Hot Reload Configuration

系统 SHALL 监控 `.ga/mcp_servers.json` 文件变化。当文件内容变化（mtime 更新）时，系统 SHALL 解析新配置，并执行增量变更：新增的 Server 建立连接、移除的 Server 断开连接、变更的 Server 重连。

#### Scenario: 新增一个 Server 配置

- **WHEN** 配置文件中新增了一个 MCP Server 的完整配置
- **THEN** 系统为该 Server 建立连接，其余 Server 不受影响

#### Scenario: 移除一个 Server 配置

- **WHEN** 配置文件中移除了一个已有 Server
- **THEN** 系统断开该 Server 连接并清理资源，其余 Server 不受影响

#### Scenario: 热更新时 JSON 格式错误

- **WHEN** 修改配置文件导致 JSON 格式无效
- **THEN** 系统保留所有现有连接不变，记录错误日志，不应用无效配置

### Requirement: Health Monitoring and Auto-Reconnect

系统 SHALL 对已连接的 MCP Server 进行定期心跳检测。连接意外断开时，系统 SHALL 按可配置的重试策略自动重连。

#### Scenario: 心跳正常

- **WHEN** Server 在心跳间隔内正常响应
- **THEN** Server 状态保持 `connected`

#### Scenario: 心跳失败触发重连

- **WHEN** 心跳检测连续失败达到阈值（默认 3 次）
- **THEN** Server 状态变为 `disconnected`，系统启动重连流程

#### Scenario: 重连成功

- **WHEN** `disconnected` 状态的 Server 在重试间隔后成功重连
- **THEN** Server 状态恢复为 `connected`，重新发现工具

#### Scenario: 重连耗尽

- **WHEN** 重连次数达到上限
- **THEN** Server 状态变为 `failed`，停止重试，记录错误日志

### Requirement: Server Lifecycle Control via CLI

系统 SHALL 通过 `ga mcp` 子命令提供 Server 生命周期控制：`list` 列出所有 Server 及其健康状态，`stop <name>` 断开指定 Server，`start <name>` 启动指定 Server，`restart <name>` 重启指定 Server。

#### Scenario: 列出所有 Server

- **WHEN** 执行 `ga mcp list`
- **THEN** 输出表格，每行包含 Server 名称、传输类型、连接状态、已连接时长、工具数量

#### Scenario: 停止一个运行中的 Server

- **WHEN** 执行 `ga mcp stop my-server`
- **THEN** 该 Server 断开连接，CLI 确认

#### Scenario: 重启一个 Server

- **WHEN** 执行 `ga mcp restart my-server`
- **THEN** 该 Server 先断开连接，再重新连接并发现工具

### Requirement: Single Server Failure Isolation

当某个 MCP Server 不可用时，系统 SHALL 保证该 Server 的故障不影响其他 Server、不影响 GA 原生工具、不阻断 Agent Loop 的正常运行。

#### Scenario: 一个 Server 连接失败

- **WHEN** 3 个已配置 Server 中有一个连接失败
- **THEN** 其余 2 个 Server 保持正常，GA 原生工具正常可用

#### Scenario: mcp_call 调用失败 Server 的工具

- **WHEN** LLM 调用 `mcp_call` 指定一个已 `disconnected` 的 Server 的工具
- **THEN** 返回错误信息，Agent Loop 不中断

### Requirement: Call Audit Logging

系统 SHALL 记录每次 `mcp_call` 的审计信息，包含：时间戳、Server 名称、工具名称、输入参数摘要、输出结果摘要、执行耗时（毫秒）、是否成功。

#### Scenario: 成功调用的审计记录

- **WHEN** `mcp_call` 成功执行
- **THEN** 审计日志记录完整信息，`success: true`，包含耗时

#### Scenario: 失败调用的审计记录

- **WHEN** `mcp_call` 执行失败（Server 不可用、工具不存在、参数错误等）
- **THEN** 审计日志记录完整信息，`success: false`，包含错误原因
