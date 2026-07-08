---
change: add-mcp-integration
design-doc: docs/superpowers/specs/2026-01-08-add-mcp-integration-design.md
base-ref: aa0b707d9fb8d60c88ece7a1719e50b9fb693ada
archived-with: 2026-07-08-add-mcp-integration
---

# MCP Integration 实施计划

## 概述

为 GenericAgent 添加 MCP Client 支持，包含配置管理、stdio/SSE 双传输、工具发现、mcp_call 工具、生命周期管理、热更新、CLI 集成、记忆融合、审计日志。

执行方式：TDD（red-green-refactor），独立 git worktree 隔离。

## 执行环境

- 项目根目录：`/mnt/d/GenericAgent`
- Worktree 隔离：`/mnt/d/GenericAgent/../ga-mcp-worktree`
- Python 3.12.3
- 包管理：pip
- 测试框架：pytest

## TDD 执行顺序

每个任务遵循：先写失败测试（RED）→ 实现代码使测试通过（GREEN）→ 重构（REFACTOR）。

### Phase 1: 基础设施（任务组 1-2）

1. **RED**：`tests/test_mcp_client.py::test_mcp_manager_singleton` — 验证 MCPManager 单例
2. **GREEN**：创建 `mcp_client.py`，实现 `MCPManager` 单例类
3. **RED**：`test_load_config_valid` / `test_load_config_missing_file` / `test_load_config_invalid_json`
4. **GREEN**：实现配置加载，处理文件不存在和 JSON 解析错误
5. 添加 `mcp` 到 `requirements.txt`
6. commit: "feat: MCP client skeleton + config loading"

### Phase 2: 传输层（任务组 3-4）

7. **RED**：`test_stdio_connection_lifecycle` — mock 子进程，验证 initialize → list_tools → call_tool → disconnect
8. **GREEN**：实现 `StdioConnection`（基于 `mcp` SDK 的 `stdio_client`）
9. **RED**：`test_sse_connection_lifecycle` — mock HTTP，验证 SSE 连接生命周期
10. **GREEN**：实现 `SSEConnection`（基于 `mcp` SDK 的 `sse_client`）
11. commit: "feat: stdio + SSE transport connections"

### Phase 3: 工具发现与 mcp_call（任务组 5）

12. **RED**：`test_tool_discovery_and_cache` — 验证 list_tools 结果缓存和失效
13. **GREEN**：实现 `MCPToolRegistry`（tool name → server + inputSchema 映射）
14. **RED**：`test_do_mcp_call_success` / `test_do_mcp_call_unknown_server` / `test_do_mcp_call_unknown_tool`
15. **GREEN**：在 `ga.py` 实现 `do_mcp_call`，在 `tools_schema.json` 添加 `mcp_call` schema
16. commit: "feat: tool discovery + mcp_call tool"

### Phase 4: 健康监控与重连（任务组 6）

17. **RED**：`test_health_check_pass` / `test_reconnect_on_failure` / `test_reconnect_exponential_backoff`
18. **GREEN**：实现心跳检测 + 指数退避重连（2s→60s cap）
19. **GREEN**：实现 `atexit` 子进程清理
20. commit: "feat: health monitoring + auto-reconnect"

### Phase 5: 热更新（任务组 7）

21. **RED**：`test_hot_reload_add_server` / `test_hot_reload_remove_server` / `test_hot_reload_invalid_json_preserves_existing`
22. **GREEN**：实现 mtime 轮询 + 增量 diff + 尽力而为操作
23. **GREEN**：实现热更新锁（防并发）
24. commit: "feat: hot reload with incremental diff"

### Phase 6: CLI + Agent 启动（任务组 8-9）

25. **RED**：`test_cli_list` / `test_cli_start_stop`
26. **GREEN**：在 `ga_cli/cli.py` 新增 `mcp` 子命令族
27. **GREEN**：在 `agentmain.py` 初始化 MCPManager
28. commit: "feat: CLI subcommands + agent init"

### Phase 7: 记忆融合（任务组 10）

29. **RED**：`test_skill_catalog_includes_mcp_tools` / `test_system_prompt_has_mcp_section`
30. **GREEN**：修改 `skill_loader.py` 的 `get_skill_catalog()`
31. **GREEN**：hook `tool_after` 事件（MCP 调用写入 L4 会话）
32. **GREEN**：Skill 结晶标注 `mcp_dependencies` frontmatter
33. commit: "feat: memory system integration"

### Phase 8: 审计日志（任务组 11）

34. **RED**：`test_audit_log_format` / `test_audit_log_records_errors`
35. **GREEN**：实现 `AuditLogger` → `temp/mcp_audit.jsonl`
36. commit: "feat: audit logging"

### Phase 9: 端到端测试（任务组 12）

37. **RED+GREEN**：端到端 stdio Server 调用链路
38. **RED+GREEN**：端到端热更新 → system prompt 变化
39. **RED+GREEN**：端到端故障重连
40. commit: "test: end-to-end integration tests"

### Phase 10: 文档（任务组 13）

41. 更新 `MAP.md`
42. 创建 `memory/mcp_sop.md`
43. commit: "docs: update MAP + MCP SOP"

## 验收标准

- [x] 所有 pytest 测试通过
- [x] `mcp` 包安装成功
- [x] `ga mcp list` 命令可用
- [x] 配置 stdio Server → mcp_call 调用成功
- [x] 热更新：改配置文件 → 工具列表刷新
- [x] 故障重连：kill Server → 自动恢复
- [x] 审计日志可查
- [x] system prompt 包含 MCP 工具列表

