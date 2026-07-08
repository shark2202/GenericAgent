# MCP Client SOP

## 概述
GenericAgent 支持通过 MCP (Model Context Protocol) Client 连接外部 MCP Server，扩展 Agent 工具能力。

## 配置
配置文件：`.ga/mcp_servers.json`

### stdio Server
```json
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

### SSE Server
```json
{
  "mcpServers": {
    "remote": {
      "transport": "sse",
      "url": "http://localhost:8080/sse"
    }
  }
}
```

## 使用
- Agent 启动时自动连接所有配置的 Server
- 使用 `mcp_call` 工具调用：`mcp_call(server="filesystem", tool="read_file", arguments={"path": "/tmp/test.txt"})`
- system prompt 中 [MCP] 部分列出所有可用 Server 和工具

## CLI 管理
- `ga mcp list` — 查看所有 Server 状态
- `ga mcp start <name>` — 启动指定 Server
- `ga mcp stop <name>` — 停止指定 Server
- `ga mcp restart <name>` — 重启指定 Server

## 热更新
修改 `.ga/mcp_servers.json` 后，Agent 自动检测变化并增量重载：
- 新增 Server → 自动连接
- 移除 Server → 自动断开
- 修改 Server → 断开旧连接 + 建立新连接
- JSON 格式错误 → 保留现有连接，输出错误日志

## 故障恢复
- Server 断开后自动重连（指数退避：2s → 4s → 8s → ... → 60s 封顶）
- 单个 Server 故障不影响其他 Server

## 记忆融合
- MCP 工具列表注入 system prompt（与 Skill 索引同级）
- MCP 调用记录在会话历史中，任务完成后 Skill 结晶自动标注 MCP 依赖
