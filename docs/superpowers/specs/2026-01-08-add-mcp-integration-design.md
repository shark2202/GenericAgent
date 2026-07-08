---
comet_change: add-mcp-integration
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-08-add-mcp-integration
status: final
---

# MCP Client 与 GenericAgent 记忆/自进化融合 — 深度技术设计

## 1. 架构总览

```
.ga/mcp_servers.json ──→ MCPClientManager (mcp_client.py)
                              │
              ┌───────────────┼───────────────┐
              │               │               │
         StdioConnection  SSEConnection   SSE Connection
         (子进程 stdio)  (HTTP SSE)        (HTTP SSE)
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                    MCPToolRegistry
                    (工具索引 + 缓存)
                              │
              ┌───────────────┼───────────────┐
              │               │               │
         system_prompt    do_mcp_call      audit_log
         (skill_loader)    (ga.py)      (结构化日志)
              │               │
              ▼               ▼
         L1 Skill索引      L4 会话/审计
              │
              ▼
         L3 技能结晶 (标注 MCP 依赖)
```

### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| `MCPClientManager` | `mcp_client.py` | 顶层管理器：配置加载、热更新检测、连接池管理、生命周期 |
| `MCPServerConnection` | `mcp_client.py` | 单 Server 抽象：连接/重连/心跳/工具发现/调用 |
| `StdioConnection` | `mcp_client.py` | stdio 传输实现：子进程管理、JSON-RPC 通信 |
| `SSEConnection` | `mcp_client.py` | SSE/Streamable HTTP 传输实现：HTTP 客户端、event stream 解析 |
| `MCPToolRegistry` | `mcp_client.py` | 工具索引缓存：tool name → (server, inputSchema) 映射 |
| `AuditLogger` | `mcp_client.py` | 审计日志：调用记录、错误收集、Schema 变更检测 |
| `do_mcp_call` | `ga.py` | 工具实现：解析参数 → 路由到 MCPClientManager → 返回结果 |
| `mcp_call` schema | `assets/tools_schema.json` | LLM tool definition |
| `ga mcp` CLI | `ga_cli/cli.py` | 子命令族：list/start/stop/restart |
| `get_skill_catalog()` 扩展 | `skill_loader.py` | 注入 MCP 工具列表到 system prompt |

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 2. 数据流

### 2.1 启动流程

```
1. agentmain.py: GenericAgent.__init__()
2. → MCPClientManager.load_config(".ga/mcp_servers.json")
3. → 遍历 servers: 创建 StdioConnection 或 SSEConnection
4. → 每个连接: initialize() → list_tools() → 注册到 MCPToolRegistry
5. → MCPToolRegistry 产出 tool_catalog (name, description, inputSchema)
6. → get_system_prompt() 调用 get_skill_catalog() → 拼接 MCP tool catalog
7. → Agent Loop 开始，LLM 在 system prompt 中看到 MCP 工具列表
```

### 2.2 mcp_call 调用流程

```
1. LLM 产出: mcp_call(server="filesystem", tool="read_file", arguments={"path": "/tmp/x"})
2. → agent_loop.py: BaseHandler.dispatch("mcp_call", args)
3. → ga.py: do_mcp_call(args)
4. → MCPToolRegistry.resolve("filesystem", "read_file") → MCPServerConnection
5. → connection.call_tool("read_file", {"path": "/tmp/x"})
6. → JSON-RPC: tools/call → MCP Server → 结果
7. → AuditLogger.record(server, tool, arguments, result, elapsed)
8. → 返回 StepOutcome(data=result) → 注入 L4 会话
```

### 2.3 热更新流程

```
1. MCPClientManager._hot_reload_check()（每次 mcp_call 前或定期检查）
2. → os.stat(".ga/mcp_servers.json").st_mtime 对比缓存
3. → 有变化: load_config() → diff 旧配置 vs 新配置
4. → 新增 Server: 创建连接 → initialize → list_tools → 注册
5. → 移除 Server: disconnect() → 注销 → 清理子进程
6. → 变更 Server: disconnect(old) → 创建新连接 → 重新注册
7. → 任意步骤失败: 日志记录，不阻塞其他 Server 操作
8. → MCPToolRegistry 更新 → 下一次 get_system_prompt() 生效 (L1 刷新)
```

### 2.4 故障恢复流程

