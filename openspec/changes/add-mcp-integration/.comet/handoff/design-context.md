# Comet Design Handoff

- Change: add-mcp-integration
- Phase: design
- Mode: compact
- Context hash: 1f4fe759701352729283abe3b35928f2f8da8cf6391f30b20703249771ed9f2c

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/add-mcp-integration/proposal.md

- Source: openspec/changes/add-mcp-integration/proposal.md
- Lines: 1-30
- SHA256: 604fbd9bef1b52754538974b3b8567a2b7cf91b1d4ab920a3474535bf653aef8

```md
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

```

## openspec/changes/add-mcp-integration/design.md

- Source: openspec/changes/add-mcp-integration/design.md
- Lines: 1-105
- SHA256: ff5b9138747d581a11ccea339f2b9e0c274e9b3c9e4aa2b31bcae882302ac765

[TRUNCATED]

```md
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


```

Full source: openspec/changes/add-mcp-integration/design.md

## openspec/changes/add-mcp-integration/tasks.md

- Source: openspec/changes/add-mcp-integration/tasks.md
- Lines: 1-78
- SHA256: a484cbebd014c6a4e41d25e688906e5b9ff68f161a8c649cbe6acecd4872506a

```md
## 1. 基础设施搭建

- [ ] 1.1 创建 `mcp_client.py` 模块骨架：`MCPManager` 单例类 + `MCPServerConnection` 类（抽象连接生命周期）
- [ ] 1.2 添加 `mcp` SDK 依赖到 `requirements.txt` 或声明依赖文件
- [ ] 1.3 编写 MCPManager 单元测试骨架（TDD：先写测试确认接口设计）

## 2. 配置加载与解析

- [ ] 2.1 实现 `.ga/mcp_servers.json` 配置加载：解析 JSON，验证每个 Server 的必填字段（`name`, `transport`）
- [ ] 2.2 编写配置加载测试（正常格式、文件不存在、JSON 无效）

## 3. stdio Transport

- [ ] 3.1 实现 `StdioConnection`：子进程启动、JSON-RPC 握手（`initialize`）、`notifications/initialized`
- [ ] 3.2 编写 stdio 连接测试（启动成功、命令不存在、进程意外退出）

## 4. SSE Transport

- [ ] 4.1 实现 `SSEConnection`：SSE 连接建立、JSON-RPC 消息收发
- [ ] 4.2 编写 SSE 连接测试（连接成功、连接超时）

## 5. 工具发现与 mcp_call

- [ ] 5.1 实现 `tools/list` 调用与缓存：连接成功后自动发现工具列表
- [ ] 5.2 在 `ga.py` 中新增 `do_mcp_call` 方法（委托到 `MCPManager.call_tool()`）
- [ ] 5.3 在 `tools_schema.json` 中新增 `mcp_call` 工具定义
- [ ] 5.4 编写 `do_mcp_call` 测试（成功调用、不存在的 Server、不存在的工具）

## 6. 健康监控与自动重连

- [ ] 6.1 实现心跳检测：对每个已连接 Server 定期调用 `ping`，记录失败次数
- [ ] 6.2 实现自动重连：失败阈值触发 → 按间隔重试 → 达到上限标记 `failed`
- [ ] 6.3 编写健康监控测试（心跳正常、失败触发重连、重连成功、重连耗尽）
- [ ] 6.4 实现 `atexit` 清理所有子进程

## 7. 热更新

- [ ] 7.1 实现配置文件 mtime 轮询：每 2 秒检查一次
- [ ] 7.2 实现增量重载逻辑：新增 Server 连接、移除的断开、变更的重连
- [ ] 7.3 实现热更新锁：防止并发重载
- [ ] 7.4 编写热更新测试（新增 Server、移除 Server、JSON 格式错误时保留现有连接）

## 8. GA CLI 集成

- [ ] 8.1 在 `ga_cli/cli.py` 中新增 `mcp` 子命令族：`list`、`start`、`stop`、`restart`
- [ ] 8.2 编写 CLI 命令测试

## 9. Agent 启动集成

- [ ] 9.1 在 `agentmain.py` 的 `GenericAgent.__init__()` 或 `run()` 中初始化 `MCPManager`
- [ ] 9.2 确保热更新轮询在 Agent 生命周期内持续运行

## 10. 记忆融合

- [ ] 10.1 修改 `skill_loader.py` 的 `get_skill_catalog()`：拼接 MCP 工具列表（`<server>/<tool>` 格式，标注 `[MCP]`）
- [ ] 10.2 确保 MCP 工具在 `get_system_prompt()` 中与 Skill 索引同级展示
- [ ] 10.3 `mcp_call` 结果通过 `plugins/hooks.py` 的 `tool_after` 事件触发 L2 记忆评估
- [ ] 10.4 修改 Skill 结晶逻辑：检测任务历史中的 `mcp_call`，自动生成 `mcp_dependencies` frontmatter
- [ ] 10.5 编写记忆融合测试

## 11. 调用审计

- [ ] 11.1 实现审计日志记录：时间戳、Server、工具名、参数摘要、结果摘要、耗时、成功/失败
- [ ] 11.2 审计日志写入 `temp/mcp_audit.jsonl`
- [ ] 11.3 编写审计日志测试

## 12. 集成测试与验收

- [ ] 12.1 端到端测试：配置 stdio Server → Agent 启动 → `mcp_call` 成功调用 → 审计日志可查
- [ ] 12.2 端到端测试：配置 SSE Server → 连接成功 → 工具发现 → 调用返回
- [ ] 12.3 端到端测试：热更新 → 增减 Server → system prompt 反映变化
- [ ] 12.4 端到端测试：Server 挂断 → 自动重连 → 恢复可用
- [ ] 12.5 TDD 完整性检查：所有测试通过，覆盖率 ≥ 80%

## 13. 文档

- [ ] 13.1 更新 `MAP.md`：新增 `mcp_client.py` 条目
- [ ] 13.2 在 `memory/` 下创建 `mcp_sop.md`：MCP Client 使用说明

```

