# Brainstorm Summary

- Change: add-mcp-integration
- Date: 2026-01-08

## Confirmed Technical Approach

MCP Client 采用独立模块 `mcp_client.py`，基于官方 `mcp` Python SDK。核心架构：
- `MCPClientManager` 作为顶层管理器，管理多个 `MCPServerConnection` 实例
- 每种传输（stdio/SSE）一个连接类，统一接口 `list_tools()` / `call_tool(name, args)`
- `MCPToolRegistry` 维护工具索引缓存，供 system prompt 注入和 `do_mcp_call` 路由
- 热更新通过文件 mtime 检测 + 增量 diff，逐 Server 操作（尽力而为）
- 故障恢复：Agent 启动时连接，挂断后自动重连（指数退避 2s→60s）
- L2 蒸馏解耦：MCP 调用写入 L4 会话，L3 结晶阶段自动处理

## Key Trade-offs and Risks

- **官方 SDK 依赖**：`mcp` 包更新可能引入 breaking change → 锁定版本
- **单一 mcp_call 工具**：LLM 多一次调用才能使用 MCP 工具（先看 system prompt 发现工具，再调 mcp_call） → 可接受，符合 GA 最小工具理念
- **热更新尽力而为**：部分失败不阻塞 → 需清晰日志让用户感知
- **stdio 进程泄漏**：MCP Server 子进程可能僵尸 → MCPClientManager 析构时清理所有子进程
- **L2 蒸馏解耦**：短期内 MCP 调用信息不自动进入 L2 → L3 结晶阶段补全

## Testing Strategy

三层 TDD：
1. **单元测试**（`tests/test_mcp_client.py`）：mock mcp SDK，验证配置加载、diff 逻辑、生命周期管理
2. **集成测试**：用 mcp SDK 的 InMemoryTransport 模拟 Server，验证完整调用链路
3. **端到端测试**：启动真实 MCP Server（如 mcp-server-filesystem），验证 stdio/SSE 真实传输

## Spec Patches

None — open 阶段 spec 已覆盖所有设计决策。