```
1. 心跳检测: 每 30s ping 一次 (mcp SDK 内置 ping 或自定义)
2. → ping 失败: 标记 UNAVAILABLE
3. → 启动重连: 初始 2s → 指数退避 (4s, 8s, 16s, 32s, 60s cap)
4. → 重连成功: initialize → list_tools → 对比旧 schema
5.   → schema 变更: 更新 MCPToolRegistry + 注解审计日志
6.   → schema 不变: 仅更新状态标记
7. → 每次退避 >30s: 输出 WARNING 日志
8. → 用户可手动 ga mcp stop/restart 干预
```

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 3. 接口设计

### 3.1 MCPClientManager (顶层 API)

```python
class MCPClientManager:
    def __init__(self, config_path: str = ".ga/mcp_servers.json")
    def load_config(self) -> list[ServerConfig]
    def connect_all(self) -> None
    async def connect_all_async(self) -> None
    def get_registry(self) -> MCPToolRegistry
    def call_tool(self, server: str, tool: str, arguments: dict, timeout: float = 30.0) -> ToolResult
    async def call_tool_async(self, server: str, tool: str, arguments: dict, timeout: float = 30.0) -> ToolResult
    def hot_reload(self) -> HotReloadResult
    def stop_server(self, name: str) -> None
    def start_server(self, name: str) -> None
    def restart_server(self, name: str) -> None
    def list_servers(self) -> list[ServerStatus]
    def shutdown(self) -> None
```

### 3.2 MCPServerConnection (单 Server 抽象)

```python
class MCPServerConnection(ABC):
    name: str
    transport: Literal["stdio", "sse"]
    status: Literal["connected", "connecting", "unavailable", "dead"]

    async def initialize(self) -> None
    async def list_tools(self) -> list[ToolSchema]
    async def call_tool(self, name: str, arguments: dict, timeout: float) -> ToolResult
    async def ping(self) -> bool
    async def disconnect(self) -> None
```

### 3.3 配置文件格式 (`.ga/mcp_servers.json`)

```json
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    },
    "remote-api": {
      "transport": "sse",
      "url": "http://localhost:3001/sse"
    }
  },
  "options": {
    "pingInterval": 30,
    "reconnectBaseDelay": 2,
    "reconnectMaxDelay": 60,
    "callTimeout": 30
  }
}
```

### 3.4 mcp_call 工具 Schema（注入 `tools_schema.json`）

```json
{
  "type": "function",
  "function": {
    "name": "mcp_call",
    "description": "Call a tool on a connected MCP Server. Use server:tool names from the MCP tools catalog in system prompt.",
    "parameters": {
      "type": "object",
      "properties": {
        "server": {"type": "string", "description": "MCP Server name as configured in .ga/mcp_servers.json"},
        "tool": {"type": "string", "description": "Tool name to call on the server"},
        "arguments": {"type": "object", "description": "Tool arguments as key-value pairs"}
      },
      "required": ["server", "tool"]
    }
  }
}
```

### 3.5 CLI 命令 (`ga mcp`)

```
ga mcp list                      # 列出所有 Server: name, transport, status, tool_count
ga mcp start <name>              # 连接指定 Server（已连接则跳过）
ga mcp stop <name>               # 断开指定 Server
ga mcp restart <name>            # 断开 + 重连指定 Server
ga mcp reload                    # 手动触发热更新（重新加载 .ga/mcp_servers.json）
```

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 4. 与现有系统的融合点

### 4.1 L1 技能索引（system prompt 注入）

`skill_loader.py` 的 `get_skill_catalog()` 扩展：

```python
def get_skill_catalog():
    catalog = _scan_skills()  # 现有逻辑
    mcp_tools = _get_mcp_tool_catalog()  # 新增：从 MCPToolRegistry 获取
    if mcp_tools:
        catalog += "\n## MCP Tools\n"
        for tool in mcp_tools:
            catalog += f"- **{tool.server}:{tool.name}**: {tool.description}\n"
    return catalog
```

MCP 工具与 Skill 同级显示，LLM 在 system prompt 中可见。热更新后下一个 `get_system_prompt()` 调用自动刷新。

### 4.2 L4 会话（MCP 调用链归档）

`do_mcp_call` 返回的 `StepOutcome.data` 包含：
- `server` + `tool` + `arguments`
- 调用结果文本
- `elapsed_ms`

此结果作为普通工具返回进入 Agent Loop 的 `messages` 历史，自动保留在 L4 原始会话中。

### 4.3 L3 技能结晶（MCP 依赖标注）

