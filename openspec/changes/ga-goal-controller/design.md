## Context

`ga-core-cli`（Item 1）提供 agent-agnostic Core（`Runner` trait + session/project 状态 + SQLite + 事件时间线 + 审批 + Supervisor CLI + 分发 P2）。Galley 的「Galley Goal」证明需要 goal 级长周期编排：交一个目标、设时长 + subagent 预算、后台跑到完成或预算耗尽。当前无 goal 状态机、后台控制器、预算管理或 deliverable 归集。本变更在 Core 之上加 **Goal 控制器**，不重做 Core。

约束：依赖 Item 1（未归档，但 API 契约已定）；不含 TUI；不含 socket API / 插件；goal 状态入同一 SQLite。

## Goals / Non-Goals

**Goals**：goal 状态机 + propose/confirm/run/status/deliverable CLI + subagent 预算 + 时长 + 后台执行 + 跨重启恢复 + deliverable 归集。

**Non-Goals**：TUI 渲染（Item 3）；socket API / 插件；重做 Core / CLI；多 goal 间依赖编排（DAG，后续变更）。

## Decisions

### D1: Goal 状态机 = proposed → confirmed → running → 终态

状态：`proposed → confirmed → running → done | failed | budget-exhausted | timeout`。`propose` 生成方案（含计划 / 预算 / 时长）仅 record 不执行；`confirm` 需显式 confirm-token；`run` 启动后台控制器；终态持久化于 `goals` 表。

- 为何：对齐 Galley「propose → confirm → run」；显式确认是长周期 / 预算消耗的安全门。
- 替代：propose 即 run —— 预算消耗无确认、风险高，否决。

### D2: subagent 预算 = 限并发 runner 数

goal 声明 `max_concurrent_runners`；控制器派发新 runner 前检查活跃数 < 预算，否则排队；无法再派发且无活跃 runner → `budget-exhausted`。

- 为何：预算 = 资源上限，简单可控；复用 Item 1 runner spawn。
- Open Question：预算是「并发数」还是「累计 runner 总数 / token / 时长」？v1 取并发数，累计维度后续。

### D3: 时长控制 = deadline timer

goal 声明 `max_duration`；控制器跟踪已运行时长，到期 → `timeout`，优雅停活跃 runner（复用 Item 1 `RunnerSupervisor.terminate`）。

- 为何：防止失控长跑。

### D4: 后台执行 + 跨重启恢复

goal 控制器跑在 Core daemon 内（非客户端进程）；客户端断开后 goal 继续；Core 重启后从 SQLite 恢复 `running` 的 goal（重拉控制器，重连其下 session runner 或按策略重启）。

- 为何：复用 Item 1 detach/reattach + SQLite；goal 是 daemon 级任务。

### D5: deliverable = goal 聚合产出

goal 的 deliverable = 其下所有 session 终答 + 关键事件摘要，存于 `goal_deliverables` 表或从事件流聚合；`ga goal deliverable get <id>` 返回。

- 为何：Galley `goal deliverable get` 同款。
- Open Question：deliverable 格式（纯文本摘要 vs 结构化 JSON）。

### D6: confirm-token 机制

`propose` 返回一次性、限时的内部 confirm-token；`run` 必须带该 token 才启动。

- 为何：防重放 / 误启；对齐 Galley。

## Risks / Trade-offs

- [goal 控制器崩溃] → 状态在 SQLite，daemon 重启后恢复；控制器逻辑幂等
- [预算语义歧义] → v1 用并发数，文档明确
- [跨重启恢复时 runner 已死] → 恢复策略：标记 session 为 interrupted，按 goal 策略重试或失败
- [deliverable 格式] → v1 纯文本摘要 + 可选 JSON

## Open Questions

- 预算维度（并发 vs 累计 / token / 时长）
- deliverable 格式
- goal 与 session / project 的归属（goal 持有一组 session？goal 属于某 project？）
- 多 goal 依赖 / DAG（明确非目标，后续）