## openspec/changes/add-mcp-integration/specs/mcp-client/spec.md

- Source: openspec/changes/add-mcp-integration/specs/mcp-client/spec.md
- Lines: 1-196
- SHA256: 1d66ddbb0483745edb526267100f58b80c4ed82f2a260a3912a6816477796898

[TRUNCATED]

```md
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


```

Full source: openspec/changes/add-mcp-integration/specs/mcp-client/spec.md

## openspec/changes/add-mcp-integration/specs/skill-discovery/spec.md

- Source: openspec/changes/add-mcp-integration/specs/skill-discovery/spec.md
- Lines: 1-105
- SHA256: 16b2810593977d112a9306b24155fa607efeb9ab84367c1e615cf2d2b149405d

[TRUNCATED]

```md
# skill-discovery Delta Spec

## MODIFIED Requirements

### Requirement: Skill Catalog Discovery

The system SHALL scan `$HOME/.agents/skills/` and `$CWD/.agents/skills/` for directories containing a `SKILL.md` file, parse their YAML frontmatter, and build a catalog of available skills. Additionally, the system SHALL include MCP Server tools in the catalog when MCP Client is active.

#### Scenario: Skills directory exists with valid skills

- **WHEN** `$HOME/.agents/skills/` contains directories with `SKILL.md` files that have valid YAML frontmatter with `name` field
- **THEN** each valid skill is included in the catalog with its name, description, and absolute path

#### Scenario: Skills directory does not exist

- **WHEN** `$HOME/.agents/skills/` or `$CWD/.agents/skills/` does not exist
- **THEN** the directory is silently skipped, no error is raised

#### Scenario: SKILL.md without valid frontmatter

- **WHEN** a `SKILL.md` file has no YAML frontmatter or no `name` field
- **THEN** the skill is silently excluded from the catalog

#### Scenario: Non-SKILL.md files in skill directories

- **WHEN** a skill directory contains files other than `SKILL.md`
- **THEN** those files are ignored during catalog construction

#### Scenario: Directory name differs from frontmatter name

- **WHEN** a skill directory is named `foo` but its `SKILL.md` frontmatter has `name: bar`
- **THEN** the catalog SHALL use `bar` as the skill identifier, and the directory name `foo` SHALL only be used for conflict resolution (project-over-user override)

#### Scenario: MCP tools included when MCP Client is active

- **WHEN** MCP Client is initialized and at least one MCP Server is connected with tools
- **THEN** each MCP tool is included in the catalog as `<server>/<tool>`, with description from the tool's schema, and source marked as `mcp`

#### Scenario: MCP tools excluded when no MCP Client

- **WHEN** no MCP Client is initialized or no MCP servers are connected
- **THEN** the catalog does not include any MCP entries

### Requirement: Skill Catalog Injection into System Prompt

The system SHALL append the skill catalog to the system prompt in a compact format, listing each skill's name, description, and file path. When MCP Client is active, the system SHALL also include available MCP Server tools in the same section, distinguished by source label.

#### Scenario: Catalog with multiple skills

- **WHEN** 3 skills are discovered
- **THEN** the system prompt ends with a section listing all 3 skills, each on one line: `- **name**: description (path/to/SKILL.md)`

#### Scenario: Catalog with no skills

- **WHEN** no valid skills are found in any scan path
- **THEN** no skill catalog section is appended to the system prompt

#### Scenario: Catalog includes MCP tools alongside skills

- **WHEN** 2 skills are discovered and 1 MCP Server is connected with 2 tools
- **THEN** the system prompt section lists all 2 skills first, then the 2 MCP tools, each with source label `[MCP]`

### Requirement: Long-Term Memory from MCP Calls

The system SHALL allow `mcp_call` results that contain information worth persisting to trigger the existing `start_long_term_update` mechanism (L2 memory distillation). The agent MAY recognize MCP-call-derived knowledge and initiate memory updates.

#### Scenario: MCP call returns environment facts

- **WHEN** an `mcp_call` returns information about persistent configuration, user preferences, or environment state
- **THEN** the agent MAY initiate `start_long_term_update` to persist the knowledge to L2 memory

#### Scenario: MCP call returns ephemeral data

- **WHEN** an `mcp_call` returns transient query results with no lasting value
- **THEN** no L2 memory update is triggered

### Requirement: Self-Evolution Skill Crystallization with MCP Dependencies

When the system crystallizes a completed task into a Skill (L3), and that task involved MCP tool calls, the generated Skill SHALL annotate the MCP dependencies (which Server and which tools were used).


```

Full source: openspec/changes/add-mcp-integration/specs/skill-discovery/spec.md