当 Agent 完成任务并触发 `start_long_term_update` 蒸馏为 L3 Skill 时，Skill 生成逻辑检查会话中的 `mcp_call` 调用，自动在 SKILL.md frontmatter 添加：

```yaml
mcp_dependencies:
  - server: filesystem
    tools: ["read_file", "write_file"]
```

### 4.4 审计日志（数据闭环）

`AuditLogger` 输出到 `.ga/mcp_audit.log`（JSONL 格式）：

```jsonl
{"ts": "2026-01-08T10:03:00Z", "type": "call", "server": "filesystem", "tool": "read_file", "args_summary": "path=/tmp/x", "result_summary": "3 lines", "elapsed_ms": 145}
{"ts": "2026-01-08T10:03:01Z", "type": "error", "server": "remote-api", "error": "Connection refused", "context": "ping"}
{"ts": "2026-01-08T10:03:30Z", "type": "schema_change", "server": "filesystem", "added": ["delete_file"], "removed": []}
```

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 5. 重连与退避策略

```
重连状态机:
CONNECTED ──(ping 失败)──→ RECONNECTING ──(成功)──→ CONNECTED
                              │
                              ├──(失败 + 退避 < 上限)──→ RECONNECTING (等 N 秒)
                              └──(永不放弃，持续重试)

退避序列: 2s → 4s → 8s → 16s → 32s → 60s → 60s → ...
告警阈值: >30s 输出 WARNING
```

## 6. 错误处理矩阵

| 场景 | 行为 | 日志级别 |
|------|------|----------|
| 配置文件不存在 | 静默跳过，MCP 功能禁用 | DEBUG |
| 配置文件 JSON 格式错误 | 保留旧连接，报错日志，不热更新 | ERROR |
| Server 连接失败（启动时） | 标记 UNAVAILABLE，进入重连循环 | WARNING |
| Server 连接失败（热更新时） | 标记 UNAVAILABLE，不阻塞其他 Server | WARNING |
| mcp_call 工具不存在 | 返回错误文本给 LLM | INFO |
| mcp_call 超时（30s） | 返回超时错误 + 部分结果（如有） | WARNING |
| Server 进程崩溃（stdio） | 自动重连 | WARNING |
| Server 返回非 JSON | 返回原始文本给 LLM | WARNING |
| 心跳失败 | 进入重连状态 | INFO |
| 重连后 schema 变更 | 更新 registry，注解审计日志 | INFO |

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 7. 测试策略

### 7.1 单元测试（`tests/test_mcp_client.py`）

- `test_load_config_valid`：解析合法 JSON 配置
- `test_load_config_missing_file`：文件不存在时的默认行为
- `test_load_config_invalid_json`：无效 JSON 不崩溃
- `test_hot_reload_diff`：增量 diff 逻辑（新增/移除/变更）
- `test_hot_reload_no_change`：mtime 无变化时跳过
- `test_server_status_transitions`：状态机转换正确
- `test_audit_log_format`：审计日志格式正确

### 7.2 集成测试

- `test_stdio_connection_lifecycle`：用 InMemoryTransport 模拟 stdio Server，验证完整生命周期
- `test_sse_connection_lifecycle`：同上，SSE 传输
- `test_mcp_call_roundtrip`：调用 → 返回 → 结果解析
- `test_tool_registry_cache`：工具列表缓存正确性
- `test_reconnect_flow`：模拟断连 → 重连 → schema 变更
- `test_system_prompt_injection`：get_skill_catalog() 正确拼接 MCP catalog

### 7.3 端到端测试

- `test_real_stdio_server`：启动 mcp-server-filesystem，完整调用 read_file/write_file
- `test_cli_list`：`ga mcp list` 输出正确
- `test_cli_stop_start`：启停 Server 正常

archived-with: 2026-07-08-add-mcp-integration
status: final
---

## 8. 开放问题

- [ ] mcp SDK 的 async API vs GA 的同步 Agent Loop：do_mcp_call 是否需要 `asyncio.run()` 桥接？还是使用同步 Client 变体？
  - **方向**：优先检查 mcp SDK 是否提供同步 Client；若有则直接用；若无则 `asyncio.run()` 桥接
- [ ] stdio transport 的 env 变量传递：`mcp_servers.json` 中的 `env` 字段如何合并到子进程环境？
  - **方向**：合并到 `os.environ.copy() + server_config.env`

