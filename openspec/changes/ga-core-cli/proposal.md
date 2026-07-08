## Why

GenericAgent 当前的 `ga_cli` 仅做子进程分发，`tuiapp_v2` 是单会话 TUI，缺乏 Galley 式「多会话编排 + 给 Supervisor Agent 的稳定 CLI 契约 + 工具时间线/审批」能力，也无法像 herdr 那样 multiplex 多种 agent（GA / opencode / claude-code / codex）。本变更构建 **Rust 单二进制 Core + CLI 契约层**作为基础 MVP：Core 是 **agent-agnostic** 编排内核——用可插拔 `Runner` trait 驱动任意 agent 子进程，拥有 session/project 状态、SQLite 持久化、通用流事件时间线、跨重启存活、跨平台；CLI 作为给 Supervisor Agent 的稳定公开契约（origin 三元组、JSON schema、退出码、审批/allowlist/YOLO）。它是后续 Goal 控制器与 TUI multiplexer 的共同基础。

## What Changes

- **NEW**：Rust 单二进制 Core daemon —— session/project 状态所有权、SQLite 编排层存储、**可插拔 `Runner` trait 驱动任意 agent**（GA / opencode / claude-code / codex）生命周期 + 通用流事件 IPC、事件时间线、跨重启持久化、跨平台（Linux/macOS/Windows）
- **NEW**：Runner adapter 注册表 —— GA 为一等 adapter（发射富结构 tool_call 事件），其他 agent 经通用 PTY/流 adapter 接入（output 行 / status blocked-working-done）
- **NEW**：稳定 Supervisor CLI 契约 —— Galley 式命令面（`status` / `sessions list` / `session new --runner=<agent>` / `session watch` / `session archive` / `project create|follow` / `llm set`），origin 三元组，JSON schema，退出码
- **NEW**：审批/治理子系统 —— 结构化事件 runner 支持工具级审批；流 runner 支持命令/面板级确认；allowlist / YOLO（Core 执行 + CLI 表面）
- **BREAKING**：新 `ga` CLI 替换现有 `ga_cli`（Python 子进程分发器）
- 复用：GA 的 `memory/` / `assets/` 由 GA runner adapter 只读写；不重写任何 agent 引擎、不重写 IM 前端

## Capabilities

### New Capabilities

- `ga-orchestration-core`: agent-agnostic Rust Core daemon —— session/project 状态机、SQLite、可插拔 `Runner` trait + adapter 注册表、通用流事件时间线、跨重启存活、跨平台进程管理
- `supervisor-cli`: 给 Supervisor Agent 的稳定 CLI 契约 —— 命令面（含 `--runner` 选择）、origin 三元组、JSON 输出 schema、退出码
- `approval-governance`: 审批 / allowlist / YOLO 治理 —— 结构化 runner 工具级 + 流 runner 命令级（Core 执行 + CLI 表面）

### Modified Capabilities

<!-- 无现有 spec 级需求变更（greenfield Rust 项目，现有 specs 为 mcp-client / skill-discovery，与本变更无交集） -->

## Impact

- 新增 Rust workspace（单二进制 `ga`），含 Core daemon + CLI + Runner trait + adapter（GA / generic-PTY）
- 新增 SQLite schema：sessions / projects / events / approvals / runner_kinds
- 新增通用流事件 IPC 协议（PTY/流 + 结构化事件适配）版本化
- 替换 `ga_cli/` 与 `ga` / `ga.cmd` 启动脚本
- 不影响任何 agent 引擎本体（GA Python 引擎 / opencode / claude-code / codex 均作为外部 runner 接入）
- 不影响 `frontends/` IM 前端（v1 保持独立）
- GA 的 `memory/` / `assets/` 由 GA runner adapter 只读写
