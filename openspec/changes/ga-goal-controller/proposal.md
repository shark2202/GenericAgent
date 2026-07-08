## Why

`ga-core-cli`（Item 1）提供了 session/project 编排 + Supervisor CLI 契约 + 审批，但缺少 Galley 的「Galley Goal」能力：交给 Core 一个长期目标、设定时长与 subagent 预算，让它在后台持续工作直到目标达成或预算耗尽。当前没有 goal 级别的状态机、后台执行控制器、预算管理或 deliverable 归集。本变项在 Core 之上构建 **Goal 控制器**，使 Supervisor Agent 与人类能以「目标」为单位派发与跟踪长周期工作。

## What Changes

- **NEW**：Goal 状态机 —— `proposed → confirmed → running → done | failed | budget-exhausted | timeout`，持久化于 SQLite `goals` 表
- **NEW**：Goal 生命周期 CLI —— `ga goal propose`（生成方案待确认）、`ga goal run --proposal=<id> --confirm-token=<token>`（显式确认后启动）、`ga goal status <id>`、`ga goal deliverable get <id>`
- **NEW**：subagent 预算 —— 限制单 goal 可并发的 runner 数；预算耗尽则停止派发新 runner 并标记 `budget-exhausted`
- **NEW**：时长控制 —— goal 可设最长运行时长，到期标记 `timeout` 并优雅收尾
- **NEW**：后台执行 —— goal 运行独立于发起客户端，跨重启可恢复（复用 Item 1 的 detach/reattach 与 SQLite 持久化）
- **NEW**：deliverable 归集 —— goal 产出物可检索（`goal deliverable get`）
- 复用：依赖 `ga-core-cli` 的 runner spawn、事件流、origin 三元组、审批治理；不重做 Core/CLI/TUI

## Capabilities

### New Capabilities

- `goal-controller`: 长期 Goal 控制器 —— goal 状态机、propose/confirm/run/status/deliverable、subagent 预算、时长控制、后台执行、`goals` 表、deliverable 归集

### Modified Capabilities

<!-- 无现有 main spec 级需求变更（ga-orchestration-core 尚未归档入 main specs；goal 状态由 goal-controller 拥有，扩展同一 SQLite） -->

## Impact

- 扩展 Core SQLite：新增 `goals` 表（id / state / proposal / budget / duration / supervisor / reason / created_at / finished_at）
- 新增 CLI 子命令组 `ga goal ...`（复用 Item 1 的 clap 框架、origin 三元组、JSON schema、退出码）
- 复用 Item 1：runner spawn（预算=限并发 runner 数）、事件流（goal 聚合其下 session 事件）、detach/reattach、审批治理
- 不影响 Item 1 的 session/project/CLI 契约本体（仅扩展）
- 不含 TUI 渲染（Item 3）
