## 1. 基础设施搭建

- [x] 1.1 创建 `mcp_client.py` 模块骨架：`MCPManager` 单例类 + `MCPServerConnection` 类（抽象连接生命周期）
- [x] 1.2 添加 `mcp` SDK 依赖到 `requirements.txt` 或声明依赖文件
- [x] 1.3 编写 MCPManager 单元测试骨架（TDD：先写测试确认接口设计）

## 2. 配置加载与解析

- [x] 2.1 实现 `.ga/mcp_servers.json` 配置加载：解析 JSON，验证每个 Server 的必填字段（`name`, `transport`）
- [x] 2.2 编写配置加载测试（正常格式、文件不存在、JSON 无效）

## 3. stdio Transport

- [x] 3.1 实现 `StdioConnection`：子进程启动、JSON-RPC 握手（`initialize`）、`notifications/initialized`
- [x] 3.2 编写 stdio 连接测试（启动成功、命令不存在、进程意外退出）

## 4. SSE Transport

- [x] 4.1 实现 `SSEConnection`：SSE 连接建立、JSON-RPC 消息收发
- [x] 4.2 编写 SSE 连接测试（连接成功、连接超时）

## 5. 工具发现与 mcp_call

- [x] 5.1 实现 `tools/list` 调用与缓存：连接成功后自动发现工具列表
- [x] 5.2 在 `ga.py` 中新增 `do_mcp_call` 方法（委托到 `MCPManager.call_tool()`）
- [x] 5.3 在 `tools_schema.json` 中新增 `mcp_call` 工具定义
- [x] 5.4 编写 `do_mcp_call` 测试（成功调用、不存在的 Server、不存在的工具）

## 6. 健康监控与自动重连

- [x] 6.1 实现心跳检测：对每个已连接 Server 定期调用 `ping`，记录失败次数
- [x] 6.2 实现自动重连：失败阈值触发 → 按间隔重试 → 达到上限标记 `failed`
- [x] 6.3 编写健康监控测试（心跳正常、失败触发重连、重连成功、重连耗尽）
- [x] 6.4 实现 `atexit` 清理所有子进程

## 7. 热更新

- [x] 7.1 实现配置文件 mtime 轮询：每 2 秒检查一次
- [x] 7.2 实现增量重载逻辑：新增 Server 连接、移除的断开、变更的重连
- [x] 7.3 实现热更新锁：防止并发重载
- [x] 7.4 编写热更新测试（新增 Server、移除 Server、JSON 格式错误时保留现有连接）

## 8. GA CLI 集成

- [x] 8.1 在 `ga_cli/cli.py` 中新增 `mcp` 子命令族：`list`、`start`、`stop`、`restart`
- [x] 8.2 编写 CLI 命令测试

## 9. Agent 启动集成

- [x] 9.1 在 `agentmain.py` 的 `GenericAgent.__init__()` 或 `run()` 中初始化 `MCPManager`
- [x] 9.2 确保热更新轮询在 Agent 生命周期内持续运行

## 10. 记忆融合

- [x] 10.1 修改 `skill_loader.py` 的 `get_skill_catalog()`：拼接 MCP 工具列表（`<server>/<tool>` 格式，标注 `[MCP]`）
- [x] 10.2 确保 MCP 工具在 `get_system_prompt()` 中与 Skill 索引同级展示
- [x] 10.3 `mcp_call` 结果通过 `plugins/hooks.py` 的 `tool_after` 事件触发 L2 记忆评估
- [x] 10.4 修改 Skill 结晶逻辑：检测任务历史中的 `mcp_call`，自动生成 `mcp_dependencies` frontmatter
- [x] 10.5 编写记忆融合测试

## 11. 调用审计

- [x] 11.1 实现审计日志记录：时间戳、Server、工具名、参数摘要、结果摘要、耗时、成功/失败
- [x] 11.2 审计日志写入 `temp/mcp_audit.jsonl`
- [x] 11.3 编写审计日志测试

## 12. 集成测试与验收

- [x] 12.1 端到端测试：配置 stdio Server → Agent 启动 → `mcp_call` 成功调用 → 审计日志可查
- [x] 12.2 端到端测试：配置 SSE Server → 连接成功 → 工具发现 → 调用返回
- [x] 12.3 端到端测试：热更新 → 增减 Server → system prompt 反映变化
- [x] 12.4 端到端测试：Server 挂断 → 自动重连 → 恢复可用
- [x] 12.5 TDD 完整性检查：所有测试通过，覆盖率 ≥ 80%

## 13. 文档

- [x] 13.1 更新 `MAP.md`：新增 `mcp_client.py` 条目
- [x] 13.2 在 `memory/` 下创建 `mcp_sop.md`：MCP Client 使用说明
