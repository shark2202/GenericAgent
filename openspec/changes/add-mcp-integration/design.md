## Context

GenericAgent 当前拥有 9 个原子工具（`code_run`, `file_read`, `file_patch`, `file_write`, `web_scan`, `web_execute_js`, `update_working_checkpoint`, `ask_user`, `start_long_term_update`），工具定义在 `assets/tools_schema.json`，实现在 `ga.py` 的 `do_*` 方法中。Agent Loop（`agent_loop.py`）通过 `BaseHandler.dispatch()` 路由工具调用。

记忆系统分为 L1（索引 `global_mem_insight.txt`）、L2（事实 `global_mem.txt`）、L3（技能文件 `memory/*.md`）、L4（原始会话 `L4_raw_sessions/`）。技能发现由 `skill_loader.py` 的 `get_skill_catalog()` 实现，注入 `get_system_prompt()`。

项目设计哲学：最小架构（~3K 行核心代码），不预加载能力，通过自进化结晶 Skill。

本变更在保持最小架构的前提下，接入 MCP 生态并深度融合记忆/自进化系统。

## Goals / Non-Goals

**Goals:**
- MCP Client 能连接 stdio + SSE 两种传输的 MCP Server
- 通过 `mcp_call` 工具暴露 MCP 工具给 LLM
- 配置热更新（增量重载，不重启 Agent）
- Server 生命周期管理（健康监控、自动重连、CLI 启停）
- 调用审计日志
- 与 L1-L4 记忆系统融合：工具列表注入 system prompt、结果触发 L2 蒸馏、Skill 结晶标注 MCP 依赖
- 单 Server 故障隔离

**Non-Goals:**
- 不将 GA 工具暴露为 MCP Server
- 不做 GUI 配置界面
- 不做 MCP Server 自动发现（mDNS 等）
- 不做 MCP 资源（resources）和提示（prompts）支持，仅做工具（tools）

## Decisions

### D1: 使用官方 `mcp` Python SDK

**选择**：`pip install mcp`（官方 SDK）
**理由**：官方 SDK 维护 MCP 协议规范，支持 stdio + SSE 传输、JSON-RPC 握手、工具发现和调用。自实现协议需处理 JSON-RPC 2.0、传输层、错误码等细节，维护成本高。
**备选**：自实现 JSON-RPC over stdio/SSE——协议本身不复杂，但边界情况（partial messages、SSE event parsing、connection lifecycle）多，收益低。

### D2: 单一 `mcp_call` 工具 vs 扁平化工具列表

**选择**：单一 `mcp_call` 工具，参数为 `server` + `tool` + `arguments`
**理由**：
- 不修改 `tools_schema.json` 的原生工具列表，MCP 工具增减不需要改 schema
- LLM 通过 `mcp_call` 调用，工具列表从 system prompt 获取（与 Skill 索引同级）
- 保持最小架构，避免工具列表膨胀
**备选**：将 MCP 工具动态注入 `tools_schema.json`——需要运行时修改 schema、处理工具名冲突、每次热更新重建 schema，复杂度高且破坏 GA 现有工具管理方式。

### D3: 配置文件位置与格式

**选择**：`.ga/mcp_servers.json`，格式兼容 Claude Desktop
**理由**：`.ga/` 是项目级配置目录（与 `.opcteam/` 对齐），用户熟悉 Claude Desktop 的 JSON 格式，降低学习成本。
**格式**：
```json
{
  "mcpServers": {
    "server-name": {
      "transport": "stdio",
      "command": "node",
      "args": ["server.js"],
      "env": {"KEY": "value"}
    },
    "remote-server": {
      "transport": "sse",
      "url": "http://localhost:3001/mcp"
    }
  }
}
```

### D4: 热更新机制——文件 mtime 轮询

**选择**：轮询配置文件 mtime，变化时重载
**理由**：跨平台兼容（`watchdog`/`inotify` 在不同 OS 行为不一），轮询间隔 2s 足够实时且开销极低。Agent Loop 每轮 `get_system_prompt()` 前检查 mtime。
**备选**：`watchdog` 库文件系统事件——需要额外依赖，Windows/Linux 行为不一致，事件去重复杂。

### D5: 健康监控——ping 心跳

**选择**：定期调用 MCP `ping` 方法，失败 N 次（默认 3）标记 disconnected
**理由**：MCP 协议内置 `ping` 方法，标准做法。间隔 30s（可配）。
**备选**：监控 stdio 子进程存活 / SSE 连接状态——不够通用，无法检测"进程活着但 JSON-RPC 无响应"的僵死状态。

### D6: MCP Client 为独立模块 `mcp_client.py`

**选择**：新建 `mcp_client.py`，包含 `MCPClient` 类和 `MCPManager` 单例管理器
**理由**：单一职责，不侵入 `ga.py` 的核心逻辑。`MCPManager` 管理所有 Server 连接、热更新、健康监控。`ga.py` 只新增 `do_mcp_call` 调用 `MCPManager`。
**备选**：直接在 `ga.py` 中实现——破坏单一职责，`ga.py` 已较大。

### D7: 记忆融合——最小侵入式 hook

**选择**：
- L1：`skill_loader.get_skill_catalog()` 调用 `MCPManager.get_tool_catalog()`，拼接 MCP 工具到索引
- L2：`do_mcp_call` 返回结果后，通过现有 `plugins.hooks` 的 `tool_after` 事件触发记忆评估（不强制每次都蒸馏，由 Agent 自行判断）
- L3：Skill 结晶时，检查任务历史中的 `mcp_call` 调用，自动生成 `mcp_dependencies` frontmatter
**理由**：利用现有 hook 系统（`plugins/hooks.py`），不新增侵入点。L1 融合只需在 `get_skill_catalog()` 末尾追加 MCP 工具列表。

## Risks / Trade-offs

- **[mcp SDK 版本稳定性]** → 锁定版本（`mcp>=1.0`），在 `requirements.txt` 中 pin
- **[stdio 子进程泄漏]** → MCPManager 在 Agent 退出时通过 `atexit` 清理所有子进程
- **[热更新竞态]** → 配置重载加锁（`threading.Lock`），避免半重载状态
- **[LLM 工具列表膨胀]** → `mcp_call` 模式下，system prompt 只列工具名+描述（不列完整 schema），LLM 调用时传 JSON 参数
- **[SSE 连接不稳定]** → 重连策略 + 心跳双重保障
- **[MCP 依赖标注准确性]** → L3 结晶依赖会话历史中的 `mcp_call` 记录，若历史被截断可能遗漏——可接受，标注是 best-effort

## Open Questions

- MCP `resources` 和 `prompts` 是否在后续迭代支持？（当前 Non-Goal，但架构应预留扩展点）
- 是否需要 `mcp_call` 的参数 schema 完整注入 system prompt？（当前只注名称+描述，LLM 可能需要 schema 才能正确构造参数——design phase 确认：首次调用前 LLM 可通过 `mcp_call` 传 `tool="__list__"` 获取工具详情）
